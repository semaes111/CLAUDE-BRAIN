"""
Watcher — Observador transversal del sistema

El Watcher es un middleware que envuelve TODAS las operaciones del agente.
Nada pasa sin que el Watcher lo vea, registre y analice.

Responsabilidades:
  1. Registro en Supabase → tabla `interactions` (audit trail completo)
  2. Métricas en Redis → latencia, tokens estimados, tasas de error
  3. Pub/Sub de eventos → otros servicios pueden suscribirse (n8n, Telegram)
  4. Rate limit monitor → alerta antes de alcanzar límites
  5. Error aggregator → detecta patrones de errores
  6. Component usage stats → qué agentes/skills se usan más

Patrón de uso:
    async with watcher.observe(session_id, task) as ctx:
        result = await runner.run(ctx.prompt)
        ctx.set_result(result)
    # → Al salir del context manager, guarda automáticamente en Supabase
"""

import asyncio
import hashlib
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

import redis
from supabase import create_client, Client


# ─────────────────────────────────────────────────────────
# EVENTO DE INTERACCIÓN
# ─────────────────────────────────────────────────────────

@dataclass
class InteractionEvent:
    interaction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    task_preview: str = ""          # Primeros 200 chars de la tarea
    task_hash: str = ""             # SHA256 de la tarea completa
    agent_used: Optional[str] = None
    skills_used: list = field(default_factory=list)
    command_used: Optional[str] = None
    routing_reasoning: str = ""
    routing_confidence: float = 0.0
    response_preview: str = ""      # Primeros 500 chars de la respuesta
    success: bool = True
    error: Optional[str] = None
    latency_ms: int = 0
    tokens_estimated: int = 0       # Estimación: chars/4
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0

    def finalize(self, response: str, success: bool, error: str = ""):
        self.ended_at = time.time()
        self.latency_ms = int((self.ended_at - self.started_at) * 1000)
        self.response_preview = response[:500] if response else ""
        self.tokens_estimated = len(response) // 4
        self.success = success
        self.error = error[:500] if error else None

    def to_dict(self) -> dict:
        return {
            "id": self.interaction_id,
            "session_id": self.session_id,
            "task_preview": self.task_preview,
            "task_hash": self.task_hash,
            "agent_used": self.agent_used,
            "skills_used": self.skills_used,
            "command_used": self.command_used,
            "routing_reasoning": self.routing_reasoning,
            "routing_confidence": self.routing_confidence,
            "response_preview": self.response_preview,
            "success": self.success,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "tokens_estimated": self.tokens_estimated,
        }


# ─────────────────────────────────────────────────────────
# CONTEXT DEL OBSERVE
# ─────────────────────────────────────────────────────────

class ObserveContext:
    """Context object pasado al código bajo observación."""

    def __init__(self, event: InteractionEvent):
        self.event = event
        self._response = ""
        self._success = True
        self._error = ""

    def set_routing(self, agent: str | None, skills: list, command: str | None,
                    reasoning: str, confidence: float):
        self.event.agent_used = agent
        self.event.skills_used = skills or []
        self.event.command_used = command
        self.event.routing_reasoning = reasoning
        self.event.routing_confidence = confidence

    def set_result(self, response: str, success: bool = True, error: str = ""):
        self._response = response
        self._success = success
        self._error = error


# ─────────────────────────────────────────────────────────
# WATCHER PRINCIPAL
# ─────────────────────────────────────────────────────────

class Watcher:
    """
    Observador transversal. Envuelve todas las operaciones del agente.
    
    Registra en:
      - Supabase: audit trail completo (tabla interactions)
      - Redis: métricas calientes (contadores, latencias)
      - Redis Pub/Sub: eventos en tiempo real para n8n/Telegram
    """

    METRICS_KEY = "watcher:metrics"
    ERRORS_KEY  = "watcher:errors"
    RATE_KEY    = "watcher:rate:{window}"

    def __init__(self):
        self._redis = redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379"),
            decode_responses=True
        )
        self._supabase: Client = create_client(
            url=os.getenv("SUPABASE_URL", "http://supabase-kong:8000"),
            key=os.getenv("SUPABASE_SERVICE_KEY", ""),
        )
        self._active: dict[str, InteractionEvent] = {}

    # ─────────────────────────────────────────────
    # CONTEXT MANAGER PRINCIPAL
    # ─────────────────────────────────────────────

    @asynccontextmanager
    async def observe(self, session_id: str, task: str):
        """
        Context manager principal. Todo el código dentro queda observado.

        Uso:
            async with watcher.observe(session_id, task) as ctx:
                ctx.set_routing(agent, skills, command, reasoning, confidence)
                result = await runner.run(...)
                ctx.set_result(result.output, result.success)
        """
        event = InteractionEvent(
            session_id=session_id,
            task_preview=task[:200],
            task_hash=hashlib.sha256(task.encode()).hexdigest()[:16],
        )
        ctx = ObserveContext(event)
        self._active[event.interaction_id] = event

        # Publicar evento de inicio
        self._publish("interaction.started", {
            "id": event.interaction_id,
            "session_id": session_id,
            "task_preview": event.task_preview,
        })

        try:
            yield ctx
            event.finalize(ctx._response, ctx._success, ctx._error)
        except Exception as e:
            event.finalize("", False, str(e))
            raise
        finally:
            self._active.pop(event.interaction_id, None)
            # Guardar y publicar de forma asíncrona (no bloquea la respuesta)
            asyncio.create_task(self._persist(event))
            asyncio.create_task(self._update_metrics(event))
            self._publish("interaction.completed", {
                "id": event.interaction_id,
                "success": event.success,
                "latency_ms": event.latency_ms,
                "agent": event.agent_used,
                "skills": event.skills_used,
            })

    # ─────────────────────────────────────────────
    # PERSISTENCIA — Supabase
    # ─────────────────────────────────────────────

    async def _persist(self, event: InteractionEvent):
        """Guarda la interacción en Supabase (async, no bloquea)."""
        try:
            self._supabase.table("interactions").insert(event.to_dict()).execute()
        except Exception as e:
            # Nunca fallar silenciosamente en producción
            print(f"[Watcher] Error persistiendo en Supabase: {e}")

    # ─────────────────────────────────────────────
    # MÉTRICAS — Redis
    # ─────────────────────────────────────────────

    async def _update_metrics(self, event: InteractionEvent):
        """Actualiza métricas calientes en Redis."""
        try:
            pipe = self._redis.pipeline()

            # Contadores globales
            pipe.hincrby(self.METRICS_KEY, "total_requests", 1)
            if event.success:
                pipe.hincrby(self.METRICS_KEY, "success", 1)
            else:
                pipe.hincrby(self.METRICS_KEY, "errors", 1)
                pipe.lpush(self.ERRORS_KEY, f"{event.error}|{event.session_id}")
                pipe.ltrim(self.ERRORS_KEY, 0, 99)  # Últimos 100 errores

            # Latencia (media móvil simple)
            pipe.lpush("watcher:latencies", event.latency_ms)
            pipe.ltrim("watcher:latencies", 0, 999)

            # Tokens estimados (totales)
            pipe.hincrby(self.METRICS_KEY, "tokens_estimated", event.tokens_estimated)

            # Agente más usado
            if event.agent_used:
                pipe.hincrby(f"watcher:agent_usage", event.agent_used, 1)

            # Rate por ventana de 1 minuto (para detectar si vamos a alcanzar rate limit)
            window = int(time.time() // 60)
            rate_key = self.RATE_KEY.format(window=window)
            pipe.incr(rate_key)
            pipe.expire(rate_key, 120)  # TTL de 2 minutos

            pipe.execute()
        except Exception as e:
            print(f"[Watcher] Error actualizando métricas Redis: {e}")

    # ─────────────────────────────────────────────
    # PUB/SUB — Eventos en tiempo real
    # ─────────────────────────────────────────────

    def _publish(self, event_type: str, data: dict):
        """Publica evento en Redis Pub/Sub para n8n y otros consumidores."""
        import json
        try:
            self._redis.publish(
                f"claude-brain:{event_type}",
                json.dumps({"type": event_type, "data": data})
            )
        except Exception:
            pass  # Pub/sub es best-effort

    # ─────────────────────────────────────────────
    # CONSULTAS DE MÉTRICAS
    # ─────────────────────────────────────────────

    def get_metrics(self) -> dict:
        """Métricas del sistema para health check y dashboard."""
        try:
            base = self._redis.hgetall(self.METRICS_KEY) or {}
            total = int(base.get("total_requests", 0))
            errors = int(base.get("errors", 0))
            tokens = int(base.get("tokens_estimated", 0))

            # Latencia media de las últimas 100 peticiones
            latencies = self._redis.lrange("watcher:latencies", 0, 99)
            avg_latency = (
                int(sum(int(l) for l in latencies) / len(latencies))
                if latencies else 0
            )

            # Rate actual (peticiones en el minuto actual)
            window = int(time.time() // 60)
            current_rate = int(self._redis.get(self.RATE_KEY.format(window=window)) or 0)

            # Top agentes usados
            agent_usage = self._redis.hgetall("watcher:agent_usage") or {}
            top_agents = sorted(agent_usage.items(), key=lambda x: int(x[1]), reverse=True)[:5]

            return {
                "total_requests": total,
                "success_rate": round((total - errors) / max(total, 1) * 100, 1),
                "error_count": errors,
                "avg_latency_ms": avg_latency,
                "current_rate_per_min": current_rate,
                "tokens_estimated_total": tokens,
                "active_requests": len(self._active),
                "top_agents": dict(top_agents),
            }
        except Exception as e:
            return {"error": str(e)}

    def get_recent_errors(self, n: int = 10) -> list[str]:
        """Últimos N errores registrados."""
        try:
            return self._redis.lrange(self.ERRORS_KEY, 0, n - 1) or []
        except Exception:
            return []

    async def get_history(self, session_id: str, limit: int = 20) -> list[dict]:
        """Historial de interacciones de una sesión desde Supabase."""
        try:
            result = (
                self._supabase.table("interactions")
                .select("id,task_preview,agent_used,skills_used,success,latency_ms,tokens_estimated")
                .eq("session_id", session_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception:
            return []
