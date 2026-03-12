"""
ComponentRegistry — Registro unificado de los 119 componentes de aitmpl.com

Tipos de componentes:
  Agent   — Especialista con system prompt propio (36 agentes)
              frontmatter: name, description, tools, model
              body: system prompt completo del agente

  Skill   — Conocimiento/guías inyectables (70 skills en SKILL.md)
              frontmatter: name, description, allowed-tools
              body: guías y ejemplos

  Command — Comandos ejecutables con pasos bash inline (13 comandos)
              frontmatter: description, argument-hint, allowed-tools
              body: pasos con !`bash` inline

Directorios:
  /app/components/agents/     ← 36 agentes descargados por install-aitmpl.sh
  /app/components/skills/     ← 70 skills
  /app/components/commands/   ← 13 comandos
  /app/skills/                ← skills propias del proyecto (nextjs-supabase-dev, etc.)
"""

import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ─────────────────────────────────────────────────────────
# MODELOS DE DATOS
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


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    allowed_tools: list[str] = field(default_factory=list)
    category: str = ""
    source: str = "aitmpl"
    _content: Optional[str] = field(default=None, repr=False)

    @property
    def content(self) -> str:
        if self._content is None:
            skill_file = self.path / "SKILL.md"
            raw = skill_file.read_text(encoding="utf-8")
            # Quitar frontmatter YAML
            parts = raw.split("---", 2)
            self._content = parts[2].strip() if len(parts) >= 3 else raw
        return self._content

    def to_prompt_block(self) -> str:
        return f"<skill name='{self.name}'>\n{self.content}\n</skill>"


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


# ─────────────────────────────────────────────────────────
# REGISTRY PRINCIPAL
# ─────────────────────────────────────────────────────────

class ComponentRegistry:
    """
    Registro central de todos los componentes disponibles.
    Soporta búsqueda semántica por descripción y selección por nombre.
    """

    COMPONENT_DIRS = {
        "agents":   "/app/components/agents",
        "skills":   "/app/components/skills",
        "commands": "/app/components/commands",
        # Skills propias del proyecto
        "project_skills": "/app/skills",
    }

    def __init__(self):
        self.agents:   dict[str, Agent]   = {}
        self.skills:   dict[str, Skill]   = {}
        self.commands: dict[str, Command] = {}
        self._scan_all()

    def _parse_frontmatter(self, raw: str) -> tuple[dict, str]:
        """Extrae frontmatter YAML y cuerpo del markdown."""
        parts = raw.split("---", 2)
        if len(parts) < 3:
            return {}, raw
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            fm = {}
        return fm, parts[2].strip()

    def _parse_tools(self, tools_raw) -> list[str]:
        """Normaliza tools: string CSV o lista."""
        if not tools_raw:
            return []
        if isinstance(tools_raw, list):
            return [t.strip() for t in tools_raw if t.strip()]
        return [t.strip() for t in str(tools_raw).split(",") if t.strip()]

    # ── Loaders ──────────────────────────────────────────

    def _load_agents(self, base_dir: str):
        for fp in glob.glob(f"{base_dir}/**/*.md", recursive=True):
            try:
                raw = Path(fp).read_text(encoding="utf-8")
                fm, body = self._parse_frontmatter(raw)
                if not fm.get("name") or not body.strip():
                    continue
                # Detectar categoría desde el path
                rel = os.path.relpath(fp, base_dir)
                category = rel.split(os.sep)[0] if os.sep in rel else "general"

                self.agents[fm["name"]] = Agent(
                    name=fm["name"],
                    description=str(fm.get("description", ""))[:200],
                    system_prompt=body,
                    tools=self._parse_tools(fm.get("tools", "")),
                    model=str(fm.get("model", "sonnet")),
                    category=category,
                )
            except Exception:
                pass

    def _load_skills(self, base_dir: str, source: str = "aitmpl"):
        for fp in glob.glob(f"{base_dir}/**/SKILL.md", recursive=True):
            try:
                raw = Path(fp).read_text(encoding="utf-8")
                fm, _ = self._parse_frontmatter(raw)
                name = fm.get("name")
                if not name:
                    continue
                path = Path(fp).parent
                rel = os.path.relpath(str(path), base_dir)
                category = rel.split(os.sep)[0] if os.sep in rel else "general"

                self.skills[name] = Skill(
                    name=name,
                    description=str(fm.get("description", ""))[:200],
                    path=path,
                    allowed_tools=self._parse_tools(
                        fm.get("allowed-tools", fm.get("x-tools-required", ""))
                    ),
                    category=category,
                    source=source,
                )
            except Exception:
                pass

    def _load_commands(self, base_dir: str):
        for fp in glob.glob(f"{base_dir}/**/*.md", recursive=True):
            try:
                raw = Path(fp).read_text(encoding="utf-8")
                fm, body = self._parse_frontmatter(raw)
                if not body.strip():
                    continue
                name = Path(fp).stem

                self.commands[name] = Command(
                    name=name,
                    description=str(fm.get("description", ""))[:200],
                    body=body,
                    argument_hint=str(fm.get("argument-hint", "")),
                    allowed_tools=self._parse_tools(fm.get("allowed-tools", "")),
                )
            except Exception:
                pass

    def _scan_all(self):
        dirs = self.COMPONENT_DIRS
        if os.path.exists(dirs["agents"]):
            self._load_agents(dirs["agents"])
        if os.path.exists(dirs["skills"]):
            self._load_skills(dirs["skills"], source="aitmpl")
        if os.path.exists(dirs["commands"]):
            self._load_commands(dirs["commands"])
        if os.path.exists(dirs["project_skills"]):
            self._load_skills(dirs["project_skills"], source="project")

    def reload(self):
        self.agents.clear()
        self.skills.clear()
        self.commands.clear()
        self._scan_all()

    # ── API pública ───────────────────────────────────────

    def get_agent(self, name: str) -> Optional[Agent]:
        return self.agents.get(name)

    def get_skill(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)

    def get_command(self, name: str) -> Optional[Command]:
        return self.commands.get(name)

    def summary(self) -> dict:
        """Resumen para health check / Telegram /status."""
        return {
            "agents":   len(self.agents),
            "skills":   len(self.skills),
            "commands": len(self.commands),
            "total":    len(self.agents) + len(self.skills) + len(self.commands),
        }

    def catalog(self) -> dict:
        """Catálogo completo estructurado por categoría."""
        agents_by_cat: dict[str, list] = {}
        for a in self.agents.values():
            agents_by_cat.setdefault(a.category, []).append({
                "name": a.name, "description": a.description
            })

        skills_by_cat: dict[str, list] = {}
        for s in self.skills.values():
            skills_by_cat.setdefault(s.category, []).append({
                "name": s.name, "description": s.description, "source": s.source
            })

        return {
            "agents": agents_by_cat,
            "skills": skills_by_cat,
            "commands": [
                {"name": c.name, "description": c.description, "argument_hint": c.argument_hint}
                for c in self.commands.values()
            ],
        }

    # ── Índice compacto para el system prompt ────────────

    def get_index_prompt(self) -> str:
        """
        Índice ultra-compacto de todos los componentes.
        Se inyecta en el system prompt para que el Router sepa qué hay disponible.
        ~3 tokens por componente = ~360 tokens para los 119.
        """
        lines = ["<available_components>"]

        if self.agents:
            lines.append("AGENTS (especialistas con system prompt propio):")
            for cat, items in self._group_by_category(self.agents).items():
                names = ", ".join(a.name for a in items)
                lines.append(f"  [{cat}]: {names}")

        if self.skills:
            lines.append("\nSKILLS (conocimiento inyectable):")
            for cat, items in self._group_by_category(self.skills).items():
                names = ", ".join(s.name for s in items)
                lines.append(f"  [{cat}]: {names}")

        if self.commands:
            lines.append("\nCOMMANDS (ejecutables con argumentos):")
            for c in self.commands.values():
                lines.append(f"  /{c.name}: {c.description[:60]}")

        lines.append("</available_components>")
        return "\n".join(lines)

    def _group_by_category(self, components: dict) -> dict:
        groups = {}
        for item in components.values():
            groups.setdefault(item.category, []).append(item)
        return groups

    # ── Builder de prompt final ───────────────────────────

    def build_prompt(
        self,
        task: str,
        agent_name: Optional[str] = None,
        skill_names: Optional[list[str]] = None,
        command_name: Optional[str] = None,
        command_args: str = "",
        memory_context: str = "",
    ) -> tuple[str, str, list[str]]:
        """
        Construye (system_prompt, user_prompt, tools) para ejecutar una tarea.

        Returns:
            system_prompt: system prompt del agente + instrucciones adicionales de skills
            user_prompt:   tarea + contexto de memoria + skills + comando si aplica
            tools:         herramientas combinadas de agente + skills
        """
        system_parts = ["Eres CLAUDE-BRAIN, un agente de desarrollo autónomo."]
        user_parts = []
        tools = set()

        # Agente especialista
        if agent_name and (agent := self.get_agent(agent_name)):
            system_parts.append(f"\n## Modo: {agent.name}\n{agent.system_prompt}")
            tools.update(agent.tools)

        # Contexto de memoria
        if memory_context:
            user_parts.append(memory_context)

        # Skills inyectadas
        for name in (skill_names or []):
            if skill := self.get_skill(name):
                user_parts.append(skill.to_prompt_block())
                tools.update(skill.allowed_tools)

        # Comando a ejecutar
        if command_name and (cmd := self.get_command(command_name)):
            user_parts.append(f"<command name='{command_name}'>\n{cmd.render(command_args)}\n</command>")
            tools.update(cmd.allowed_tools)

        user_parts.append(f"<task>\n{task}\n</task>")

        # Filtrar tools a las soportadas por el CLI
        VALID_TOOLS = {
            "Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep",
            "WebSearch", "WebFetch", "TodoRead", "TodoWrite",
        }
        final_tools = [t for t in tools if t in VALID_TOOLS] or list(VALID_TOOLS)

        return (
            "\n\n".join(system_parts),
            "\n\n".join(user_parts),
            final_tools,
        )
