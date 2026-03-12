"""
RuntimeExecutor — Ejecuta las Actions del AgenticLoop

Cada Action type tiene su executor:
  BASH     → subprocess en /workspaces con timeout y captura de output
  READ     → leer archivo con rango de líneas
  WRITE    → crear/sobreescribir archivo completo
  EDIT     → editar fragmento específico (str_replace)
  BROWSE   → Playwright headless (o httpx fallback)
  THINK    → no-op, retorna observación vacía
  DELEGATE → llama al AgenticLoop con un agente especialista

Seguridad:
  - Comandos bash en directorio /workspaces (aislado del host)
  - Timeout configurable por tipo de acción
  - Path traversal protection (no puede salir de /workspaces)
  - Lista negra de comandos peligrosos (rm -rf /, format, etc.)
"""

import asyncio
import os
import re
from pathlib import Path

from agent.core.agentic_loop import Action, ActionType, Observation
from agent.core.jupyter_kernel import JupyterKernelManager


DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"dd\s+if=",
    r">\s*/dev/sd",
    r"chmod\s+777\s+/",
    r":(){:|:&};:",  # fork bomb
    r"curl.*\|\s*bash",
    r"wget.*\|\s*sh",
]


class RuntimeExecutor:
    """
    Ejecutor de acciones para el AgenticLoop.
    Aisla la ejecución en /workspaces y captura todos los outputs.
    """

    DEFAULT_TIMEOUTS = {
        ActionType.BASH:    60,
        ActionType.READ:    5,
        ActionType.WRITE:   5,
        ActionType.EDIT:    5,
        ActionType.BROWSE:  30,
        ActionType.THINK:   0,
        ActionType.DELEGATE: 300,
    }

    def __init__(
        self,
        base_dir: str = "/workspaces",
        enable_browser: bool = True,
        runner=None,  # ClaudeMaxRunner, para DELEGATE
    ):
        self.base_dir       = Path(base_dir)
        self.enable_browser = enable_browser
        self._runner        = runner
        self._browser       = None
        self._jupyter       = JupyterKernelManager()
        # Session ID activa (se rellena por el loop en cada ejecución)
        self._current_session: str = "default"

    async def execute(self, action: Action, cwd: str = None) -> Observation:
        """Dispatch al executor correcto según el tipo de acción."""
        work_dir = Path(cwd or self.base_dir)

        try:
            match action.type:
                case ActionType.BASH:
                    return await self._exec_bash(action, work_dir)
                case ActionType.READ:
                    return await self._exec_read(action, work_dir)
                case ActionType.WRITE:
                    return await self._exec_write(action, work_dir)
                case ActionType.EDIT:
                    return await self._exec_edit(action, work_dir)
                case ActionType.BROWSE:
                    return await self._exec_browse(action)
                case ActionType.IPYTHON:
                    return await self._exec_ipython(action)
                case ActionType.THINK:
                    return Observation(
                        action_type=ActionType.THINK,
                        content="",
                        success=True,
                    )
                case ActionType.DELEGATE:
                    return await self._exec_delegate(action, work_dir)
                case _:
                    return Observation(
                        action_type=action.type,
                        content=f"Tipo de acción desconocido: {action.type}",
                        success=False,
                    )
        except asyncio.TimeoutError:
            timeout = self.DEFAULT_TIMEOUTS.get(action.type, 60)
            return Observation(
                action_type=action.type,
                content=f"Timeout ({timeout}s) ejecutando {action.type.value}",
                success=False,
            )
        except Exception as e:
            return Observation(
                action_type=action.type,
                content=f"Error interno: {type(e).__name__}: {e}",
                success=False,
            )

    # ─────────────────────────────────────────────
    # BASH
    # ─────────────────────────────────────────────

    async def _exec_bash(self, action: Action, cwd: Path) -> Observation:
        cmd     = action.payload.get("cmd", "")
        timeout = int(action.payload.get("timeout", self.DEFAULT_TIMEOUTS[ActionType.BASH]))

        # Seguridad: bloquear comandos peligrosos
        if self._is_dangerous(cmd):
            return Observation(
                action_type=ActionType.BASH,
                content=f"❌ BLOQUEADO: comando potencialmente peligroso: {cmd[:100]}",
                success=False,
                metadata={"blocked": True},
            )

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # unificar stdout y stderr
            cwd=str(cwd),
            env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"},
        )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace")
            # Truncar si es muy largo
            if len(output) > 8000:
                output = output[:4000] + "\n...[TRUNCADO]...\n" + output[-2000:]
            return Observation(
                action_type=ActionType.BASH,
                content=output or "(sin output)",
                success=proc.returncode == 0,
                metadata={"exit_code": proc.returncode, "cmd": cmd[:200]},
            )
        except asyncio.TimeoutError:
            proc.kill()
            return Observation(
                action_type=ActionType.BASH,
                content=f"Timeout ({timeout}s): {cmd[:100]}",
                success=False,
            )

    def _is_dangerous(self, cmd: str) -> bool:
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return True
        return False

    # ─────────────────────────────────────────────
    # READ
    # ─────────────────────────────────────────────

    async def _exec_read(self, action: Action, cwd: Path) -> Observation:
        path  = self._safe_path(action.payload.get("path", ""), cwd)
        start = int(action.payload.get("start", 1))
        end   = action.payload.get("end")  # None = hasta el final

        if not path.exists():
            return Observation(
                action_type=ActionType.READ,
                content=f"Archivo no encontrado: {path}",
                success=False,
            )

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            total = len(lines)

            if end is None:
                end = min(start + 99, total)  # máximo 100 líneas por defecto

            selected = lines[start - 1:int(end)]
            numbered = "\n".join(f"{start + i:4d}│ {line}" for i, line in enumerate(selected))

            return Observation(
                action_type=ActionType.READ,
                content=f"📄 {path} (líneas {start}-{end} de {total}):\n{numbered}",
                success=True,
                metadata={"total_lines": total, "path": str(path)},
            )
        except Exception as e:
            return Observation(
                action_type=ActionType.READ,
                content=f"Error leyendo {path}: {e}",
                success=False,
            )

    # ─────────────────────────────────────────────
    # WRITE
    # ─────────────────────────────────────────────

    async def _exec_write(self, action: Action, cwd: Path) -> Observation:
        path    = self._safe_path(action.payload.get("path", ""), cwd)
        content = action.payload.get("content", "")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            path.write_text(content, encoding="utf-8")
            lines = content.count("\n") + 1
            verb  = "actualizado" if existed else "creado"
            return Observation(
                action_type=ActionType.WRITE,
                content=f"✅ Archivo {verb}: {path} ({lines} líneas, {len(content)} bytes)",
                success=True,
                metadata={"path": str(path), "lines": lines},
            )
        except Exception as e:
            return Observation(
                action_type=ActionType.WRITE,
                content=f"Error escribiendo {path}: {e}",
                success=False,
            )

    # ─────────────────────────────────────────────
    # EDIT (str_replace)
    # ─────────────────────────────────────────────

    async def _exec_edit(self, action: Action, cwd: Path) -> Observation:
        path    = self._safe_path(action.payload.get("path", ""), cwd)
        old_str = action.payload.get("old", "")
        new_str = action.payload.get("new", "")

        if not path.exists():
            return Observation(
                action_type=ActionType.EDIT,
                content=f"Archivo no encontrado: {path}",
                success=False,
            )

        try:
            original = path.read_text(encoding="utf-8")

            occurrences = original.count(old_str)
            if occurrences == 0:
                return Observation(
                    action_type=ActionType.EDIT,
                    content=f"Texto no encontrado en {path}:\n{old_str[:200]}",
                    success=False,
                )
            if occurrences > 1:
                return Observation(
                    action_type=ActionType.EDIT,
                    content=f"Texto ambiguo ({occurrences} ocurrencias) en {path}. Sé más específico.",
                    success=False,
                )

            modified = original.replace(old_str, new_str, 1)
            path.write_text(modified, encoding="utf-8")

            # Generar diff simple
            old_lines = old_str.splitlines()
            new_lines = new_str.splitlines()
            diff = (
                "\n".join(f"- {l}" for l in old_lines[:5]) +
                "\n" +
                "\n".join(f"+ {l}" for l in new_lines[:5])
            )

            return Observation(
                action_type=ActionType.EDIT,
                content=f"✅ Editado {path}:\n{diff}",
                success=True,
                metadata={"path": str(path)},
            )
        except Exception as e:
            return Observation(
                action_type=ActionType.EDIT,
                content=f"Error editando {path}: {e}",
                success=False,
            )

    # ─────────────────────────────────────────────
    # BROWSE (Playwright o httpx fallback)
    # ─────────────────────────────────────────────

    async def _exec_browse(self, action: Action) -> Observation:
        url        = action.payload.get("url", "")
        browse_act = action.payload.get("action", "goto")

        if not url.startswith(("http://", "https://")):
            return Observation(
                action_type=ActionType.BROWSE,
                content=f"URL inválida: {url}",
                success=False,
            )

        # Intentar con Playwright si está disponible
        try:
            from playwright.async_api import async_playwright
            return await self._playwright_browse(url, browse_act, action.payload)
        except ImportError:
            pass

        # Fallback: httpx + extracción de texto
        try:
            import httpx
            from html.parser import HTMLParser

            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text_parts = []
                    self._skip = False
                def handle_starttag(self, tag, attrs):
                    if tag in ("script", "style", "nav", "footer"):
                        self._skip = True
                def handle_endtag(self, tag):
                    if tag in ("script", "style", "nav", "footer"):
                        self._skip = False
                def handle_data(self, data):
                    if not self._skip and data.strip():
                        self.text_parts.append(data.strip())

            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                headers={"User-Agent": "CLAUDE-BRAIN/2.0"},
            ) as client:
                resp = await client.get(url)
                parser = TextExtractor()
                parser.feed(resp.text)
                text = " ".join(parser.text_parts)[:5000]

            return Observation(
                action_type=ActionType.BROWSE,
                content=f"🌐 {url}\n\n{text}",
                success=True,
                metadata={"status_code": resp.status_code, "method": "httpx"},
            )
        except Exception as e:
            return Observation(
                action_type=ActionType.BROWSE,
                content=f"Error navegando {url}: {e}",
                success=False,
            )

    async def _playwright_browse(self, url: str, action: str, payload: dict) -> Observation:
        """Browser completo con Playwright — screenshots, clicks, forms."""
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=20000)

                if action == "click":
                    selector = payload.get("selector", "")
                    if selector:
                        await page.click(selector)
                        await page.wait_for_load_state("networkidle")

                elif action == "type":
                    selector = payload.get("selector", "")
                    text     = payload.get("text", "")
                    if selector and text:
                        await page.fill(selector, text)

                elif action == "scroll":
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")

                # Extraer texto del DOM
                text = await page.evaluate("""() => {
                    document.querySelectorAll('script,style,nav,footer,aside').forEach(e=>e.remove())
                    return document.body ? document.body.innerText.substring(0, 5000) : ''
                }""")

                current_url = page.url
                title       = await page.title()

                return Observation(
                    action_type=ActionType.BROWSE,
                    content=f"🌐 {title}\n{current_url}\n\n{text}",
                    success=True,
                    metadata={"url": current_url, "method": "playwright"},
                )
            finally:
                await browser.close()

    # ─────────────────────────────────────────────
    # IPYTHON — Kernel Python con estado persistente
    # ─────────────────────────────────────────────

    async def _exec_ipython(self, action: Action) -> Observation:
        """
        Ejecuta código en el kernel Jupyter de la sesión actual.
        Las variables persisten entre llamadas (como en un notebook).
        Si hay imágenes (matplotlib), se incluyen en la observación.
        """
        code    = action.payload.get("code", "")
        timeout = int(action.payload.get("timeout", 120))
        session = self._current_session

        cell_result = await self._jupyter.execute(
            session_id=session,
            code=code,
            timeout=timeout,
        )

        # Construir texto de observación
        obs_text = cell_result.to_observation_text()

        return Observation(
            action_type=ActionType.IPYTHON,
            content=obs_text,
            success=cell_result.success,
            metadata={
                "images":      cell_result.images,  # base64 PNGs
                "exec_count":  cell_result.exec_count,
                "duration_ms": cell_result.duration_ms,
                "has_images":  len(cell_result.images) > 0,
            },
        )

    # ─────────────────────────────────────────────
    # DELEGATE
    # ─────────────────────────────────────────────

    async def _exec_delegate(self, action: Action, cwd: Path) -> Observation:
        """Delega la subtarea a un subagente especialista."""
        agent_name = action.payload.get("agent", "")
        task       = action.payload.get("task", "")

        if not self._runner:
            return Observation(
                action_type=ActionType.DELEGATE,
                content="Delegación no disponible (runner no configurado)",
                success=False,
            )

        try:
            # Importación tardía para evitar circular
            from agent.core.agentic_loop import AgenticLoop
            from agent.registry.component_registry import ComponentRegistry

            registry = ComponentRegistry()
            agent    = registry.get_agent(agent_name)
            extra_system = agent.system_prompt if agent else ""

            subloop = AgenticLoop(
                runner=self._runner,
                runtime=self,
                max_iterations=10,  # subagentes tienen menos iteraciones
            )

            result = await subloop.run(
                task=task,
                session_id=f"delegate_{agent_name}",
                cwd=str(cwd),
                extra_system=extra_system,
            )

            return Observation(
                action_type=ActionType.DELEGATE,
                content=(
                    f"Subagente '{agent_name}' completó:\n{result.message}\n"
                    f"({result.iterations} iteraciones)"
                ),
                success=result.success,
                metadata={"agent": agent_name, "iterations": result.iterations},
            )
        except Exception as e:
            return Observation(
                action_type=ActionType.DELEGATE,
                content=f"Error en delegación a '{agent_name}': {e}",
                success=False,
            )

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def _safe_path(self, path_str: str, cwd: Path) -> Path:
        """Resuelve el path y previene path traversal fuera de /workspaces."""
        if path_str.startswith("/"):
            resolved = Path(path_str).resolve()
        else:
            resolved = (cwd / path_str).resolve()

        # Asegurar que el path está dentro de /workspaces o /tmp
        allowed_roots = [Path("/workspaces").resolve(), Path("/tmp").resolve()]
        for root in allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                pass

        # Si está fuera, redirigir dentro de /workspaces
        return Path("/workspaces") / path_str.lstrip("/")
