"""
Modelos de datos compartidos entre módulos de CLAUDE-BRAIN.

Centraliza las dataclasses usadas por ComponentRegistry y SkillManager
para eliminar duplicación de la clase Skill.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ─────────────────────────────────────────────────────────
# AGENT
# ─────────────────────────────────────────────────────────

@dataclass
class Agent:
    name: str
    description: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    model: str = "sonnet"
    category: str = ""
    source: str = "aitmpl"


# ─────────────────────────────────────────────────────────
# SKILL — Clase unificada (antes duplicada en registry y skill_manager)
# ─────────────────────────────────────────────────────────

@dataclass
class Skill:
    name: str
    description: str
    path: Path
    allowed_tools: list[str] = field(default_factory=list)
    category: str = ""
    source: str = "aitmpl"
    system_addition: str = ""
    _content: str | None = field(default=None, repr=False)

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


# ─────────────────────────────────────────────────────────
# COMMAND
# ─────────────────────────────────────────────────────────

@dataclass
class Command:
    name: str
    description: str
    body: str
    argument_hint: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    source: str = "aitmpl"

    def render(self, arguments: str = "") -> str:
        """Renderiza el comando sustituyendo $ARGUMENTS."""
        return self.body.replace("$ARGUMENTS", arguments)
