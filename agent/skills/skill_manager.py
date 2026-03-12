"""
SkillManager — Sistema de Skills modulares con formato SKILL.md

Implementa progressive disclosure:
  - Índice (~50 tokens/skill) cargado siempre en el prompt
  - Contenido completo cargado solo cuando la skill se activa
  - Tools requeridas por cada skill pasadas al CLI

Compatible con el formato SKILL.md estándar (2025):
  - Claude Code, VS Code/Copilot, Cursor, Gemini CLI
"""

import glob
import os
from pathlib import Path

import yaml

from agent.config import settings
from agent.models import Skill


class SkillManager:
    """
    Gestiona el ciclo de vida de skills modulares.

    Directorios de búsqueda (en orden):
    1. /app/skills (montado desde ./skills/ del repo)
    2. /workspaces/.skills (skills de proyectos específicos)
    3. ~/.claude/skills (skills globales del usuario)
    """

    DEFAULT_DIRS = [
        settings.project_skills_dir,
        f"{settings.workdir}/.skills",
        os.path.expanduser("~/.claude/skills"),
    ]

    def __init__(self, dirs: list[str] | None = None):
        self.dirs = dirs or self.DEFAULT_DIRS
        self._registry: dict[str, Skill] = {}
        self._active: set[str] = set()
        self._scan_all()

    def _scan_all(self):
        for d in self.dirs:
            if not os.path.exists(d):
                continue
            for skill_file in glob.glob(f"{d}/*/SKILL.md"):
                path = Path(skill_file).parent
                self._register_skill(path)

    def _register_skill(self, path: Path):
        skill_file = path / "SKILL.md"
        try:
            raw = skill_file.read_text(encoding="utf-8")
            parts = raw.split("---", 2)
            if len(parts) < 3:
                return
            fm = yaml.safe_load(parts[1]) or {}
            name = fm.get("name")
            if not name:
                return
            self._registry[name] = Skill(
                name=name,
                description=fm.get("description", "Sin descripción"),
                path=path,
                allowed_tools=fm.get("x-tools-required", []),
                system_addition=fm.get("x-system-prompt-addition", ""),
            )
        except Exception:
            pass

    def reload(self):
        self._registry.clear()
        self._scan_all()

    # ── API pública ──────────────────────────────────────

    def list_skills(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "tools_required": s.allowed_tools,
                "active": s.name in self._active,
            }
            for s in self._registry.values()
        ]

    def get_index_prompt(self) -> str:
        if not self._registry:
            return ""
        lines = ["<skills_disponibles>"]
        for s in self._registry.values():
            lines.append(f"  • {s.name}: {s.description}")
        lines.append("</skills_disponibles>")
        return "\n".join(lines)

    def activate(self, name: str) -> Skill | None:
        skill = self._registry.get(name)
        if skill:
            _ = skill.content  # Trigger lazy load
            self._active.add(name)
        return skill

    def deactivate(self, name: str):
        self._active.discard(name)

    def get_active_tools(self) -> list[str]:
        tools: set[str] = set()
        for name in self._active:
            if skill := self._registry.get(name):
                tools.update(skill.allowed_tools)
        return list(tools)

    def build_task_prompt(
        self,
        user_task: str,
        skill_names: list[str] | None = None,
        memory_context: str = "",
    ) -> str:
        parts: list[str] = []

        if memory_context:
            parts.append(memory_context)

        for name in (skill_names or []):
            skill = self.activate(name)
            if skill:
                parts.append(skill.to_prompt_block())
                if skill.system_addition:
                    parts.append(
                        f"<instrucciones_{name}>\n{skill.system_addition}\n</instrucciones_{name}>"
                    )

        parts.append(f"<tarea>\n{user_task}\n</tarea>")
        return "\n\n".join(parts)

    def get_system_prompt(self) -> str:
        parts = [
            "Eres CLAUDE-BRAIN, un agente de desarrollo autónomo.",
            self.get_index_prompt(),
        ]
        for name in self._active:
            if skill := self._registry.get(name):
                if skill.system_addition:
                    parts.append(skill.system_addition)
        return "\n\n".join(filter(None, parts))
