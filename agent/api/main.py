"""
CLAUDE-BRAIN API — FastAPI REST + WebSocket

Endpoints:
  POST /v1/chat          → Chat con agente (Max OAuth, $0 extra)
  GET  /v1/chat/stream   → Streaming SSE
  WS   /ws/{session_id}  → WebSocket bidireccional
  GET  /v1/skills        → Listar skills
  POST /v1/skills/activate → Activar skill
  GET  /v1/memory/search → Búsqueda semántica
  POST /v1/execute       → Ejecutar código en sandbox
  GET  /v1/status        → Health check
"""

import asyncio
import json
import os
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.core.claude_runner import ClaudeMaxRunner
from agent.memory.memory_manager import MemoryManager
from agent.orchestrator.multi_agent import MultiAgentOrchestrator
from agent.skills.skill_manager import SkillManager

# ─────────────────────────────────────────────
# INICIALIZACIÓN
# ─────────────────────────────────────────────

app = FastAPI(
    title="CLAUDE-BRAIN API",
    description="Autonomous AI Agent powered by Claude Code Max (Zero extra API billing)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Componentes del agente
runner = ClaudeMaxRunner()
memory = MemoryManager()
skills = SkillManager()
orchestrator = MultiAgentOrchestrator(
    runner=runner,
    max_concurrent=int(os.getenv("AGENT_MAX_SUBAGENTS", "4")),
)

SANDBOX_URL = "http://sandbox-api:8080"

# ─────────────────────────────────────────────
# MODELOS PYDANTIC
# ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., description="Mensaje o tarea para el agente")
    session_id: str = Field(default="default", description="ID de sesión para memoria")
    skill_names: list[str] = Field(default=[], description="Skills a activar")
    use_memory: bool = Field(default=True, description="Usar memoria persistente")
    use_multiagent: bool = Field(default=False, description="Permitir subagentes paralelos")
    cwd: Optional[str] = Field(default=None, description="Directorio de trabajo")
    tools: Optional[list[str]] = Field(default=None, description="Tools específicas a usar")

class ChatResponse(BaseModel):
    response: str
    session_id: str
    billing: str = "Max OAuth (no API billing)"

class ExecuteRequest(BaseModel):
    code: str = Field(..., max_length=50000)
    language: str = Field(default="python", pattern="^(python|javascript|bash)$")
    timeout: int = Field(default=30, ge=1, le=60)

class SkillActivateRequest(BaseModel):
    name: str

# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Chat principal con el agente.
    Usa Claude Code Max OAuth → sin billing adicional.
    """
    # 1. Construir contexto de memoria
    memory_ctx = ""
    if req.use_memory:
        memory_ctx = await memory.build_context(req.session_id, req.message)

    # 2. Construir prompt con skills y memoria
    task = skills.build_task_prompt(
        user_task=req.message,
        skill_names=req.skill_names,
        memory_context=memory_ctx,
    )

    # 3. Obtener tools (built-in + skills activas)
    agent_tools = req.tools or (runner.BUILTIN_TOOLS + skills.get_active_tools())

    # 4. Ejecutar
    if req.use_multiagent:
        response = await orchestrator.orchestrate(task)
    else:
        result = await runner.run_with_tools(
            task=task,
            tools=list(set(agent_tools)),
            cwd=req.cwd,
            system=skills.get_system_prompt(),
        )
        response = result.output

    # 5. Guardar en memoria (async, no bloquea)
    if req.use_memory:
        memory.add_message(req.session_id, "user", req.message)
        memory.add_message(req.session_id, "assistant", response[:2000])
        asyncio.create_task(memory.extract_and_save(req.session_id, runner))

    return ChatResponse(response=response, session_id=req.session_id)


@app.get("/v1/chat/stream")
async def chat_stream(
    message: str,
    session_id: str = "default",
    cwd: Optional[str] = None,
):
    """Streaming SSE — tokens en tiempo real."""
    async def generate():
        async for token in runner.stream(message, cwd=cwd):
            yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket bidireccional para chat en tiempo real."""
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive_text()
            await websocket.send_json({"type": "start"})

            async for token in runner.stream(message):
                await websocket.send_json({"type": "token", "data": token})

            memory.add_message(session_id, "user", message)
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_json({"type": "error", "data": str(e)})
        await websocket.close()


@app.get("/v1/skills")
def list_skills():
    """Lista todas las skills disponibles."""
    return {"skills": skills.list_skills()}


@app.post("/v1/skills/activate")
def activate_skill(req: SkillActivateRequest):
    """Activa una skill."""
    skill = skills.activate(req.name)
    if not skill:
        return {"error": f"Skill '{req.name}' no encontrada"}, 404
    return {"activated": req.name, "tools_required": skill.tools_required}


@app.post("/v1/skills/reload")
def reload_skills():
    """Recarga todas las skills desde disco."""
    skills.reload()
    return {"skills_loaded": len(skills._registry)}


@app.get("/v1/memory/search")
async def search_memory(
    query: str,
    memory_type: Optional[str] = None,
    limit: int = 5,
):
    """Búsqueda semántica en la memoria persistente."""
    results = await memory.search_memory(query, memory_type=memory_type, limit=limit)
    return {"results": results, "count": len(results)}


@app.post("/v1/execute")
async def execute_code(req: ExecuteRequest):
    """
    Ejecuta código en sandbox aislado (snekbox/nsjail).
    Python, JavaScript, Bash con restricciones de seguridad.
    """
    async with httpx.AsyncClient(timeout=req.timeout + 10) as client:
        try:
            resp = await client.post(
                f"{SANDBOX_URL}/execute",
                json={
                    "code": req.code,
                    "language": req.language,
                    "timeout": req.timeout,
                },
            )
            return resp.json()
        except httpx.ConnectError:
            return {"error": "Sandbox no disponible", "exit_code": 1}


@app.get("/v1/status")
async def status():
    """Health check completo del sistema."""
    # Test Claude CLI con Max OAuth
    test = await runner.run("responde solo: OK", timeout=30)

    return {
        "status": "healthy" if test.success else "degraded",
        "components": {
            "claude_cli": {
                "ok": test.success,
                "billing": "Max OAuth (no API billing)",
                "note": "claude --print sin ANTHROPIC_API_KEY",
            },
            "redis": _check_redis(),
            "supabase": _check_supabase(),
            "embeddings": await _check_embeddings(),
        },
        "version": "1.0.0",
    }


def _check_redis() -> dict:
    try:
        memory.redis.ping()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _check_supabase() -> dict:
    try:
        memory.supabase.table("agent_memories").select("id").limit(1).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _check_embeddings() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{memory.embed_url}/health")
            return {"ok": resp.status_code == 200, "model": "nomic-embed-text-v1.5"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
