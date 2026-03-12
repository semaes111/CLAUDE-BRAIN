"""
ComponentRegistry — Registro unificado de agentes, skills y comandos.

Tipos de componentes:
  Agent   — Especialista con system prompt propio
  Skill   — Conocimiento/guías inyectables (SKILL.md)
  Command — Comandos ejecutables con pasos bash inline
"""

import glob
import os
from pathlib import Path

import yaml

from agent.config import settings
from agent.models import Agent, Command, Skill


VALID_CLI_TOOLS = {
    "Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep",
    "WebSearch", "WebFetch", "TodoRead", "TodoWrite",
}


class ComponentRegistry:
    """
    Registro central de todos los componentes disponibles.
    Soporta búsqueda por nombre y generación de índice para prompts.
    """

    def __init__(self):
        self.agents:   dict[str, Agent]   = {}
        self.skills:   dict[str, Skill]   = {}
        self.commands: dict[str, Command] = {}
        self._scan_all()

    # ── Scanning ─────────────────────────────────────────

    def _scan_all(self):
        dirs = {
            "agents":  settings.components_agents_dir,
            "skills":  settings.components_skills_dir,
            "commands": settings.components_commands_dir,
            "project":  settings.project_skills_dir,
        }
        if os.path.exists(dirs["agents"]):
            self._load_agents(dirs["agents"])
        if os.path.exists(dirs["skills"]):
            self._load_skills(dirs["skills"], source="aitmpl")
        if os.path.exists(dirs["commands"]):
            self._load_commands(dirs["commands"])
        if os.path.exists(dirs["project"]):
            self._load_skills(dirs["project"], source="project")

    def reload(self):
        self.agents.clear()
        self.skills.clear()
        self.commands.clear()
        self._scan_all()

    # ── Parsers ──────────────────────────────────────────

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[dict, str]:
        parts = raw.split("---", 2)
        if len(parts) < 3:
            return {}, raw
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            fm = {}
        return fm, parts[2].strip()

    @staticmethod
    def _parse_tools(tools_raw) -> list[str]:
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
                    system_addition=str(fm.get("x-system-prompt-addition", "")),
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

    # ── API pública ───────────────────────────────────────

    def get_agent(self, name: str) -> Agent | None:
        return self.agents.get(name)

    def get_skill(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def get_command(self, name: str) -> Command | None:
        return self.commands.get(name)

    def summary(self) -> dict:
        return {
            "agents":   len(self.agents),
            "skills":   len(self.skills),
            "commands": len(self.commands),
            "total":    len(self.agents) + len(self.skills) + len(self.commands),
        }

    def catalog(self) -> dict:
        agents_by_cat: dict[str, list] = {}
        for a in self.agents.values():
            agents_by_cat.setdefault(a.category, []).append(
                {"name": a.name, "description": a.description}
            )

        skills_by_cat: dict[str, list] = {}
        for s in self.skills.values():
            skills_by_cat.setdefault(s.category, []).append(
                {"name": s.name, "description": s.description, "source": s.source}
            )

        return {
            "agents": agents_by_cat,
            "skills": skills_by_cat,
            "commands": [
                {"name": c.name, "description": c.description, "argument_hint": c.argument_hint}
                for c in self.commands.values()
            ],
        }

    # ── Índice compacto para prompts ─────────────────────

    def get_index_prompt(self) -> str:
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
        groups: dict[str, list] = {}
        for item in components.values():
            groups.setdefault(item.category, []).append(item)
        return groups

    # ── Builder de prompt final ───────────────────────────

    def build_prompt(
        self,
        task: str,
        agent_name: str | None = None,
        skill_names: list[str] | None = None,
        command_name: str | None = None,
        command_args: str = "",
        memory_context: str = "",
    ) -> tuple[str, str, list[str]]:
        """
        Construye (system_prompt, user_prompt, tools) para ejecutar una tarea.
        """
        system_parts = ["Eres CLAUDE-BRAIN, un agente de desarrollo autónomo."]
        user_parts: list[str] = []
        tools: set[str] = set()

        if agent_name and (agent := self.get_agent(agent_name)):
            system_parts.append(f"\n## Modo: {agent.name}\n{agent.system_prompt}")
            tools.update(agent.tools)

        if memory_context:
            user_parts.append(memory_context)

        for name in (skill_names or []):
            if skill := self.get_skill(name):
                user_parts.append(skill.to_prompt_block())
                tools.update(skill.allowed_tools)

        if command_name and (cmd := self.get_command(command_name)):
            user_parts.append(f"<command name='{command_name}'>\n{cmd.render(command_args)}\n</command>")
            tools.update(cmd.allowed_tools)

        user_parts.append(f"<task>\n{task}\n</task>")

        final_tools = [t for t in tools if t in VALID_CLI_TOOLS] or list(VALID_CLI_TOOLS)

        return (
            "\n\n".join(system_parts),
            "\n\n".join(user_parts),
            final_tools,
        )
