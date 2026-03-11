"""
MemoryManager — Sistema de memoria persistente de 3 capas

Capa 1: Redis       → Working memory (conversación activa, TTL 24h)
Capa 2: pgvector    → Long-term memory (búsqueda semántica)
Capa 3: Claude CLI  → Extracción automática de memorias importantes ($0 extra)

Embeddings: nomic-embed-text-v1.5 LOCAL (HuggingFace TGI)
            768 dimensiones, 8192 tokens de contexto, ~270MB RAM
            Sin llamadas a OpenAI API → $0/mes adicional
"""

import asyncio
import json
import os
import uuid
from datetime import timedelta
from typing import Optional

import httpx
import redis
from supabase import create_client, Client


class MemoryManager:
    MEMORY_TYPES = ("episodic", "semantic", "procedural")
    DEFAULT_TTL = timedelta(hours=24)
    MAX_HISTORY = 50  # Últimos N mensajes en Redis

    def __init__(self):
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.supabase: Client = create_client(
            url=os.getenv("SUPABASE_URL", "http://supabase-kong:8000"),
            key=os.getenv("SUPABASE_SERVICE_KEY", ""),
        )
        self.embed_url = os.getenv("EMBEDDING_URL", "http://embeddings:8080")
        self._ttl_seconds = int(self.DEFAULT_TTL.total_seconds())

    # ─────────────────────────────────────────────
    # EMBEDDINGS — nomic-embed-text local
    # ─────────────────────────────────────────────

    async def embed(self, text: str) -> list[float]:
        """
        Genera embedding con nomic-embed-text-v1.5 LOCAL.
        HuggingFace Text Embeddings Inference (Docker).
        Sin OpenAI API → $0 adicional.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.embed_url}/embed",
                json={"inputs": text, "normalize": True},
            )
            resp.raise_for_status()
            data = resp.json()
            # TEI devuelve lista de vectores (uno por input)
            return data[0] if isinstance(data[0], list) else data

    # ─────────────────────────────────────────────
    # CAPA 1: REDIS — Working Memory
    # ─────────────────────────────────────────────

    def add_message(self, session_id: str, role: str, content: str):
        """Añade mensaje al historial de conversación."""
        key = f"conv:{session_id}"
        self.redis.rpush(key, json.dumps({"role": role, "content": content[:4000]}))
        self.redis.ltrim(key, -self.MAX_HISTORY, -1)
        self.redis.expire(key, self._ttl_seconds)

    def get_history(self, session_id: str, n: int = 20) -> list[dict]:
        """Recupera los últimos N mensajes."""
        raw = self.redis.lrange(f"conv:{session_id}", -n, -1)
        return [json.loads(m) for m in raw]

    def set_context(self, session_id: str, key: str, value):
        """Guarda contexto de sesión (task actual, archivos abiertos, etc.)."""
        self.redis.hset(f"ctx:{session_id}", key, json.dumps(value))
        self.redis.expire(f"ctx:{session_id}", self._ttl_seconds)

    def get_context(self, session_id: str) -> dict:
        """Recupera contexto completo de la sesión."""
        raw = self.redis.hgetall(f"ctx:{session_id}")
        return {k: json.loads(v) for k, v in raw.items()}

    def clear_session(self, session_id: str):
        """Limpia la sesión de Redis."""
        self.redis.delete(f"conv:{session_id}", f"ctx:{session_id}")

    # ─────────────────────────────────────────────
    # CAPA 2: PGVECTOR — Long-term Memory
    # ─────────────────────────────────────────────

    async def save_memory(
        self,
        content: str,
        memory_type: str = "episodic",
        metadata: Optional[dict] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Guarda una memoria en Supabase pgvector con embedding local.
        Retorna el ID generado.
        """
        if memory_type not in self.MEMORY_TYPES:
            memory_type = "episodic"

        embedding = await self.embed(content)
        mem_id = str(uuid.uuid4())

        self.supabase.table("agent_memories").insert({
            "id": mem_id,
            "content": content,
            "memory_type": memory_type,
            "embedding": embedding,
            "metadata": metadata or {},
            "session_id": session_id,
        }).execute()

        return mem_id

    async def search_memory(
        self,
        query: str,
        memory_type: Optional[str] = None,
        limit: int = 5,
        threshold: float = 0.65,
    ) -> list[dict]:
        """Búsqueda semántica por similitud coseno en pgvector."""
        embedding = await self.embed(query)
        result = self.supabase.rpc("match_memories", {
            "query_embedding": embedding,
            "match_threshold": threshold,
            "match_count": limit * 4,  # Over-retrieve para mejor calidad
            "filter_type": memory_type,
        }).execute()
        return result.data[:limit]

    async def delete_memory(self, memory_id: str):
        """Elimina una memoria específica."""
        self.supabase.table("agent_memories").delete().eq("id", memory_id).execute()

    # ─────────────────────────────────────────────
    # CAPA 3: EXTRACCIÓN AUTOMÁTICA
    # Usa Claude CLI (Max OAuth, $0 extra)
    # ─────────────────────────────────────────────

    async def extract_and_save(self, session_id: str, runner) -> list[str]:
        """
        Usa Claude CLI para extraer memorias importantes de la conversación.
        Se ejecuta de forma asíncrona sin bloquear el flujo principal.
        """
        history = self.get_history(session_id, n=30)
        if len(history) < 4:
            return []

        conversation_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:500]}" for m in history[-10:]
        )

        result = await runner.run(
            f"""Analiza esta conversación y extrae 3-5 memorias importantes.
Responde SOLO con JSON array (sin markdown, sin explicaciones):
[{{"content": "...", "type": "episodic|semantic|procedural"}}]

Tipos:
- episodic: eventos específicos que ocurrieron
- semantic: conocimiento o hechos aprendidos
- procedural: cómo hacer algo, pasos o métodos

CONVERSACIÓN:
{conversation_text}
""",
            timeout=60,
        )

        saved_ids = []
        try:
            # Limpiar posible markdown del output
            clean = result.output.strip()
            if "```" in clean:
                clean = clean.split("```")[1].lstrip("json").strip()

            memories = json.loads(clean)
            for mem in memories:
                if isinstance(mem, dict) and "content" in mem:
                    mem_id = await self.save_memory(
                        content=mem["content"],
                        memory_type=mem.get("type", "episodic"),
                        session_id=session_id,
                    )
                    saved_ids.append(mem_id)
        except (json.JSONDecodeError, Exception):
            pass  # No bloquear el flujo si falla la extracción

        return saved_ids

    # ─────────────────────────────────────────────
    # CONTEXTO — Para inyectar en prompts
    # ─────────────────────────────────────────────

    async def build_context(self, session_id: str, query: str) -> str:
        """
        Construye el bloque de contexto de memoria para inyectar en el prompt.
        Combina working memory (Redis) + long-term (pgvector).
        """
        parts = []

        # Contexto de sesión activa
        ctx = self.get_context(session_id)
        if ctx:
            parts.append(f"<contexto_sesion>\n{json.dumps(ctx, ensure_ascii=False)}\n</contexto_sesion>")

        # Memorias semánticas relevantes
        try:
            memories = await self.search_memory(query, limit=5)
            if memories:
                mem_text = "\n".join(f"  [{m['memory_type']}] {m['content']}" for m in memories)
                parts.append(f"<memorias_relevantes>\n{mem_text}\n</memorias_relevantes>")
        except Exception:
            pass  # Continuar sin memoria si hay error de DB

        return "\n\n".join(parts)
