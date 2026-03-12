"""
Mem0Manager — Sistema de memoria de 3 capas

Capa 0 → Redis            Working memory (conversación activa, TTL 24h)
Capa 1 → mem0 + pgvector  Memoria semántica/episódica de largo plazo
Capa 2 → Supabase tables  Memoria procedimental estructurada (facts, preferences)

mem0ai (https://github.com/mem0ai/mem0):
  - Extrae automáticamente memorias importantes de las conversaciones
  - Detecta duplicados y los actualiza en lugar de duplicar
  - Multi-tenant: separa memorias por user_id y session_id
  - Compatible con pgvector (nuestra instancia Supabase self-hosted)

Embeddings: nomic-embed-text-v1.5 vía HTTP (HuggingFace TGI local, $0)
LLM extracción: ClaudeMaxRunner subprocess ($0 extra, usa Max OAuth)
"""

import json
import os
from dataclasses import dataclass

import httpx
import redis

from mem0 import Memory

from agent.config import settings
from agent.core.veracity import should_persist_memory


def build_mem0_config() -> dict:
    """
    Configura mem0 con nuestros backends self-hosted.
    Sin OpenAI, sin billing adicional.
    """
    return {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host":        "supabase-db",
                "port":        5432,
                "dbname":      "postgres",
                "user":        "postgres",
                "password":    settings.postgres_password,
                "collection_name": "mem0_memories",
                "embedding_model_dims": 768,
            }
        },
        "embedder": {
            "provider": "huggingface",
            "config": {
                "model": "nomic-ai/nomic-embed-text-v1.5",
            }
        },
        "llm": {
            "provider": "anthropic",
            "config": {
                "model": "claude-haiku-4-5-20251001",
                "api_key": settings.anthropic_api_key,
            }
        } if settings.anthropic_api_key else {
            "provider": "openai",
            "config": {
                "model": "gpt-4o-mini",
                "api_key": "not-used",
                "openai_base_url": "http://agent-api:8000/v1/openai-compat",
            }
        },
        "version": "v1.1",
    }


class Mem0Manager:
    """
    Sistema de memoria unificado.
    Expone una API simple independientemente de los backends.
    """

    def __init__(self):
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)
        self._embed_url = settings.embedding_url
        self._mem0: Memory | None = None
        self._mem0_ready = False
        self._init_mem0()

    def _init_mem0(self):
        """Inicializa mem0 de forma tolerante a fallos."""
        try:
            config = build_mem0_config()
            self._mem0 = Memory.from_config(config)
            self._mem0_ready = True
        except Exception as e:
            print(f"[Mem0] Inicialización fallida: {e} — usando fallback Redis+pgvector")
            self._mem0_ready = False

    # ─────────────────────────────────────────────
    # CAPA 0: REDIS — Working Memory
    # ─────────────────────────────────────────────

    def add_message(self, session_id: str, role: str, content: str):
        """Añade mensaje al historial activo."""
        key = f"conv:{session_id}"
        self._redis.rpush(key, json.dumps({"role": role, "content": content[:4000]}))
        self._redis.ltrim(key, -50, -1)
        self._redis.expire(key, 86400)  # TTL 24h

    def get_history(self, session_id: str, n: int = 20) -> list[dict]:
        """Historial reciente de la conversación."""
        raw = self._redis.lrange(f"conv:{session_id}", -n, -1)
        return [json.loads(m) for m in raw]

    def set_session_context(self, session_id: str, key: str, value):
        """Guarda metadata de sesión (task actual, archivos, etc.)."""
        self._redis.hset(f"ctx:{session_id}", key, json.dumps(value))
        self._redis.expire(f"ctx:{session_id}", 86400)

    def get_session_context(self, session_id: str) -> dict:
        raw = self._redis.hgetall(f"ctx:{session_id}")
        return {k: json.loads(v) for k, v in raw.items()}

    # ─────────────────────────────────────────────
    # CAPA 1: MEM0 — Memoria Semántica/Episódica
    # ─────────────────────────────────────────────

    async def remember(
        self,
        messages: list[dict],
        user_id: str,
        session_id: str,
        session_success: bool = True,  # resultado del AgenticLoop o chat
    ) -> list[dict]:
        """
        Extrae y guarda memorias importantes de una conversación.
        Aplica MemoryGuard (veracity.py) antes de persistir:
          - Descarta memorias con markers de incertidumbre
          - Descarta datos técnicos específicos de sesiones fallidas
          - Descarta memorias demasiado vagas (< 20 chars)

        mem0 detecta: preferencias, hechos, procedimientos, errores pasados.
        Deduplica automáticamente.
        """
        if not self._mem0_ready:
            return []
        try:
            # mem0 extrae las memorias candidatas del historial
            result = self._mem0.add(
                messages=messages,
                user_id=user_id,
                metadata={"session_id": session_id}
            )
            raw_memories = result.get("results", [])

            # ── MemoryGuard: filtrar antes de que queden persistidas ──
            # NOTA: mem0 ya las guardó en este punto si no las podemos interceptar
            # antes del add(). El guard actúa borrando las que no pasan el filtro.
            filtered_out = []
            for mem in raw_memories:
                mem_text = mem.get("memory", "")
                should_keep, reason = should_persist_memory(mem_text, session_success)
                if not should_keep:
                    # Borrar inmediatamente la memoria que no pasó el filtro
                    mem_id = mem.get("id")
                    if mem_id and self._mem0_ready:
                        try:
                            self._mem0.delete(memory_id=mem_id)
                            filtered_out.append((mem_text[:60], reason))
                        except Exception:
                            pass

            if filtered_out:
                print(f"[MemoryGuard] {len(filtered_out)} memorias eliminadas:")
                for text, reason in filtered_out:
                    print(f"  ✗ '{text}' → {reason}")

            # Retornar solo las que pasaron el filtro
            passed = [m for m in raw_memories if m.get("id") not in
                      {mem.get("id") for mem, _ in [(m, r) for m, r in filtered_out]}]
            return passed

        except Exception as e:
            print(f"[Mem0] Error en remember: {e}")
            return []

    async def recall(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
    ) -> list[dict]:
        """
        Busca memorias relevantes para la query actual.
        Combina búsqueda semántica (pgvector) con filtros de usuario.
        """
        if not self._mem0_ready:
            return await self._fallback_search(query, limit)
        try:
            result = self._mem0.search(
                query=query,
                user_id=user_id,
                limit=limit,
            )
            return result.get("results", [])
        except Exception as e:
            print(f"[Mem0] Error en recall: {e}")
            return await self._fallback_search(query, limit)

    async def get_all_memories(self, user_id: str) -> list[dict]:
        """Todas las memorias de un usuario."""
        if not self._mem0_ready:
            return []
        try:
            result = self._mem0.get_all(user_id=user_id)
            return result.get("results", [])
        except Exception:
            return []

    async def delete_memory(self, memory_id: str):
        """Elimina una memoria específica."""
        if self._mem0_ready:
            try:
                self._mem0.delete(memory_id=memory_id)
            except Exception:
                pass

    async def reset_user(self, user_id: str):
        """Elimina TODAS las memorias de un usuario."""
        if self._mem0_ready:
            try:
                self._mem0.delete_all(user_id=user_id)
            except Exception:
                pass

    # ─────────────────────────────────────────────
    # CAPA 2: SUPABASE — Memoria Procedimental
    # Facts estáticos, preferencias del usuario, proyectos
    # ─────────────────────────────────────────────

    def save_fact(self, user_id: str, category: str, content: str):
        """
        Guarda en Redis como facts estructurados.
        Estos son datos que el usuario declaró explícitamente.
        """
        key = f"facts:{user_id}:{category}"
        self._redis.set(key, content, ex=2592000)  # 30 días

    def get_facts(self, user_id: str) -> dict:
        """Todos los facts de un usuario."""
        pattern = f"facts:{user_id}:*"
        facts = {}
        for key in self._redis.scan_iter(pattern):
            category = key.split(":")[-1]
            facts[category] = self._redis.get(key)
        return facts

    # ─────────────────────────────────────────────
    # FALLBACK — Embeddings locales directamente
    # Si mem0 no está disponible, búsqueda directa en pgvector
    # ─────────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._embed_url}/embed",
                json={"inputs": text, "normalize": True}
            )
            data = resp.json()
            return data[0] if isinstance(data[0], list) else data

    async def _fallback_search(self, query: str, limit: int) -> list[dict]:
        """Búsqueda directa en agent_memories sin mem0."""
        from supabase import create_client
        try:
            sb = create_client(settings.supabase_url, settings.supabase_service_key)
            embedding = await self._embed(query)
            result = sb.rpc("match_memories", {
                "query_embedding": embedding,
                "match_threshold": 0.65,
                "match_count": limit * 2,
                "filter_type": None,
            }).execute()
            return [{"memory": r["content"], "score": r["similarity"]} for r in result.data[:limit]]
        except Exception:
            return []

    # ─────────────────────────────────────────────
    # CONTEXT BUILDER — Para inyectar en prompts
    # ─────────────────────────────────────────────

    async def build_context(self, user_id: str, session_id: str, query: str) -> str:
        """
        Construye el bloque de contexto completo para inyectar en el prompt.
        Combina las 3 capas de memoria.
        """
        parts = []

        # Capa 0: Contexto de sesión activa
        ctx = self.get_session_context(session_id)
        if ctx:
            parts.append(
                f"<session_context>\n{json.dumps(ctx, ensure_ascii=False)}\n</session_context>"
            )

        # Capa 1: Memorias semánticas relevantes (mem0)
        try:
            memories = await self.recall(query, user_id=user_id, limit=5)
            if memories:
                mem_lines = []
                for m in memories:
                    text = m.get("memory", m.get("content", ""))
                    score = round(float(m.get("score", 0)) * 100)
                    mem_lines.append(f"  [{score}%] {text}")
                parts.append(
                    f"<relevant_memories>\n" + "\n".join(mem_lines) + "\n</relevant_memories>"
                )
        except Exception:
            pass

        # Capa 2: Facts declarativos del usuario
        facts = self.get_facts(user_id)
        if facts:
            facts_text = "\n".join(f"  {k}: {v}" for k, v in facts.items())
            parts.append(f"<user_facts>\n{facts_text}\n</user_facts>")

        return "\n\n".join(parts)
