"""Router del AgenticLoop — /v1/agent endpoints."""

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.api.deps import runner, registry, runtime
from agent.core.agentic_loop import AgenticLoop

router = APIRouter(prefix="/v1/agent", tags=["agent"])


class AgentRunRequest(BaseModel):
    task:           str
    session_id:     str       = "default"
    max_iterations: int       = Field(default=30, ge=1, le=50)
    cwd:            str       = "/workspaces"
    confirm_mode:   bool      = False
    agent_name:     str | None = None


@router.post("/run")
async def agent_run(req: AgentRunRequest):
    """Ejecuta el AgenticLoop completo (multi-turno)."""
    extra_system = ""
    if req.agent_name and (agent := registry.get_agent(req.agent_name)):
        extra_system = agent.system_prompt

    loop = AgenticLoop(
        runner=runner, runtime=runtime,
        max_iterations=req.max_iterations, confirm_mode=req.confirm_mode,
    )

    result = await loop.run(
        task=req.task, session_id=req.session_id,
        cwd=req.cwd, extra_system=extra_system,
    )

    return {
        "success": result.success, "message": result.message,
        "iterations": result.iterations, "stuck": result.stuck,
        "steps": [
            {
                "i": s.iteration, "action": s.action.type.value,
                "thought": s.action.thought[:200],
                "payload": str(s.action.payload)[:300],
                "obs_ok": s.observation.success,
                "obs": s.observation.content[:400],
            }
            for s in result.steps
        ],
    }


@router.get("/run/stream")
async def agent_run_stream(
    task: str, session_id: str = "default",
    max_iterations: int = 30, cwd: str = "/workspaces",
):
    """Streaming del AgenticLoop — SSE con cada step en tiempo real."""
    loop = AgenticLoop(runner=runner, runtime=runtime, max_iterations=max_iterations)

    async def generate():
        async for event in loop.stream(task, session_id, cwd):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
