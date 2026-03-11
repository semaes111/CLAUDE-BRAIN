"""
Sandbox API — Ejecución segura de código

Python: snekbox (nsjail) — sandbox ultra-seguro
JS/Bash: Docker con gVisor (--runtime=runsc) o restricciones estándar

Restricciones de seguridad:
  --network=none          Sin acceso a red
  --read-only             Filesystem de solo lectura
  --memory=256m           Límite de RAM
  --cpus=0.5              Límite de CPU
  --pids-limit=64         Sin fork bombs
  --cap-drop=ALL          Sin privilegios
  --user=nobody           Usuario sin permisos
  --tmpfs /tmp:size=50m   Temp limitado y no ejecutable
"""

import asyncio
import os
from typing import Literal

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="CLAUDE-BRAIN Sandbox Executor")

SNEKBOX_URL = os.getenv("SNEKBOX_URL", "http://snekbox:8060")
MAX_OUTPUT = 100_000  # 100KB máximo de output


class CodeRequest(BaseModel):
    code: str = Field(..., max_length=50_000)
    language: Literal["python", "javascript", "bash"] = "python"
    timeout: int = Field(default=30, ge=1, le=60)


class ExecuteResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


@app.post("/execute", response_model=ExecuteResult)
async def execute(req: CodeRequest) -> ExecuteResult:
    if req.language == "python":
        return await _execute_python(req)
    else:
        return await _execute_docker(req)


async def _execute_python(req: CodeRequest) -> ExecuteResult:
    """Python via snekbox (nsjail) — el sandbox más seguro disponible."""
    async with httpx.AsyncClient(timeout=req.timeout + 10) as client:
        try:
            resp = await client.post(
                f"{SNEKBOX_URL}/eval",
                json={"input": req.code},
            )
            data = resp.json()
            stdout = (data.get("stdout") or "")[:MAX_OUTPUT]
            returncode = data.get("returncode", 1)
            return ExecuteResult(
                stdout=stdout,
                stderr="",
                exit_code=returncode,
                timed_out=returncode == 137,  # SIGKILL = timeout en snekbox
            )
        except httpx.ConnectError:
            return ExecuteResult(stdout="", stderr="snekbox no disponible", exit_code=1)


async def _execute_docker(req: CodeRequest) -> ExecuteResult:
    """JavaScript/Bash via Docker con restricciones de seguridad."""
    images = {
        "javascript": ("node:20-slim", ["node", "-e", req.code]),
        "bash": ("alpine:3.19", ["sh", "-c", req.code]),
    }
    image, cmd_parts = images[req.language]

    docker_cmd = [
        "docker", "run", "--rm",
        "--network=none",
        "--read-only",
        "--memory=256m", "--memory-swap=256m",
        "--cpus=0.5",
        "--pids-limit=64",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--tmpfs=/tmp:size=50m,noexec,nosuid",
        "--user=nobody",
        image,
        *cmd_parts,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=req.timeout
        )
        return ExecuteResult(
            stdout=stdout_b.decode("utf-8", errors="replace")[:MAX_OUTPUT],
            stderr=stderr_b.decode("utf-8", errors="replace")[:MAX_OUTPUT],
            exit_code=proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return ExecuteResult(stdout="", stderr="Timeout", exit_code=124, timed_out=True)
    except Exception as e:
        return ExecuteResult(stdout="", stderr=str(e), exit_code=1)


@app.get("/health")
def health():
    return {"status": "ok", "sandbox": "snekbox+docker"}
