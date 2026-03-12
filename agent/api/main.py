"""
CLAUDE-BRAIN API v2 — FastAPI

Flujo de una petición:
  1. POST /v1/chat → Watcher.observe() inicia observación
  2. SmartRouter.route() → elige agent/skills/command automáticamente
  3. Mem0Manager.build_context() → recupera memoria relevante (3 capas)
  4. ComponentRegistry.build_prompt() → system + user prompt
  5. ClaudeMaxRunner.run() → subprocess claude CLI (Max OAuth, $0 extra)
  6. Mem0Manager.remember() → extrae memorias (async)
  7. Watcher persiste → Supabase + Redis
"""

import asyncio, json, os, time
from typing import Optional
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.core.claude_runner import ClaudeMaxRunner
from agent.core.router import SmartRouter
from agent.core.watcher import Watcher
from agent.memory.mem0_manager import Mem0Manager
from agent.orchestrator.multi_agent import MultiAgentOrchestrator
from agent.registry.component_registry import ComponentRegistry

app = FastAPI(title="CLAUDE-BRAIN API v2", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

runner       = ClaudeMaxRunner()
registry     = ComponentRegistry()
router_ai    = SmartRouter(runner=runner, registry=registry)
watcher      = Watcher()
memory       = Mem0Manager()
orchestrator = MultiAgentOrchestrator(runner=runner, max_concurrent=int(os.getenv("AGENT_MAX_SUBAGENTS","4")))
SANDBOX_URL  = os.getenv("SANDBOX_URL", "http://sandbox-api:8080")

class ChatRequest(BaseModel):
    message:        str
    session_id:     str       = "default"
    user_id:        str       = "default"
    agent_name:     Optional[str]   = None
    skill_names:    list[str]       = []
    command_name:   Optional[str]   = None
    command_args:   str             = ""
    auto_route:     bool = True
    use_memory:     bool = True
    use_multiagent: bool = False
    cwd:            Optional[str]   = None

class ExecuteRequest(BaseModel):
    code:     str = Field(..., max_length=50000)
    language: str = Field(default="python", pattern="^(python|javascript|bash)$")
    timeout:  int = Field(default=30, ge=1, le=60)

@app.post("/v1/chat")
async def chat(req: ChatRequest):
    t0 = time.time()
    async with watcher.observe(req.session_id, req.message) as ctx:
        # Routing automático o manual
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
            result = await runner.run(task=user_prompt, system=system_prompt, allowed_tools=tools, cwd=req.cwd)
            response, success = result.output, result.success

        ctx.set_result(response, success)

        # Memoria async
        if req.use_memory:
            memory.add_message(req.session_id, "user", req.message)
            memory.add_message(req.session_id, "assistant", response[:2000])
            history = memory.get_history(req.session_id, n=10)
            asyncio.create_task(memory.remember(history, user_id=req.user_id, session_id=req.session_id))

    return {
        "response": response, "session_id": req.session_id, "user_id": req.user_id,
        "agent_used": agent_name, "skills_used": skill_names or [],
        "routing_reasoning": reasoning, "latency_ms": int((time.time()-t0)*1000),
        "billing": "Max OAuth ($0 extra)"
    }

@app.get("/v1/chat/stream")
async def chat_stream(message: str, session_id: str = "default"):
    async def gen():
        async for token in runner.stream(message):
            yield f"data: {json.dumps({'type':'token','data':token})}\n\n"
        yield f"data: {json.dumps({'type':'done'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.websocket("/ws/{session_id}")
async def ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_text()
            await websocket.send_json({"type":"start"})
            async for token in runner.stream(msg):
                await websocket.send_json({"type":"token","data":token})
            await websocket.send_json({"type":"done"})
    except WebSocketDisconnect:
        pass

@app.get("/v1/registry")
def get_registry():
    return {"summary": registry.summary(), "catalog": registry.catalog()}

@app.get("/v1/registry/agents")
def list_agents():
    return {"agents": [{"name":a.name,"description":a.description,"category":a.category} for a in registry.agents.values()]}

@app.get("/v1/registry/skills")
def list_skills():
    return {"skills": [{"name":s.name,"description":s.description,"source":s.source} for s in registry.skills.values()]}

@app.get("/v1/registry/commands")
def list_commands():
    return {"commands": [{"name":c.name,"description":c.description,"argument_hint":c.argument_hint} for c in registry.commands.values()]}

@app.post("/v1/registry/reload")
def reload_registry():
    registry.reload()
    return {"reloaded": registry.summary()}

@app.post("/v1/route")
async def route_task(message: str):
    d = await router_ai.route(message)
    return {"agent": d.agent, "skills": d.skills, "command": d.command,
            "reasoning": d.reasoning, "confidence": d.confidence}

@app.get("/v1/memory/search")
async def search_memory(query: str, user_id: str = "default", limit: int = 5):
    return {"results": await memory.recall(query, user_id=user_id, limit=limit)}

@app.get("/v1/memory/all")
async def get_all_memory(user_id: str = "default"):
    return {"memories": await memory.get_all_memories(user_id=user_id)}

@app.delete("/v1/memory/user/{user_id}")
async def delete_user_memory(user_id: str):
    await memory.reset_user(user_id)
    return {"deleted": user_id}

@app.post("/v1/memory/fact")
def save_fact(user_id: str, category: str, content: str):
    memory.save_fact(user_id, category, content)
    return {"saved": True}

@app.get("/v1/watcher/metrics")
def get_metrics():
    return watcher.get_metrics()

@app.get("/v1/watcher/history/{session_id}")
async def get_history(session_id: str, limit: int = 20):
    return {"history": await watcher.get_history(session_id, limit)}

@app.post("/v1/execute")
async def execute_code(req: ExecuteRequest):
    async with httpx.AsyncClient(timeout=req.timeout + 10) as client:
        try:
            resp = await client.post(f"{SANDBOX_URL}/execute", json=req.dict())
            return resp.json()
        except httpx.ConnectError:
            return {"error": "Sandbox no disponible", "exit_code": 1}

# Endpoint OpenAI-compatible para que mem0 use el CLI como LLM extractor
@app.post("/v1/openai-compat/chat/completions")
async def openai_compat(request: dict):
    messages = request.get("messages", [])
    prompt = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    result = await runner.run(prompt, timeout=60)
    return {"choices": [{"message": {"role":"assistant","content": result.output}, "finish_reason":"stop"}],
            "model": "claude-code-max"}

@app.get("/v1/status")
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
        }
    }

# ─────────────────────────────────────────────────────────
# AGENTIC LOOP ENDPOINTS
# ─────────────────────────────────────────────────────────

from agent.core.agentic_loop import AgenticLoop
from agent.core.runtime_executor import RuntimeExecutor

_runtime = RuntimeExecutor(
    base_dir="/workspaces",
    enable_browser=True,
    runner=runner,
)

class AgentRunRequest(BaseModel):
    task:           str
    session_id:     str        = "default"
    max_iterations: int        = Field(default=30, ge=1, le=50)
    cwd:            str        = "/workspaces"
    confirm_mode:   bool       = False
    agent_name:     Optional[str] = None  # fuerza un agente especialista

@app.post("/v1/agent/run")
async def agent_run(req: AgentRunRequest):
    """
    Ejecuta el AgenticLoop completo (multi-turno).
    El agente puede ejecutar bash, leer/escribir archivos, navegar web.
    Retorna cuando llega a finish/reject o max_iterations.
    """
    # Obtener system prompt de agente especialista si se especifica
    extra_system = ""
    if req.agent_name and (agent := registry.get_agent(req.agent_name)):
        extra_system = agent.system_prompt

    loop = AgenticLoop(
        runner=runner,
        runtime=_runtime,
        max_iterations=req.max_iterations,
        confirm_mode=req.confirm_mode,
    )

    result = await loop.run(
        task=req.task,
        session_id=req.session_id,
        cwd=req.cwd,
        extra_system=extra_system,
    )

    return {
        "success":    result.success,
        "message":    result.message,
        "iterations": result.iterations,
        "stuck":      result.stuck,
        "steps": [
            {
                "i":          s.iteration,
                "action":     s.action.type.value,
                "thought":    s.action.thought[:200],
                "payload":    str(s.action.payload)[:300],
                "obs_ok":     s.observation.success,
                "obs":        s.observation.content[:400],
            }
            for s in result.steps
        ],
    }

@app.get("/v1/agent/run/stream")
async def agent_run_stream(
    task:           str,
    session_id:     str = "default",
    max_iterations: int = 30,
    cwd:            str = "/workspaces",
):
    """Streaming del AgenticLoop — SSE con cada step en tiempo real."""
    loop = AgenticLoop(
        runner=runner,
        runtime=_runtime,
        max_iterations=max_iterations,
    )

    async def generate():
        async for event in loop.stream(task, session_id, cwd):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────
# GIT WORKFLOW ENDPOINTS
# ─────────────────────────────────────────────────────────

from agent.core.git_workflow import GitWorkflow

_git = GitWorkflow()

class IssueRequest(BaseModel):
    repo:       str  # owner/repo
    issue_num:  int
    session_id: str = "default"

@app.post("/v1/git/solve-issue")
async def solve_github_issue(req: IssueRequest):
    """
    Resuelve un GitHub Issue de forma autónoma.
    Clona el repo, analiza el issue, aplica cambios, hace commit.
    """
    # Clonar
    ctx = await _git.clone_repo(f"https://github.com/{req.repo}.git")
    branch_name = f"fix/issue-{req.issue_num}"
    await _git.create_branch(branch_name)

    # Construir contexto
    issue_context = await _git.build_issue_context(req.repo, req.issue_num)

    # Ejecutar loop
    loop = AgenticLoop(
        runner=runner, runtime=_runtime, max_iterations=25,
    )
    result = await loop.run(
        task=issue_context,
        session_id=req.session_id,
        cwd=str(ctx.work_dir),
    )

    response = {"success": result.success, "message": result.message, "iterations": result.iterations}

    # Si terminó con éxito, hacer commit
    if result.success:
        ok, out = await _git.commit_changes(f"fix: resolve issue #{req.issue_num}\n\n{result.message[:500]}")
        response["commit"] = {"ok": ok, "output": out}

    return response

@app.get("/v1/git/diff")
async def git_diff():
    """Diff actual del workspace."""
    return {"diff": await _git.get_diff()}

@app.get("/v1/git/status")
async def git_status():
    return {"status": await _git.get_status()}
