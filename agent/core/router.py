"""
SmartRouter — Enrutamiento automático de tareas al agente/skill correcto

Proceso:
  1. Recibe la tarea del usuario
  2. Claude CLI analiza la tarea (subprocess, $0 extra)
  3. Retorna: {agent, skills, command, reasoning}
  4. El registry construye el prompt final

El Router usa el índice compacto del registry (~360 tokens)
para que Claude conozca los 119 componentes disponibles.
"""

import json
import os
from dataclasses import dataclass

from agent.core.claude_runner import ClaudeMaxRunner
from agent.registry.component_registry import ComponentRegistry


@dataclass
class RouteDecision:
    agent: str | None        # Nombre del agente especialista (o None)
    skills: list[str]        # Skills a activar
    command: str | None      # Comando a ejecutar (o None)
    command_args: str        # Argumentos para el comando
    reasoning: str           # Por qué se tomó esta decisión
    confidence: float        # 0.0 - 1.0


class SmartRouter:
    """
    Analiza la tarea del usuario y selecciona automáticamente
    el agente, skills y comandos más apropiados del registry.
    """

    ROUTER_SYSTEM = """Eres un router inteligente. Tu único trabajo es analizar tareas 
y elegir los componentes correctos del registry disponible.
Responde ÚNICAMENTE con JSON válido, sin markdown, sin explicaciones."""

    def __init__(self, runner: ClaudeMaxRunner, registry: ComponentRegistry):
        self.runner = runner
        self.registry = registry

    async def route(self, task: str) -> RouteDecision:
        """
        Analiza la tarea y retorna la decisión de enrutamiento.
        Si el análisis falla, retorna decisión segura (sin componentes específicos).
        """
        # Construir el prompt de análisis
        index = self.registry.get_index_prompt()

        analysis_prompt = f"""{index}

TAREA DEL USUARIO:
{task[:1500]}

Analiza la tarea y elige los componentes óptimos. Responde SOLO con este JSON:
{{
  "agent": "nombre-del-agente-o-null",
  "skills": ["skill1", "skill2"],
  "command": "nombre-comando-o-null",
  "command_args": "argumentos si aplica",
  "reasoning": "explicación en 1 línea",
  "confidence": 0.85
}}

Reglas:
- agent: elige el especialista más relevante, null si la tarea es general
- skills: 0-3 skills complementarias al agente
- command: solo si la tarea pide exactamente lo que hace el comando
- Si no hay componente claro, retorna todo null/vacío con confidence < 0.5
- Los nombres deben coincidir EXACTAMENTE con los del registry"""

        result = await self.runner.run(
            task=analysis_prompt,
            system=self.ROUTER_SYSTEM,
            timeout=30,  # Análisis rápido
        )

        return self._parse_decision(result.output, task)

    def _parse_decision(self, raw_output: str, task: str) -> RouteDecision:
        """Parsea la decisión del router con validación."""
        try:
            # Limpiar posible markdown
            clean = raw_output.strip()
            if "```" in clean:
                clean = clean.split("```")[1].lstrip("json").strip()
            if clean.startswith("{"):
                data = json.loads(clean)
            else:
                # Buscar JSON en el output
                import re
                match = re.search(r'\{[^}]+\}', clean, re.DOTALL)
                data = json.loads(match.group()) if match else {}
        except (json.JSONDecodeError, Exception):
            return self._fallback_decision(task)

        # Validar que los componentes existen en el registry
        agent = data.get("agent")
        if agent and not self.registry.get_agent(agent):
            agent = None  # No existe, ignorar

        skills = [
            s for s in (data.get("skills") or [])
            if self.registry.get_skill(s)
        ]

        command = data.get("command")
        if command and not self.registry.get_command(command):
            command = None

        return RouteDecision(
            agent=agent,
            skills=skills,
            command=command,
            command_args=str(data.get("command_args", "")),
            reasoning=str(data.get("reasoning", "análisis automático"))[:200],
            confidence=float(data.get("confidence", 0.5)),
        )

    def _fallback_decision(self, task: str) -> RouteDecision:
        """Decisión por defecto basada en keywords simples."""
        task_lower = task.lower()

        # Heurísticas básicas como fallback
        agent = None
        skills = []

        if any(k in task_lower for k in ["python", "fastapi", "django", "flask"]):
            agent = "python-pro" if self.registry.get_agent("python-pro") else None
        elif any(k in task_lower for k in ["next.js", "nextjs", "react", "frontend"]):
            agent = "nextjs-developer" if self.registry.get_agent("nextjs-developer") else None
        elif any(k in task_lower for k in ["sql", "database", "postgres", "supabase"]):
            agent = "database-architect" if self.registry.get_agent("database-architect") else None
        elif any(k in task_lower for k in ["typescript", "javascript"]):
            agent = "typescript-pro" if self.registry.get_agent("typescript-pro") else None

        if any(k in task_lower for k in ["review", "revisar", "audit"]):
            if self.registry.get_skill("clean-code"):
                skills.append("clean-code")

        return RouteDecision(
            agent=agent,
            skills=skills,
            command=None,
            command_args="",
            reasoning="fallback heurístico",
            confidence=0.4,
        )

    def route_sync(self, task: str) -> RouteDecision:
        """Versión síncrona del router para uso en scripts."""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self.route(task))
