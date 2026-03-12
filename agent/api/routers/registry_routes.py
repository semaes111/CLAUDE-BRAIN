"""Router del ComponentRegistry — /v1/registry endpoints."""

from fastapi import APIRouter

from agent.api.deps import registry, router_ai

router = APIRouter(prefix="/v1/registry", tags=["registry"])


@router.get("")
def get_registry():
    return {"summary": registry.summary(), "catalog": registry.catalog()}


@router.get("/agents")
def list_agents():
    return {
        "agents": [
            {"name": a.name, "description": a.description, "category": a.category}
            for a in registry.agents.values()
        ]
    }


@router.get("/skills")
def list_skills():
    return {
        "skills": [
            {"name": s.name, "description": s.description, "source": s.source}
            for s in registry.skills.values()
        ]
    }


@router.get("/commands")
def list_commands():
    return {
        "commands": [
            {"name": c.name, "description": c.description, "argument_hint": c.argument_hint}
            for c in registry.commands.values()
        ]
    }


@router.post("/reload")
def reload_registry():
    registry.reload()
    return {"reloaded": registry.summary()}


@router.post("/route")
async def route_task(message: str):
    d = await router_ai.route(message)
    return {
        "agent": d.agent, "skills": d.skills, "command": d.command,
        "reasoning": d.reasoning, "confidence": d.confidence,
    }
