"""Router de chat — /v1/chat endpoints."""

import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.api.deps import runner, registry, router_ai, watcher, memory, orchestrator

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatRequest(BaseModel):
    message:        str
    session_id:     str       = "default"
    user_id:        str       = "default"
    agent_name:     str | None = None
    skill_names:    list[str] = []
    command_name:   str | None = None
    command_args:   str       = ""
    auto_route:     bool      = True
    use_memory:     bool      = True
    use_multiagent: bool      = False
    cwd:            str | None = None


@router.post("/chat")
async def chat(req: ChatRequest):
    t0 = time.time()
    async with watcher.observe(req.session_id, req.message) as ctx:
        # Routing
        if req.auto_route and not req.agent_name and not req.skill_names:
            d = await router_ai.route(req.message)
            agent_name, skill_names = d.agent, d.skills
            command_name, command_args, reasoning = d.command, d.command_args, d.reasoning
        else:
            agent_name, skill_names = req.agent_name, req.skill_names
            command_name, command_args, reasoning = req.command_name, req.command_args, "manual"

        ctx.set_routing(agent_name, skill_names, command_name, reasoning, 1.0)

        # Memoria
        memory_ctx = ""
        if req.use_memory:
            memory_ctx = await memory.build_context(req.user_id, req.session_id, req.message)

        # Prompt
        system_prompt, user_prompt, tools = registry.build_prompt(
            task=req.message, agent_name=agent_name, skill_names=skill_names,
            command_name=command_name, command_args=command_args, memory_context=memory_ctx,
        )

        # Ejecutar
        if req.use_multiagent:
            response, success = await orchestrator.orchestrate(user_prompt), True
        else:
            result = await runner.run(
                task=user_prompt, system=system_prompt,
                allowed_tools=tools, cwd=req.cwd,
            )
            response, success = result.output, result.success

        ctx.set_result(response, success)

        # Memoria async
        if req.use_memory:
            memory.add_message(req.session_id, "user", req.message)
            memory.add_message(req.session_id, "assistant", response[:2000])
            history = memory.get_history(req.session_id, n=10)
            asyncio.create_task(
                memory.remember(history, user_id=req.user_id, session_id=req.session_id)
            )

    return {
        "response": response, "session_id": req.session_id, "user_id": req.user_id,
        "agent_used": agent_name, "skills_used": skill_names or [],
        "routing_reasoning": reasoning, "latency_ms": int((time.time() - t0) * 1000),
        "billing": "Max OAuth ($0 extra)",
    }


@router.get("/chat/stream")
async def chat_stream(message: str, session_id: str = "default"):
    async def gen():
        async for token in runner.stream(message):
            yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
