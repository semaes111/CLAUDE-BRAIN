"""
ClaudeMaxRunner — Motor central de CLAUDE-BRAIN

Invoca el CLI `claude --print` vía subprocess usando Max OAuth.
NUNCA usa ANTHROPIC_API_KEY → usa subscription Max, sin billing extra.

Billing: $0 adicional (incluido en Claude Code Max plan)
"""

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from agent.config import settings
from agent.core.veracity import VERSION_COMPACT


@dataclass
class RunResult:
    output: str
    exit_code: int
    success: bool
    error: str = ""
    tool_calls: list = field(default_factory=list)


class ClaudeMaxRunner:
    """
    Motor central del agente. Invoca claude CLI usando Max OAuth.

    Principio de funcionamiento:
    - Sin ANTHROPIC_API_KEY en el entorno → CLI usa OAuth del Max plan
    - Con ANTHROPIC_API_KEY → CLI usa API billing (cobrado por token)
    - NUNCA exportar ANTHROPIC_API_KEY en los contenedores del agente
    """

    # Tools built-in del CLI (no tienen costo adicional)
    BUILTIN_TOOLS = [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
        "WebSearch", "WebFetch", "TodoRead", "TodoWrite",
    ]

    def __init__(self, workdir: str | None = None):
        self.workdir = Path(workdir or settings.workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.timeout = settings.agent_timeout_seconds
        self._verify_claude_binary()

    def _verify_claude_binary(self):
        if not shutil.which("claude"):
            raise RuntimeError(
                "claude CLI no encontrado.\n"
                "Instalar: npm install -g @anthropic-ai/claude-code\n"
                "Autenticar: claude auth login"
            )

    def _build_system(self, extra_system: str | None = None) -> str:
        """
        Construye el system prompt COMPLETO para cada llamada al CLI.

        Siempre prefija VERSION_COMPACT para garantizar control epistémico
        en TODAS las llamadas: AgenticLoop, Router, Condenser, multi_agent.

        Estructura:
          [VERSION_COMPACT]        ← normas epistémicas mínimas (siempre)
          [extra_system]           ← contexto específico de la llamada (si lo hay)
        """
        parts = [VERSION_COMPACT.strip()]
        if extra_system and extra_system.strip():
            parts.append(extra_system.strip())
        return "\n\n".join(parts)

    def _build_env(self) -> dict:
        """
        Construye entorno SIN API key para forzar OAuth Max.
        Esta es la clave del sistema — sin esta distinción, pagarías por token.
        """
        env = {
            "HOME": os.environ.get("HOME", "/root"),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin"),
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_CONFIG_DIR": settings.claude_config_dir,
        }
        # CRÍTICO: NO incluir ANTHROPIC_API_KEY
        # Si existe en el entorno padre, NO la propagamos
        return env

    async def run(
        self,
        task: str,
        cwd: str | None = None,
        output_format: str = "text",
        allowed_tools: list | None = None,
        system: str | None = None,
        timeout: int | None = None,
    ) -> RunResult:
        """
        Ejecuta una tarea con Claude usando Max subscription (sin API billing).

        Args:
            task: Prompt/tarea completa
            cwd: Directorio de trabajo
            output_format: "text" | "json" | "stream-json"
            allowed_tools: Tools built-in permitidas
            system: System prompt adicional
            timeout: Timeout en segundos (default: AGENT_TIMEOUT_SECONDS)
        """
        cmd = ["claude", "--print", "--output-format", output_format]

        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])

        # Siempre construir el system con VERSION_COMPACT como prefijo
        final_system = self._build_system(system)
        cmd.extend(["--system", final_system])

        cmd.append(task)

        work_cwd = cwd or str(self.workdir)
        env = self._build_env()
        _timeout = timeout or self.timeout

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_cwd,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_timeout
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()

            # Detectar rate limit
            if "rate limit" in output.lower() or proc.returncode == 429:
                return RunResult(
                    output=output, exit_code=429,
                    success=False, error="Rate limit alcanzado"
                )

            return RunResult(
                output=output,
                exit_code=proc.returncode,
                success=proc.returncode == 0,
                error=error if proc.returncode != 0 else "",
            )

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return RunResult(
                output="", exit_code=124, success=False,
                error=f"Timeout después de {_timeout}s"
            )
        except Exception as e:
            return RunResult(output="", exit_code=1, success=False, error=str(e))

    async def run_with_tools(
        self,
        task: str,
        tools: list | None = None,
        cwd: str | None = None,
        system: str | None = None,
    ) -> RunResult:
        """
        Ejecuta tarea con acceso a herramientas built-in.
        Default: todas las tools disponibles.
        """
        return await self.run(
            task=task,
            cwd=cwd,
            allowed_tools=tools or self.BUILTIN_TOOLS,
            system=system,
            output_format="text",
        )

    async def run_code_task(
        self,
        task: str,
        project_path: str,
        readonly: bool = False,
    ) -> RunResult:
        """Ejecuta tarea de desarrollo en un proyecto."""
        tools = ["Read", "Glob", "Grep"] if readonly else self.BUILTIN_TOOLS
        system = (
            "Eres un ingeniero senior. Completa la tarea de desarrollo con código "
            "limpio, bien documentado y siguiendo las mejores prácticas. "
            "Cuando crees archivos, asegúrate de que sean completos y funcionales."
        )
        return await self.run_with_tools(task=task, tools=tools, cwd=project_path, system=system)

    async def stream(
        self,
        task: str,
        cwd: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Streaming de tokens para UI en tiempo real.
        Usa stream-json output format del CLI.
        """
        cmd = ["claude", "--print", "--output-format", "stream-json", task]
        work_cwd = cwd or str(self.workdir)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_cwd,
                env=self._build_env(),
            )

            async for line in proc.stdout:
                try:
                    event = json.loads(line.decode("utf-8", errors="replace"))
                    if event.get("type") == "assistant":
                        for block in event.get("message", {}).get("content", []):
                            if block.get("type") == "text":
                                yield block["text"]
                except (json.JSONDecodeError, KeyError):
                    continue

            await proc.wait()

        except Exception as e:
            yield f"\n[Error en streaming: {e}]"
