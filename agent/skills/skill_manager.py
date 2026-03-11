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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    tools_required: list = field(default_factory=list)
    system_addition: str = ""
    _content: Optional[str] = field(default=None, repr=False)

    @property
    def content(self) -> str:
        """Carga lazy — solo cuando se activa la skill."""
        if self._content is None:
            skill_file = self.path / "SKILL.md"
            raw = skill_file.read_text(encoding="utf-8")
            parts = raw.split("---", 2)
            self._content = parts[2].strip() if len(parts) >= 3 else raw
        return self._content

    def to_prompt_block(self) -> str:
        """Formatea la skill como bloque XML para inyectar en el prompt."""
        return f"<skill name='{self.name}'>\n{self.content}\n</skill>"


class SkillManager:
    """
    Gestiona el ciclo de vida de skills modulares.

    Directorios de búsqueda (en orden):
    1. /app/skills (montado desde ./skills/ del repo)
    2. /workspaces/.skills (skills de proyectos específicos)
    3. ~/.claude/skills (skills globales del usuario)
    """

    DEFAULT_DIRS = [
        "/app/skills",
        "/workspaces/.skills",
        os.path.expanduser("~/.claude/skills"),
    ]

    def __init__(self, dirs: Optional[list] = None):
        self.dirs = dirs or self.DEFAULT_DIRS
        self._registry: dict[str, Skill] = {}
        self._active: set[str] = set()
        self._scan_all()

    def _scan_all(self):
        """Escanea todos los directorios y registra skills disponibles."""
        for d in self.dirs:
            if not os.path.exists(d):
                continue
            for skill_file in glob.glob(f"{d}/*/SKILL.md"):
                path = Path(skill_file).parent
                self._register_skill(path)

    def _register_skill(self, path: Path):
        """Registra una skill desde su directorio."""
        skill_file = path / "SKILL.md"
        try:
            raw = skill_file.read_text(encoding="utf-8")
            parts = raw.split("---", 2)
            if len(parts) < 3:
                return  # Sin frontmatter YAML
            fm = yaml.safe_load(parts[1]) or {}
            name = fm.get("name")
            if not name:
                return
            self._registry[name] = Skill(
                name=name,
                description=fm.get("description", "Sin descripción"),
                path=path,
                tools_required=fm.get("x-tools-required", []),
                system_addition=fm.get("x-system-prompt-addition", ""),
            )
        except Exception:
            pass

    def reload(self):
        """Recarga todas las skills (útil para desarrollo)."""
        self._registry.clear()
        self._scan_all()

    # ─────────────────────────────────────────────
    # API PÚBLICA
    # ─────────────────────────────────────────────

    def list_skills(self) -> list[dict]:
        """Lista todas las skills disponibles."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "tools_required": s.tools_required,
                "active": s.name in self._active,
            }
            for s in self._registry.values()
        ]

    def get_index_prompt(self) -> str:
        """
        Índice compacto de skills (~50 tokens/skill).
        Se inyecta SIEMPRE en el system prompt para que Claude
        sepa qué skills existen sin cargar el contenido completo.
        """
        if not self._registry:
            return ""
        lines = ["<skills_disponibles>"]
        for s in self._registry.values():
            lines.append(f"  • {s.name}: {s.description}")
        lines.append("</skills_disponibles>")
        return "\n".join(lines)

    def activate(self, name: str) -> Optional[Skill]:
        """Activa una skill (carga su contenido)."""
        skill = self._registry.get(name)
        if skill:
            _ = skill.content  # Trigger carga lazy
            self._active.add(name)
        return skill

    def deactivate(self, name: str):
        """Desactiva una skill."""
        self._active.discard(name)

    def get_active_tools(self) -> list[str]:
        """Herramientas requeridas por todas las skills activas."""
        tools = set()
        for name in self._active:
            if skill := self._registry.get(name):
                tools.update(skill.tools_required)
        return list(tools)

    def build_task_prompt(
        self,
        user_task: str,
        skill_names: Optional[list[str]] = None,
        memory_context: str = "",
    ) -> str:
        """
        Construye el prompt final combinando:
        - Contexto de memoria
        - Contenido de skills activas
        - La tarea del usuario
        """
        parts = []

        if memory_context:
            parts.append(memory_context)

        # Inyectar skills solicitadas
        for name in (skill_names or []):
            skill = self.activate(name)
            if skill:
                parts.append(skill.to_prompt_block())
                # Añadir instrucciones adicionales al sistema
                if skill.system_addition:
                    parts.append(
                        f"<instrucciones_{name}>\n{skill.system_addition}\n</instrucciones_{name}>"
                    )

        parts.append(f"<tarea>\n{user_task}\n</tarea>")
        return "\n\n".join(parts)

    def get_system_prompt(self) -> str:
        """System prompt con índice de skills + instrucciones adicionales de skills activas."""
        parts = [
            "Eres CLAUDE-BRAIN, un agente de desarrollo autónomo.",
            self.get_index_prompt(),
        ]
        # Añadir instrucciones adicionales de skills activas
        for name in self._active:
            if skill := self._registry.get(name):
                if skill.system_addition:
                    parts.append(skill.system_addition)
        return "\n\n".join(filter(None, parts))
