"""Router de sistema — status, watcher, execute, OpenAI-compat."""

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agent.api.deps import runner, registry, memory, watcher
from agent.config import settings

router = APIRouter(tags=["system"])


# ── Execute sandbox ──────────────────────────────────────

class ExecuteRequest(BaseModel):
    code:     str = Field(..., max_length=50000)
    language: str = Field(default="python", pattern="^(python|javascript|bash)$")
    timeout:  int = Field(default=30, ge=1, le=60)


@router.post("/v1/execute")
async def execute_code(req: ExecuteRequest):
    async with httpx.AsyncClient(timeout=req.timeout + 10) as client:
        try:
            resp = await client.post(f"{settings.sandbox_url}/execute", json=req.model_dump())
            return resp.json()
        except httpx.ConnectError:
            return {"error": "Sandbox no disponible", "exit_code": 1}


# ── Watcher metrics ──────────────────────────────────────

@router.get("/v1/watcher/metrics")
def get_metrics():
    return watcher.get_metrics()


@router.get("/v1/watcher/history/{session_id}")
async def get_history(session_id: str, limit: int = 20):
    return {"history": await watcher.get_history(session_id, limit)}


# ── OpenAI-compatible (para mem0 extractor) ──────────────

@router.post("/v1/openai-compat/chat/completions")
async def openai_compat(request: dict):
    messages = request.get("messages", [])
    prompt = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    result = await runner.run(prompt, timeout=60)
    return {
        "choices": [{"message": {"role": "assistant", "content": result.output}, "finish_reason": "stop"}],
        "model": "claude-code-max",
    }


# ── WebSocket ────────────────────────────────────────────

@router.websocket("/ws/{session_id}")
async def ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_text()
            await websocket.send_json({"type": "start"})
            async for token in runner.stream(msg):
                await websocket.send_json({"type": "token", "data": token})
            await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass


# ── Status ───────────────────────────────────────────────

@router.get("/v1/status")
async def status():
    test = await runner.run("OK", timeout=30)
    return {
        "status": "healthy" if test.success else "degraded",
        "version": "2.0.0",
        "components": {
            "claude_cli": {"ok": test.success, "billing": "Max OAuth ($0 extra)"},
            "registry": registry.summary(),
            "memory": {"mem0_ready": memory._mem0_ready},
            "watcher": watcher.get_metrics(),
        },
    }
