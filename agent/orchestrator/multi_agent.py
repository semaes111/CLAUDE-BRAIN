"""
MultiAgentOrchestrator — Coordinación de subagentes paralelos

Cada subagente = un proceso `claude --print` independiente.
Todos usan Max OAuth → $0 extra por subagente.

Límite recomendado: 3-5 subagentes simultáneos para no agotar
el rate limit del plan Max.

Patrones implementados:
  - simple:   1 agente (tarea directa)
  - parallel: N agentes independientes en paralelo
  - pipeline: N agentes secuenciales (output → input)
"""

import asyncio
import json
from typing import Optional

from agent.core.claude_runner import ClaudeMaxRunner, RunResult


class MultiAgentOrchestrator:

    def __init__(self, runner: ClaudeMaxRunner, max_concurrent: int = 4):
        self.runner = runner
        self.semaphore = asyncio.Semaphore(max_concurrent)

    # ─────────────────────────────────────────────
    # SUBAGENTE INDIVIDUAL
    # ─────────────────────────────────────────────

    async def _run_subagent(
        self,
        agent_id: str,
        task: str,
        tools: Optional[list] = None,
        cwd: Optional[str] = None,
        system: Optional[str] = None,
    ) -> tuple[str, RunResult]:
        """Ejecuta un subagente individual con control de concurrencia."""
        async with self.semaphore:
            result = await self.runner.run_with_tools(
                task=task,
                tools=tools,
                cwd=cwd,
                system=system or f"Eres el subagente '{agent_id}'. Completa tu tarea específica y concisa.",
            )
            return agent_id, result

    # ─────────────────────────────────────────────
    # ESTRATEGIA: PARALLEL
    # ─────────────────────────────────────────────

    async def parallel_execution(
        self,
        subtasks: list[dict],
        synthesize: bool = True,
        original_request: str = "",
    ) -> str:
        """
        Ejecuta múltiples tareas en paralelo.

        subtasks format:
        [
            {"id": "frontend", "task": "...", "tools": ["Write", "Edit"]},
            {"id": "backend",  "task": "...", "tools": ["Write", "Bash"]},
        ]
        """
        tasks = [
            self._run_subagent(
                agent_id=st["id"],
                task=st["task"],
                tools=st.get("tools"),
                cwd=st.get("cwd"),
            )
            for st in subtasks
        ]

        results_raw = await asyncio.gather(*tasks, return_exceptions=True)

        results = {}
        for item in results_raw:
            if isinstance(item, Exception):
                continue
            agent_id, result = item
            results[agent_id] = result

        if not synthesize or not original_request:
            return "\n\n".join(
                f"**[{k}]**\n{v.output}" for k, v in results.items()
            )

        # Síntesis: Claude combina los resultados (usa Max OAuth)
        synthesis_input = "\n\n".join(
            f"=== Subagente '{k}' ===\n{v.output[:2000]}"
            for k, v in results.items()
        )
        synth = await self.runner.run(
            f"""Sintetiza los resultados de los subagentes en una respuesta coherente y completa.

PETICIÓN ORIGINAL: {original_request}

RESULTADOS DE SUBAGENTES:
{synthesis_input}

Proporciona una síntesis clara que integre todos los resultados.
"""
        )
        return synth.output

    # ─────────────────────────────────────────────
    # ESTRATEGIA: PIPELINE
    # ─────────────────────────────────────────────

    async def pipeline_execution(self, stages: list[dict]) -> list[dict]:
        """
        Ejecuta etapas secuencialmente.
        El output de cada etapa alimenta la siguiente.

        stages format:
        [
            {"id": "analysis", "task": "Analiza: {prev_output}"},
            {"id": "design",   "task": "Diseña basándote en: {prev_output}"},
            {"id": "impl",     "task": "Implementa: {prev_output}"},
        ]
        """
        results = []
        prev_output = ""

        for stage in stages:
            task = stage["task"].format(prev_output=prev_output[:3000])
            _, result = await self._run_subagent(
                agent_id=stage["id"],
                task=task,
                tools=stage.get("tools"),
                cwd=stage.get("cwd"),
            )
            prev_output = result.output
            results.append({
                "stage": stage["id"],
                "output": result.output,
                "success": result.success,
            })

        return results

    # ─────────────────────────────────────────────
    # ORQUESTADOR PRINCIPAL
    # ─────────────────────────────────────────────

    async def orchestrate(self, user_request: str) -> str:
        """
        Punto de entrada principal.
        Claude decide la estrategia óptima para la tarea.
        """
        # Fase 1: Planificación (Claude analiza y decide)
        plan_result = await self.runner.run(
            f"""Analiza esta petición y crea un plan de ejecución.
Responde SOLO con JSON válido (sin markdown):

{{
  "strategy": "simple|parallel|pipeline",
  "complexity": "low|medium|high",
  "reasoning": "por qué elegiste esta estrategia",
  "subtasks": [
    {{
      "id": "nombre_corto",
      "task": "descripción detallada de la tarea",
      "tools": ["Read", "Write", "Bash"],
      "depends_on": null
    }}
  ],
  "needs_synthesis": true
}}

Reglas:
- simple: 1 tarea directa (la mayoría de casos)
- parallel: tareas independientes que pueden hacerse a la vez
- pipeline: tareas con dependencias secuenciales claras
- Máximo 4 subtareas en paralelo

PETICIÓN: {user_request}
"""
        )

        # Parsear plan
        try:
            clean_output = plan_result.output.strip()
            if "```" in clean_output:
                clean_output = clean_output.split("```")[1].lstrip("json").strip()
            plan = json.loads(clean_output)
        except (json.JSONDecodeError, Exception):
            # Fallback: ejecución simple directa
            result = await self.runner.run_with_tools(user_request)
            return result.output

        strategy = plan.get("strategy", "simple")
        subtasks = plan.get("subtasks", [])

        # Fase 2: Ejecución según estrategia
        if strategy == "simple" or len(subtasks) <= 1:
            task = subtasks[0]["task"] if subtasks else user_request
            result = await self.runner.run_with_tools(
                task=task,
                tools=subtasks[0].get("tools") if subtasks else None,
            )
            return result.output

        elif strategy == "parallel":
            return await self.parallel_execution(
                subtasks=subtasks,
                synthesize=plan.get("needs_synthesis", True),
                original_request=user_request,
            )

        elif strategy == "pipeline":
            results = await self.pipeline_execution(subtasks)
            return results[-1]["output"] if results else "Sin resultados"

        else:
            result = await self.runner.run_with_tools(user_request)
            return result.output
