"""
SmartRouter — Enrutamiento automático por lenguaje natural

Dos velocidades:
  Fast path (< 5ms)  — reglas semánticas por keywords → cubre ~70% de los casos
  LLM path  (~3s)    — Claude CLI analiza la tarea → para los casos ambiguos

El objetivo es que el usuario nunca tenga que mencionar un agente ni una skill.
Escribe "arregla este bug de TypeScript" y el router elige typescript-pro + systematic-debugging.
"""

import json
import re
from dataclasses import dataclass, field

from agent.core.claude_runner import ClaudeMaxRunner
from agent.registry.component_registry import ComponentRegistry


@dataclass
class RouteDecision:
    agent:        str | None    = None
    skills:       list[str]     = field(default_factory=list)
    command:      str | None    = None
    command_args: str           = ""
    reasoning:    str           = ""
    confidence:   float         = 0.0
    fast_path:    bool          = False   # True = heurístico, False = LLM


# ── Tabla de reglas de enrutamiento rápido ───────────────────
# (keywords → {agent, skills})
# Se evalúa en orden, primer match gana

FAST_RULES: list[dict] = [
    # Revisión / auditoría
    {
        "keywords": ["review", "revisa", "audit", "audita", "analiza", "código", "code"],
        "anti":     ["crea", "genera", "escribe", "build"],
        "agent":    "code-reviewer",
        "skills":   ["clean-code", "security-compliance"],
        "command":  "code-review",
    },
    # Seguridad
    {
        "keywords": ["seguridad", "security", "vulnerab", "exploit", "injection", "xss", "csrf"],
        "agent":    "code-reviewer",
        "skills":   ["security-compliance"],
        "command":  "security-audit",
    },
    # Debugging
    {
        "keywords": ["bug", "error", "falla", "crash", "exception", "debug", "arregla", "fix"],
        "agent":    "debugger",
        "skills":   ["systematic-debugging"],
        "command":  "debug-error",
    },
    # Tests
    {
        "keywords": ["test", "tests", "testing", "unittest", "pytest", "jest", "spec"],
        "agent":    "code-reviewer",
        "skills":   ["best-practices"],
        "command":  "generate-tests",
    },
    # Refactoring
    {
        "keywords": ["refactor", "refactoriza", "limpia", "clean", "simplifica", "mejora la estructura"],
        "agent":    "refactoring-specialist",
        "skills":   ["clean-code", "software-architecture"],
        "command":  "refactor-code",
    },
    # Performance
    {
        "keywords": ["lento", "slow", "performance", "optimiza", "optimizar", "velocidad", "benchmark"],
        "agent":    "performance-engineer",
        "skills":   ["performance"],
        "command":  "performance-audit",
    },
    # Next.js
    {
        "keywords": ["next.js", "nextjs", "next ", "app router", "server component", "vercel"],
        "agent":    "nextjs-developer",
        "skills":   ["nextjs-best-practices", "nextjs-supabase-auth"],
    },
    # React
    {
        "keywords": ["react", "componente", "component", "hook", "useState", "useEffect", "jsx", "tsx"],
        "anti":     ["next.js", "nextjs"],
        "agent":    "react-specialist",
        "skills":   ["react-best-practices", "react-patterns"],
    },
    # TypeScript
    {
        "keywords": ["typescript", "ts ", ".ts", "tipos", "types", "interface", "generic"],
        "agent":    "typescript-pro",
        "skills":   ["typescript-expert"],
    },
    # Python
    {
        "keywords": ["python", "fastapi", "django", "flask", "pydantic", "asyncio", ".py"],
        "agent":    "python-pro",
        "skills":   ["python-patterns"],
    },
    # SQL / Postgres
    {
        "keywords": ["sql", "query", "postgres", "postgresql", "supabase", "rls", "migration"],
        "agent":    "postgres-pro",
        "skills":   ["supabase-postgres-best-practices"],
        "command":  "optimize-database",
    },
    # Base de datos / esquemas
    {
        "keywords": ["schema", "esquema", "tabla", "tabla", "índice", "index", "relación", "foreign key"],
        "agent":    "database-architect",
        "skills":   ["supabase-postgres-best-practices"],
    },
    # Data / análisis
    {
        "keywords": ["datos", "data", "análisis", "pandas", "numpy", "dataframe", "csv", "excel"],
        "agent":    "data-analyst",
        "skills":   ["senior-data-scientist"],
    },
    # Mobile / Flutter / Swift
    {
        "keywords": ["flutter", "dart", "mobile", "android", "ios", "swift", "kotlin", "app móvil"],
        "agent":    "mobile-developer",
    },
    # Frontend / UI
    {
        "keywords": ["ui", "ux", "diseño", "design", "css", "tailwind", "estilo", "animación"],
        "agent":    "ui-ux-designer",
        "skills":   ["tailwind-patterns", "frontend-design"],
    },
    # Backend / API
    {
        "keywords": ["api", "endpoint", "rest", "graphql", "backend", "servidor", "microservicio"],
        "agent":    "backend-developer",
        "skills":   ["backend-dev-guidelines", "api-patterns"],
    },
    # Arquitectura
    {
        "keywords": ["arquitectura", "architecture", "diseño de sistema", "system design", "escalab"],
        "agent":    "backend-architect",
        "skills":   ["software-architecture"],
        "command":  "architecture-explorer",
    },
    # Documentación
    {
        "keywords": ["documenta", "documentación", "docs", "readme", "jsdoc", "openapi"],
        "agent":    "fullstack-developer",
        "command":  "generate-api-docs",
    },
    # JavaScript / Node
    {
        "keywords": ["javascript", "node", "nodejs", "npm", "yarn", "express", ".js"],
        "anti":     ["react", "next"],
        "agent":    "javascript-pro",
        "skills":   ["nodejs-best-practices"],
    },
    # Fullstack general
    {
        "keywords": ["fullstack", "full stack", "aplicación completa", "app completa", "crea una app"],
        "agent":    "fullstack-developer",
        "skills":   ["senior-fullstack"],
    },
]


class SmartRouter:
    def __init__(self, runner: ClaudeMaxRunner, registry: ComponentRegistry):
        self.runner   = runner
        self.registry = registry

    async def route(self, task: str) -> RouteDecision:
        """
        Enruta la tarea al componente correcto.
        Primero intenta el fast path; si no hay match claro, usa el LLM.
        """
        decision = self._fast_route(task)

        # Si el fast path tiene buena confianza y los componentes existen → usarlo
        if decision and decision.confidence >= 0.7:
            return decision

        # LLM path para casos ambiguos
        return await self._llm_route(task, fast_hint=decision)

    def _fast_route(self, task: str) -> RouteDecision | None:
        """Enrutamiento por reglas semánticas (<5ms)."""
        task_lower = task.lower()

        for rule in FAST_RULES:
            keywords = rule.get("keywords", [])
            anti     = rule.get("anti", [])

            # Verificar que hay al menos un keyword match
            if not any(k in task_lower for k in keywords):
                continue

            # Verificar que no hay keywords negativos
            if any(k in task_lower for k in anti):
                continue

            # Validar que los componentes existen en el registry
            agent = rule.get("agent")
            if agent and not self.registry.get_agent(agent):
                agent = None  # Descartar si no está descargado

            skills = [s for s in rule.get("skills", []) if self.registry.get_skill(s)]
            command = rule.get("command")
            if command and not self.registry.get_command(command):
                command = None

            # Calcular confianza basada en cuántos keywords matchean
            matched = sum(1 for k in keywords if k in task_lower)
            confidence = min(0.6 + (matched / len(keywords)) * 0.35, 0.95)

            return RouteDecision(
                agent=agent,
                skills=skills[:2],  # Máximo 2 skills
                command=command,
                reasoning=f"fast-path: keywords {[k for k in keywords if k in task_lower][:3]}",
                confidence=confidence,
                fast_path=True,
            )

        return None  # Sin match → necesita LLM

    async def _llm_route(self, task: str, fast_hint: RouteDecision | None = None) -> RouteDecision:
        """Análisis LLM para tareas ambiguas."""
        index = self.registry.get_index_prompt()
        hint_text = ""
        if fast_hint and fast_hint.agent:
            hint_text = f"\nHint del fast-path: agent={fast_hint.agent}, skills={fast_hint.skills}"

        prompt = f"""{index}
{hint_text}

TAREA: {task[:1000]}

Elige los componentes más apropiados. Responde SOLO JSON:
{{"agent":"nombre-o-null","skills":["s1","s2"],"command":"nombre-o-null","command_args":"","reasoning":"1 línea","confidence":0.9}}

Máximo 2 skills. Nombres exactos del registry."""

        result = await self.runner.run(
            task=prompt,
            system="Eres un router. Responde SOLO JSON válido, sin markdown.",
            timeout=25,
        )

        return self._parse(result.output, task)

    def _parse(self, raw: str, task: str) -> RouteDecision:
        try:
            clean = raw.strip()
            if "```" in clean:
                clean = clean.split("```")[1].lstrip("json").strip()
            m = re.search(r'\{.*\}', clean, re.DOTALL)
            data = json.loads(m.group() if m else clean)
        except Exception:
            return RouteDecision(reasoning="sin match", confidence=0.3)

        agent  = data.get("agent")
        skills = [s for s in (data.get("skills") or []) if self.registry.get_skill(s)]
        command = data.get("command")

        if agent and not self.registry.get_agent(agent): agent = None
        if command and not self.registry.get_command(command): command = None

        return RouteDecision(
            agent=agent, skills=skills, command=command,
            command_args=str(data.get("command_args", "")),
            reasoning=str(data.get("reasoning", "llm-path"))[:200],
            confidence=float(data.get("confidence", 0.7)),
        )
