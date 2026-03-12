"""
CLAUDE-BRAIN API v2 — FastAPI Application Factory

Flujo de una petición:
  1. POST /v1/chat → Watcher.observe() inicia observación
  2. SmartRouter.route() → elige agent/skills/command automáticamente
  3. Mem0Manager.build_context() → recupera memoria relevante (3 capas)
  4. ComponentRegistry.build_prompt() → system + user prompt
  5. ClaudeMaxRunner.run() → subprocess claude CLI (Max OAuth, $0 extra)
  6. Mem0Manager.remember() → extrae memorias (async)
  7. Watcher persiste → Supabase + Redis
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.api.routers import (
    chat,
    agent_loop,
    registry_routes,
    memory_routes,
    git_routes,
    jupyter_routes,
    system,
)
from agent.api.routers.jupyter_routes import startup as jupyter_startup, shutdown as jupyter_shutdown


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle hooks — reemplaza on_event deprecated."""
    await jupyter_startup()
    yield
    await jupyter_shutdown()


app = FastAPI(
    title="CLAUDE-BRAIN API v2",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Incluir routers ──────────────────────────────────────
app.include_router(chat.router)
app.include_router(agent_loop.router)
app.include_router(registry_routes.router)
app.include_router(memory_routes.router)
app.include_router(git_routes.router)
app.include_router(jupyter_routes.router)
app.include_router(system.router)
