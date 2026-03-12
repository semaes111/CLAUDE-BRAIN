"""Router de Jupyter Kernel — /v1/jupyter endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent.core.jupyter_kernel import JupyterKernelManager

router = APIRouter(prefix="/v1/jupyter", tags=["jupyter"])

jupyter_manager = JupyterKernelManager()


async def startup():
    await jupyter_manager.start()


async def shutdown():
    await jupyter_manager.stop()


class CellRequest(BaseModel):
    code:       str = Field(..., min_length=1, max_length=100_000)
    session_id: str = "default"
    timeout:    int = Field(default=120, ge=1, le=600)


@router.post("/execute")
async def jupyter_execute(req: CellRequest):
    """Ejecuta una celda Python en el kernel de la sesión."""
    result = await jupyter_manager.execute(
        session_id=req.session_id, code=req.code, timeout=req.timeout,
    )
    return {
        "text":        result.text,
        "images":      result.images,
        "error":       result.error,
        "exec_count":  result.exec_count,
        "success":     result.success,
        "duration_ms": result.duration_ms,
        "has_images":  len(result.images) > 0,
    }


@router.post("/restart/{session_id}")
async def jupyter_restart(session_id: str):
    ok = await jupyter_manager.restart(session_id)
    return {"restarted": ok, "session_id": session_id}


@router.delete("/kernel/{session_id}")
async def jupyter_kill(session_id: str):
    await jupyter_manager.kill(session_id)
    return {"killed": session_id}


@router.get("/kernels")
async def jupyter_list():
    return {
        "kernels":   await jupyter_manager.list_kernels(),
        "available": await jupyter_manager.is_available(),
    }


@router.get("/status")
async def jupyter_status():
    available = await jupyter_manager.is_available()
    kernels   = await jupyter_manager.list_kernels()
    return {
        "available":      available,
        "active_kernels": len(kernels),
        "kernels":        kernels,
        "jupyter_url":    jupyter_manager.JUPYTER_URL,
    }
