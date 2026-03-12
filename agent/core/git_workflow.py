"""
GitWorkflow — Capacidades Git/GitHub del agente

Equivalente al SWE-bench pipeline de OpenHands:
  - Clonar repos, crear branches, commits, push, PRs
  - Analizar diffs y aplicar patches
  - Resolver GitHub Issues de forma autónoma
  - Code review automático en PRs
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from agent.config import settings


@dataclass
class GitContext:
    repo_url:   str
    branch:     str
    work_dir:   Path
    issue_num:  int | None = None
    pr_url:     str | None = None


class GitWorkflow:
    """
    Workflow Git completo para el agente autónomo.

    Flujo típico de resolución de issue:
      1. clone_repo(url)         → clona en /workspaces/{repo}
      2. create_branch(name)     → crea branch fix/issue-N
      3. [AgenticLoop trabaja]   → edita archivos, ejecuta tests
      4. commit_changes(msg)     → git add -A && git commit
      5. push_branch()           → git push origin branch
      6. create_pr(title, body)  → crea PR via GitHub API
    """

    GITHUB_API = "https://api.github.com"

    def __init__(self, github_token: str = "", work_base: str | None = None):
        self.token     = github_token or settings.github_token
        self.work_base = Path(work_base or settings.workdir)
        self._ctx: GitContext | None = None

    async def _run(self, cmd: str, cwd: str = None) -> tuple[bool, str]:
        """Ejecuta comando git y retorna (success, output)."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd or str(self.work_base),
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode("utf-8", errors="replace").strip()
        return proc.returncode == 0, output

    # ─────────────────────────────────────────────
    # REPO SETUP
    # ─────────────────────────────────────────────

    async def clone_repo(self, url: str, branch: str = "main") -> GitContext:
        """Clona el repo en /workspaces/{name}."""
        repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        work_dir  = self.work_base / repo_name

        if work_dir.exists():
            ok, out = await self._run("git pull", str(work_dir))
        else:
            # Inyectar token en la URL si es GitHub
            auth_url = url
            if self.token and "github.com" in url:
                auth_url = url.replace("https://", f"https://{self.token}@")
            ok, out = await self._run(f"git clone --depth=50 {auth_url} {work_dir}")

        self._ctx = GitContext(repo_url=url, branch=branch, work_dir=work_dir)
        return self._ctx

    async def create_branch(self, branch_name: str) -> tuple[bool, str]:
        """Crea y cambia a una nueva branch."""
        cwd = str(self._ctx.work_dir) if self._ctx else str(self.work_base)
        ok, out = await self._run(
            f"git checkout -b {branch_name} 2>/dev/null || git checkout {branch_name}",
            cwd
        )
        if self._ctx and ok:
            self._ctx.branch = branch_name
        return ok, out

    async def get_diff(self) -> str:
        """Retorna el diff actual (staged + unstaged)."""
        cwd = str(self._ctx.work_dir) if self._ctx else str(self.work_base)
        _, diff = await self._run("git diff HEAD", cwd)
        return diff or "(sin cambios)"

    async def get_status(self) -> str:
        """Estado del repositorio."""
        cwd = str(self._ctx.work_dir) if self._ctx else str(self.work_base)
        _, status = await self._run("git status --short", cwd)
        _, branch = await self._run("git branch --show-current", cwd)
        return f"Branch: {branch}\n{status or '(limpio)'}"

    async def commit_changes(self, message: str) -> tuple[bool, str]:
        """Stage y commit de todos los cambios."""
        cwd = str(self._ctx.work_dir) if self._ctx else str(self.work_base)
        await self._run("git add -A", cwd)
        return await self._run(f'git commit -m "{message}"', cwd)

    async def push_branch(self) -> tuple[bool, str]:
        """Push de la branch actual."""
        cwd = str(self._ctx.work_dir) if self._ctx else str(self.work_base)
        branch = self._ctx.branch if self._ctx else "main"
        return await self._run(f"git push origin {branch} --force-with-lease", cwd)

    # ─────────────────────────────────────────────
    # GITHUB API
    # ─────────────────────────────────────────────

    async def get_issue(self, repo: str, issue_num: int) -> dict:
        """Obtiene el contenido de un GitHub Issue."""
        import httpx
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{self.GITHUB_API}/repos/{repo}/issues/{issue_num}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def create_pr(
        self,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str = "main",
    ) -> dict:
        """Crea un Pull Request."""
        import httpx
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {
            "title": title,
            "body":  body,
            "head":  head_branch,
            "base":  base_branch,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self.GITHUB_API}/repos/{repo}/pulls",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    # ─────────────────────────────────────────────
    # ISSUE → PR PIPELINE (SWE-bench style)
    # ─────────────────────────────────────────────

    async def build_issue_context(self, repo: str, issue_num: int) -> str:
        """
        Construye el contexto completo de un issue para el AgenticLoop.
        Incluye: título, descripción, labels, estructura del repo.
        """
        issue = await self.get_issue(repo, issue_num)

        title  = issue.get("title", "")
        body   = issue.get("body", "")
        labels = ", ".join(l["name"] for l in issue.get("labels", []))

        # Estructura del repo
        cwd = str(self._ctx.work_dir) if self._ctx else str(self.work_base)
        _, tree = await self._run("find . -type f -name '*.py' -o -name '*.ts' | head -30", cwd)

        return (
            f"## GitHub Issue #{issue_num}: {title}\n\n"
            f"**Labels:** {labels or 'ninguno'}\n\n"
            f"**Descripción:**\n{body[:2000]}\n\n"
            f"**Archivos del repo:**\n{tree}\n\n"
            f"**Tu objetivo:** Resolver este issue. Al terminar:\n"
            f"1. Haz commit de los cambios\n"
            f"2. Usa la acción finish con el resumen de lo que hiciste"
        )
