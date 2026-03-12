"""
AgenticLoop — El motor central que OpenHands llama "CodeAct agent"

Diferencia crítica con el ClaudeMaxRunner actual:
  ANTES (single-shot):  tarea → claude --print → respuesta (1 turno)
  AHORA (agentic loop): tarea → [acción → observación → acción → ...] → finish

Ciclo:
  1. El agente recibe la tarea
  2. Piensa y emite una Action (BashAction | FileAction | BrowseAction | ThinkAction | FinishAction)
  3. El runtime ejecuta la Action y retorna una Observation
  4. La Observation se añade al historial y el agente decide el siguiente paso
  5. Repetir hasta AgentFinishAction o max_iterations

Esto es lo que permite que Claude resuelva GitHub issues completos,
construya apps de 0, haga debug iterativo — todo autónomo.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncGenerator, Optional

from agent.core.claude_runner import ClaudeMaxRunner


# ─────────────────────────────────────────────────────────
# TYPES — Actions & Observations
# ─────────────────────────────────────────────────────────

class ActionType(str, Enum):
    BASH    = "bash"       # Ejecutar comando shell
    READ    = "read"       # Leer archivo
    WRITE   = "write"      # Escribir archivo
    EDIT    = "edit"       # Editar líneas de archivo
    BROWSE  = "browse"     # Abrir URL en browser
    THINK   = "think"      # Pensamiento interno (no ejecuta nada)
    FINISH  = "finish"     # Tarea completada
    REJECT  = "reject"     # Tarea rechazada (imposible o fuera de scope)
    DELEGATE = "delegate"  # Delegar a subagente especialista


@dataclass
class Action:
    type:    ActionType
    payload: dict = field(default_factory=dict)
    thought: str  = ""

    # Campos por tipo:
    # BASH:     payload = {"cmd": "npm test", "timeout": 30}
    # READ:     payload = {"path": "src/app.py", "start": 1, "end": 50}
    # WRITE:    payload = {"path": "src/app.py", "content": "..."}
    # EDIT:     payload = {"path": "src/app.py", "old": "...", "new": "..."}
    # BROWSE:   payload = {"url": "https://...", "action": "goto|click|type|scroll"}
    # THINK:    payload = {"thought": "debo analizar..."}
    # FINISH:   payload = {"message": "Completado: ...", "outputs": {}}
    # REJECT:   payload = {"reason": "No puedo..."}
    # DELEGATE: payload = {"agent": "python-pro", "task": "..."}


@dataclass
class Observation:
    action_type: ActionType
    content:     str
    success:     bool = True
    metadata:    dict = field(default_factory=dict)

    # content es el output de ejecutar la Action:
    # BASH:   stdout + stderr del comando
    # READ:   contenido del archivo
    # WRITE:  confirmación de escritura
    # BROWSE: HTML → texto del DOM
    # THINK:  vacío (el pensamiento es interno)


@dataclass
class AgentStep:
    iteration: int
    action:    Action
    observation: Observation
    timestamp: float = field(default_factory=time.time)


@dataclass
class LoopResult:
    success:    bool
    message:    str
    steps:      list[AgentStep]
    iterations: int
    stuck:      bool = False
    outputs:    dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────
# STUCK DETECTOR
# Detecta cuando el agente repite las mismas acciones (loop infinito)
# Implementación inspirada en OpenHands StuckDetector
# ─────────────────────────────────────────────────────────

class StuckDetector:
    """
    Detecta patrones de repetición en las acciones del agente.

    Casos detectados:
    - Mismo comando bash ejecutado 3+ veces seguidas
    - Mismo archivo leído N veces sin modificación
    - Ciclo A→B→A→B de longitud 2-4
    - Error idéntico recibido 3+ veces
    """

    def __init__(self, window: int = 6):
        self.window = window  # cuántos steps mirar hacia atrás

    def is_stuck(self, steps: list[AgentStep]) -> tuple[bool, str]:
        if len(steps) < 3:
            return False, ""

        recent = steps[-self.window:]

        # 1. Mismo bash cmd repetido
        bash_cmds = [
            s.action.payload.get("cmd", "")
            for s in recent if s.action.type == ActionType.BASH
        ]
        if len(bash_cmds) >= 3:
            if len(set(bash_cmds[-3:])) == 1:
                return True, f"loop: mismo comando '{bash_cmds[-1][:50]}' x3"

        # 2. Mismo error recibido repetidamente
        errors = [
            s.observation.content[:100]
            for s in recent if not s.observation.success
        ]
        if len(errors) >= 3 and len(set(errors[-3:])) == 1:
            return True, f"loop: mismo error repetido x3"

        # 3. Ciclo A→B→A→B
        if len(recent) >= 4:
            actions = [self._action_hash(s.action) for s in recent[-4:]]
            if actions[0] == actions[2] and actions[1] == actions[3]:
                return True, "loop: ciclo A→B→A→B detectado"

        return False, ""

    def _action_hash(self, action: Action) -> str:
        key = f"{action.type}:{json.dumps(action.payload, sort_keys=True)[:100]}"
        return hashlib.md5(key.encode()).hexdigest()[:8]


# ─────────────────────────────────────────────────────────
# CONTEXT CONDENSER
# Resume el historial cuando se acerca al límite del context window
# ─────────────────────────────────────────────────────────

class ContextCondenser:
    """
    Cuando el historial supera MAX_TOKENS, resume los steps más antiguos.
    Mantiene siempre los últimos N steps completos.
    """

    MAX_CHARS  = 80_000   # ~20k tokens — umbral para condensar
    KEEP_STEPS = 4        # últimos N steps siempre completos

    def __init__(self, runner: ClaudeMaxRunner):
        self.runner = runner

    def needs_condensation(self, steps: list[AgentStep], task: str) -> bool:
        total = len(task) + sum(
            len(s.action.thought) + len(json.dumps(s.action.payload)) +
            len(s.observation.content)
            for s in steps
        )
        return total > self.MAX_CHARS

    async def condense(self, steps: list[AgentStep], task: str) -> str:
        """
        Resume los steps antiguos en un bloque de texto compacto.
        Retorna el resumen para usar como contexto del agente.
        """
        old_steps = steps[:-self.KEEP_STEPS] if len(steps) > self.KEEP_STEPS else steps

        history_text = "\n".join(
            f"Step {s.iteration}: [{s.action.type}] {json.dumps(s.action.payload)[:200]}"
            f" → {'OK' if s.observation.success else 'ERROR'}: {s.observation.content[:200]}"
            for s in old_steps
        )

        prompt = (
            f"Tarea original: {task[:500]}\n\n"
            f"Historial a resumir:\n{history_text}\n\n"
            "Resume en máximo 500 palabras qué se ha hecho, qué ha funcionado, "
            "qué ha fallado, y cuál es el estado actual. "
            "Sé concreto con nombres de archivos, comandos y errores."
        )

        result = await self.runner.run(
            task=prompt,
            system="Eres un resumidor técnico. Responde solo con el resumen, sin preámbulo.",
            timeout=60,
        )
        return result.output


# ─────────────────────────────────────────────────────────
# TASK TRACKER
# Lista estructurada de subtareas (equivalente al TaskTrackingAction de OpenHands)
# ─────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING    = "pending"
    IN_PROGRESS = "in_progress"
    DONE       = "done"
    FAILED     = "failed"
    SKIPPED    = "skipped"


@dataclass
class Task:
    id:       int
    title:    str
    status:   TaskStatus = TaskStatus.PENDING
    notes:    str = ""
    subtasks: list["Task"] = field(default_factory=list)

    def to_markdown(self, indent: int = 0) -> str:
        icon = {"pending": "⬜", "in_progress": "🔄", "done": "✅",
                "failed": "❌", "skipped": "⏭️"}.get(self.status.value, "⬜")
        prefix = "  " * indent
        lines = [f"{prefix}{icon} {self.title}"]
        if self.notes:
            lines.append(f"{prefix}   ↳ {self.notes}")
        for sub in self.subtasks:
            lines.append(sub.to_markdown(indent + 1))
        return "\n".join(lines)


class TaskTracker:
    def __init__(self):
        self.tasks: list[Task] = []
        self._next_id = 1

    def add(self, title: str, subtasks: list[str] = None) -> Task:
        task = Task(id=self._next_id, title=title)
        self._next_id += 1
        if subtasks:
            for sub in subtasks:
                task.subtasks.append(Task(id=self._next_id, title=sub))
                self._next_id += 1
        self.tasks.append(task)
        return task

    def update(self, task_id: int, status: TaskStatus, notes: str = ""):
        for task in self.tasks:
            if task.id == task_id:
                task.status = status
                task.notes = notes
                return
            for sub in task.subtasks:
                if sub.id == task_id:
                    sub.status = status
                    sub.notes = notes
                    return

    def to_markdown(self) -> str:
        if not self.tasks:
            return "_Sin tareas definidas_"
        return "\n".join(t.to_markdown() for t in self.tasks)

    def pending_count(self) -> int:
        count = 0
        for t in self.tasks:
            if t.status == TaskStatus.PENDING:
                count += 1
            count += sum(1 for s in t.subtasks if s.status == TaskStatus.PENDING)
        return count


# ─────────────────────────────────────────────────────────
# ACTION PARSER
# Parsea la respuesta del agente (JSON o markdown con tool calls)
# ─────────────────────────────────────────────────────────

class ActionParser:
    """
    Claude devuelve acciones en formato JSON dentro de su respuesta.
    El agente recibe instrucciones de responder con:
    
    <action>
    {"type": "bash", "cmd": "npm test", "thought": "voy a ejecutar los tests"}
    </action>
    
    O múltiples acciones en secuencia.
    """

    def parse(self, text: str) -> list[Action]:
        import re
        actions = []

        # Buscar bloques <action>...</action>
        pattern = re.compile(r'<action>\s*(.*?)\s*</action>', re.DOTALL)
        matches = pattern.findall(text)

        for match in matches:
            try:
                data = json.loads(match)
                action = self._build_action(data)
                if action:
                    actions.append(action)
            except json.JSONDecodeError:
                pass

        # Fallback: buscar JSON raw si no hay tags
        if not actions:
            json_pattern = re.compile(r'\{[^{}]*"type"\s*:\s*"(bash|read|write|edit|browse|think|finish|reject|delegate)"[^{}]*\}', re.DOTALL)
            for match in json_pattern.finditer(text):
                try:
                    data = json.loads(match.group())
                    action = self._build_action(data)
                    if action:
                        actions.append(action)
                        break  # Solo primer match en fallback
                except Exception:
                    pass

        # Si no hay acción parseada → THINK (el agente está procesando)
        if not actions:
            actions.append(Action(
                type=ActionType.THINK,
                payload={"thought": text[:500]},
                thought=text[:200],
            ))

        return actions

    def _build_action(self, data: dict) -> Optional[Action]:
        try:
            action_type = ActionType(data.get("type", "think"))
            thought = str(data.pop("thought", ""))
            data.pop("type", None)
            return Action(type=action_type, payload=data, thought=thought)
        except ValueError:
            return None


# ─────────────────────────────────────────────────────────
# AGENTIC LOOP — El motor principal
# ─────────────────────────────────────────────────────────

class AgenticLoop:
    """
    Motor de ejecución multi-turno.
    
    Diferencia vs single-shot:
      Single-shot: tarea → 1 llamada → respuesta
      Agentic:     tarea → [acción → observación] * N → resultado final
    
    El agente puede ejecutar comandos, leer/escribir archivos, navegar la web,
    delegar a especialistas — todo en bucle hasta completar la tarea.
    """

    SYSTEM_PROMPT = """Eres CLAUDE-BRAIN, un agente de software autónomo.

Resuelves tareas complejas ejecutando acciones iterativamente.
Cada respuesta DEBE incluir exactamente UNA acción en formato:

<action>
{"type": "TIPO", ...campos, "thought": "por qué hago esto"}
</action>

TIPOS DE ACCIÓN disponibles:

bash — ejecutar comando shell:
<action>
{"type": "bash", "cmd": "ls -la src/", "timeout": 30, "thought": "veo la estructura"}
</action>

read — leer archivo:
<action>
{"type": "read", "path": "src/app.py", "start": 1, "end": 100, "thought": "necesito ver el código"}
</action>

write — crear/sobreescribir archivo:
<action>
{"type": "write", "path": "src/fix.py", "content": "código aquí", "thought": "creo el archivo"}
</action>

edit — editar fragmento de archivo:
<action>
{"type": "edit", "path": "src/app.py", "old": "código viejo", "new": "código nuevo", "thought": "corrijo el bug"}
</action>

browse — navegar web:
<action>
{"type": "browse", "url": "https://docs.example.com", "thought": "busco la documentación"}
</action>

think — razonar sin ejecutar:
<action>
{"type": "think", "thought": "necesito analizar el error antes de actuar"}
</action>

delegate — delegar subtarea a especialista:
<action>
{"type": "delegate", "agent": "python-pro", "task": "optimiza esta función", "thought": "necesito un experto en Python"}
</action>

finish — tarea completada:
<action>
{"type": "finish", "message": "He completado X. Los cambios son: ...", "thought": "todo listo"}
</action>

reject — tarea imposible:
<action>
{"type": "reject", "reason": "No puedo hacer X porque...", "thought": "fuera de mis capacidades"}
</action>

REGLAS:
- Siempre incluye "thought" explicando tu razonamiento
- Una sola acción por respuesta
- Si un comando falla, analiza el error y prueba algo diferente
- Cuando termines completamente, usa finish
- Si llevas 3 intentos fallidos en lo mismo, cambia de estrategia
"""

    def __init__(
        self,
        runner:        ClaudeMaxRunner,
        runtime:       "RuntimeExecutor",
        max_iterations: int = 30,
        confirm_mode:  bool = False,
    ):
        self.runner         = runner
        self.runtime        = runtime
        self.max_iterations = max_iterations
        self.confirm_mode   = confirm_mode
        self.stuck_detector = StuckDetector()
        self.condenser      = ContextCondenser(runner)
        self.parser         = ActionParser()

    async def run(
        self,
        task:       str,
        session_id: str = "default",
        cwd:        str = "/workspaces",
        extra_system: str = "",
        on_step=None,  # callback(step: AgentStep) para streaming
    ) -> LoopResult:
        """
        Ejecuta el loop completo.
        
        Args:
            task:         Tarea en lenguaje natural
            session_id:   ID de sesión para persistencia
            cwd:          Directorio de trabajo
            extra_system: System prompt adicional (de agente especialista)
            on_step:      Callback async para streaming de pasos
        """
        steps:    list[AgentStep] = []
        tracker = TaskTracker()
        history: list[dict] = []  # Historial multi-turno para Claude

        system = self.SYSTEM_PROMPT
        if extra_system:
            system += f"\n\n## Modo especialista:\n{extra_system}"

        # Primer mensaje: la tarea
        history.append({"role": "user", "content": task})

        for iteration in range(1, self.max_iterations + 1):

            # ── Condensar si el context es demasiado largo ──────
            if self.condenser.needs_condensation(steps, task):
                summary = await self.condenser.condense(steps, task)
                # Reemplazar history antigua con el resumen
                history = [
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": f"[HISTORIAL CONDENSADO]\n{summary}"},
                ] + history[-4:]  # mantener últimos 4 turnos

            # ── Llamar al agente ────────────────────────────────
            messages_text = "\n\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in history
            )

            result = await self.runner.run(
                task=messages_text,
                system=system,
                timeout=120,
            )

            if not result.success:
                return LoopResult(
                    success=False,
                    message=f"Error del runner: {result.output}",
                    steps=steps,
                    iterations=iteration,
                )

            # ── Parsear acción ──────────────────────────────────
            actions = self.parser.parse(result.output)
            action = actions[0]  # Una acción por turno

            # Añadir respuesta del agente al historial
            history.append({"role": "assistant", "content": result.output})

            # ── Ejecutar acción ─────────────────────────────────
            if action.type == ActionType.FINISH:
                return LoopResult(
                    success=True,
                    message=action.payload.get("message", "Tarea completada"),
                    steps=steps,
                    iterations=iteration,
                    outputs=action.payload.get("outputs", {}),
                )

            if action.type == ActionType.REJECT:
                return LoopResult(
                    success=False,
                    message=f"Rechazado: {action.payload.get('reason', '')}",
                    steps=steps,
                    iterations=iteration,
                )

            # Confirmation mode: preguntar antes de acciones destructivas
            if self.confirm_mode and action.type in (ActionType.WRITE, ActionType.BASH):
                # En Telegram: el watcher emite un evento que el bot captura
                # para pedir confirmación antes de continuar
                pass

            observation = await self.runtime.execute(action, cwd=cwd)

            step = AgentStep(
                iteration=iteration,
                action=action,
                observation=observation,
            )
            steps.append(step)

            # Callback de streaming
            if on_step:
                await on_step(step)

            # Añadir observación al historial
            obs_text = (
                f"[OBSERVATION - {action.type.value}]\n"
                f"{'SUCCESS' if observation.success else 'ERROR'}\n"
                f"{observation.content[:3000]}"
            )
            history.append({"role": "user", "content": obs_text})

            # ── Detector de loops ───────────────────────────────
            stuck, reason = self.stuck_detector.is_stuck(steps)
            if stuck:
                # Inyectar instrucción de recuperación
                history.append({"role": "user", "content": (
                    f"⚠️ LOOP DETECTADO: {reason}\n"
                    "Estás repitiendo las mismas acciones. CAMBIA de estrategia:\n"
                    "- Intenta un enfoque completamente diferente\n"
                    "- Si algo no funciona, admítelo y busca alternativa\n"
                    "- Si la tarea es imposible, usa la acción reject"
                )})
                if len([s for s in steps if s.observation.success is False]) > self.max_iterations // 2:
                    return LoopResult(
                        success=False,
                        message=f"Agente atascado: {reason}",
                        steps=steps,
                        iterations=iteration,
                        stuck=True,
                    )

        # Max iterations alcanzado
        return LoopResult(
            success=False,
            message=f"Límite de {self.max_iterations} iteraciones alcanzado sin completar",
            steps=steps,
            iterations=self.max_iterations,
        )

    async def stream(
        self,
        task: str,
        session_id: str = "default",
        cwd: str = "/workspaces",
    ) -> AsyncGenerator[dict, None]:
        """Versión streaming del loop — yield de cada step."""
        steps_buffer = []

        async def on_step(step: AgentStep):
            steps_buffer.append(step)

        # Ejecutar en background
        loop_task = asyncio.create_task(
            self.run(task, session_id, cwd, on_step=on_step)
        )

        last_len = 0
        while not loop_task.done():
            await asyncio.sleep(0.2)
            while len(steps_buffer) > last_len:
                step = steps_buffer[last_len]
                yield {
                    "type": "step",
                    "iteration": step.iteration,
                    "action_type": step.action.type.value,
                    "thought": step.action.thought,
                    "payload_preview": str(step.action.payload)[:200],
                    "obs_success": step.observation.success,
                    "obs_preview": step.observation.content[:300],
                }
                last_len += 1

        result = loop_task.result()
        yield {
            "type": "finish",
            "success": result.success,
            "message": result.message,
            "iterations": result.iterations,
            "stuck": result.stuck,
        }
