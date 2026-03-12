"""Router de memoria — /v1/memory endpoints."""

from fastapi import APIRouter

from agent.api.deps import memory

router = APIRouter(prefix="/v1/memory", tags=["memory"])


@router.get("/search")
async def search_memory(query: str, user_id: str = "default", limit: int = 5):
    return {"results": await memory.recall(query, user_id=user_id, limit=limit)}


@router.get("/all")
async def get_all_memory(user_id: str = "default"):
    return {"memories": await memory.get_all_memories(user_id=user_id)}


@router.delete("/user/{user_id}")
async def delete_user_memory(user_id: str):
    await memory.reset_user(user_id)
    return {"deleted": user_id}


@router.post("/fact")
def save_fact(user_id: str, category: str, content: str):
    memory.save_fact(user_id, category, content)
    return {"saved": True}
