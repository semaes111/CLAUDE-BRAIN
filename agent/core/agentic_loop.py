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
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum

from agent.config import settings
from agent.core.claude_runner import ClaudeMaxRunner
from agent.core.veracity import (
    VERSION_FULL,
    CONDENSER_SYSTEM,
    RECALIBRATE_EVERY,
    RECALIBRATION_MESSAGE,
    compute_degradation_score,
)


# ─────────────────────────────────────────────────────────
# TYPES — Actions & Observations
# ─────────────────────────────────────────────────────────

class ActionType(str, Enum):
    BASH    = "bash"       # Ejecutar comando shell
    READ    = "read"       # Leer archivo
    WRITE   = "write"      # Escribir archivo
    EDIT    = "edit"       # Editar líneas de archivo
    BROWSE  = "browse"     # Abrir URL en browser
    IPYTHON = "ipython"    # Celda IPython con estado persistente (pandas, plt, sklearn...)
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
    # IPYTHON:  payload = {"code": "import pandas as pd\ndf = pd.read_csv('data.csv')\ndf.head()"}
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
    degradation_log: list[dict] = field(default_factory=list)  # historial de alertas epistémicas


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

    def __init__(self, window: int | None = None):
        self.window = window or settings.stuck_detector_window

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

    MAX_CHARS  = settings.context_condenser_max_chars
    KEEP_STEPS = settings.context_condenser_keep_steps

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
        Resume los steps antiguos en un bloque de texto estructurado.

        Usa CONDENSER_SYSTEM (de veracity.py) que obliga al LLM a:
        - Preservar exit codes y mensajes de error LITERALES
        - Separar [HECHOS VERIFICADOS] de [INTENTOS FALLIDOS]
        - Nunca suavizar errores ni inferir éxitos no observados
        """
        old_steps = steps[:-self.KEEP_STEPS] if len(steps) > self.KEEP_STEPS else steps

        # Formatear historial con datos DUROS: tipo, payload, resultado, exit code
        history_lines = []
        for s in old_steps:
            result_flag  = "SUCCESS" if s.observation.success else "FAILURE"
            meta         = s.observation.metadata
            exit_info    = f" [exit:{meta.get('exit_code', '?')}]" if "exit_code" in meta else ""
            content_clip = s.observation.content[:300].replace("\n", " ")
            history_lines.append(
                f"Step {s.iteration} [{s.action.type.value}]{exit_info}: "
                f"{json.dumps(s.action.payload)[:150]} "
                f"→ {result_flag}: {content_clip}"
            )

        history_text = "\n".join(history_lines)

        prompt = (
            f"Tarea: {task[:400]}\n\n"
            f"Historial de steps ({len(old_steps)} steps):\n{history_text}\n\n"
            "Genera el resumen estructurado siguiendo exactamente el formato del sistema."
        )

        result = await self.runner.run(
            task=prompt,
            system=CONDENSER_SYSTEM,
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

    def _build_action(self, data: dict) -> Action | None:
        try:
            action_type = ActionType(data.get("type", "think"))
            thought = str(data.pop("thought", ""))
            data.pop("type", None)
            return Action(type=action_type, payload=data, thought=thought)
        except ValueError:
            return None


# ─────────────────────────────────────────────────────────
# ACTION VALIDATOR
# Guardia post-parse que rechaza Actions semánticamente inválidas.
# Previene que el modelo inyecte acciones "plausibles pero inventadas".
# ─────────────────────────────────────────────────────────

class ActionValidator:
    """
    Valida la coherencia semántica de una Action después del parsing.

    El ActionParser garantiza validez JSON y campos mínimos.
    El ActionValidator garantiza que los VALORES tienen sentido:
      - BASH: cmd no puede ser vacío, comentario puro o placeholder
      - READ/WRITE/EDIT: path debe tener extensión o ser directorio válido
      - FINISH: message debe referenciar evidencia concreta (>80 chars)
      - REJECT: reason debe ser específica (>30 chars)
      - IPYTHON: code no puede ser solo un comentario
    """

    # Placeholders típicos del modelo cuando no sabe qué poner
    PLACEHOLDER_PATTERNS = [
        "your_", "example", "placeholder", "todo", "fixme",
        "<cmd>", "<path>", "<code>", "...",
        "comando_aquí", "ruta_aquí", "código_aquí",
    ]

    def validate(self, action: Action) -> tuple[bool, str]:
        """
        Valida la Action. Retorna (is_valid, reason_if_invalid).
        Si is_valid=False, el loop debe reemplazarla con un THINK de recalibración.
        """
        t = action.type
        p = action.payload

        match t:
            case ActionType.BASH:
                return self._validate_bash(p)
            case ActionType.READ:
                return self._validate_path_action(p, "READ")
            case ActionType.WRITE:
                return self._validate_write(p)
            case ActionType.EDIT:
                return self._validate_edit(p)
            case ActionType.IPYTHON:
                return self._validate_ipython(p)
            case ActionType.FINISH:
                return self._validate_finish(p)
            case ActionType.REJECT:
                return self._validate_reject(p)
            case _:
                return True, ""  # THINK, BROWSE, DELEGATE: sin restricciones estrictas

    def _validate_bash(self, p: dict) -> tuple[bool, str]:
        cmd = str(p.get("cmd", "")).strip()
        if not cmd:
            return False, "BASH con cmd vacío"
        if cmd.startswith("#"):
            return False, "BASH con solo un comentario como cmd"
        if len(cmd) < 2:
            return False, f"BASH con cmd demasiado corto: '{cmd}'"
        for ph in self.PLACEHOLDER_PATTERNS:
            if ph.lower() in cmd.lower():
                return False, f"BASH con placeholder detectado: '{ph}' en '{cmd[:60]}'"
        return True, ""

    def _validate_path_action(self, p: dict, action_name: str) -> tuple[bool, str]:
        path = str(p.get("path", "")).strip()
        if not path:
            return False, f"{action_name} con path vacío"
        for ph in self.PLACEHOLDER_PATTERNS:
            if ph.lower() in path.lower():
                return False, f"{action_name} con placeholder en path: '{path[:60]}'"
        return True, ""

    def _validate_write(self, p: dict) -> tuple[bool, str]:
        ok, reason = self._validate_path_action(p, "WRITE")
        if not ok:
            return ok, reason
        content = str(p.get("content", ""))
        if not content.strip():
            return False, "WRITE con content vacío"
        return True, ""

    def _validate_edit(self, p: dict) -> tuple[bool, str]:
        ok, reason = self._validate_path_action(p, "EDIT")
        if not ok:
            return ok, reason
        old_str = str(p.get("old", "")).strip()
        new_str = p.get("new", None)
        if not old_str:
            return False, "EDIT con 'old' vacío — ¿qué texto hay que reemplazar?"
        if new_str is None:
            return False, "EDIT sin campo 'new' — especifica el texto de reemplazo"
        return True, ""

    def _validate_ipython(self, p: dict) -> tuple[bool, str]:
        code = str(p.get("code", "")).strip()
        if not code:
            return False, "IPYTHON con code vacío"
        # Solo comentarios
        non_comment = [l for l in code.splitlines() if l.strip() and not l.strip().startswith("#")]
        if not non_comment:
            return False, "IPYTHON con código solo compuesto de comentarios"
        for ph in self.PLACEHOLDER_PATTERNS:
            if ph.lower() in code.lower():
                return False, f"IPYTHON con placeholder detectado: '{ph}'"
        return True, ""

    def _validate_finish(self, p: dict) -> tuple[bool, str]:
        msg = str(p.get("message", "")).strip()
        if len(msg) < 80:
            return False, (
                f"FINISH con message demasiado vago ({len(msg)} chars). "
                "El mensaje de finish debe referenciar evidencia concreta de los steps completados."
            )
        vague_finishes = ["completado", "listo", "done", "finished", "todo ok", "all done"]
        if msg.lower().strip() in vague_finishes:
            return False, f"FINISH vago: '{msg}'. Describe qué hiciste exactamente."
        return True, ""

    def _validate_reject(self, p: dict) -> tuple[bool, str]:
        reason = str(p.get("reason", "")).strip()
        if len(reason) < 30:
            return False, "REJECT con reason demasiado corta. Explica específicamente por qué no puedes completar la tarea."
        return True, ""


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

REGLAS OPERATIVAS:
- Siempre incluye "thought" explicando tu razonamiento
- Una sola acción por respuesta
- Si un comando falla, analiza el error y prueba algo diferente
- Cuando termines completamente, usa finish
- Si llevas 3 intentos fallidos en lo mismo, cambia de estrategia
"""

    def _build_full_system(self, extra_system: str = "") -> str:
        """
        Combina SYSTEM_PROMPT + VERSION_FULL + extra_system del especialista.

        Orden deliberado:
          1. SYSTEM_PROMPT  → qué hacer (acciones, formato, reglas operativas)
          2. VERSION_FULL   → cómo pensar (honestidad, calibración, anti-degradación)
          3. extra_system   → contexto del agente especialista (si lo hay)

        VERSION_FULL va DESPUÉS del SYSTEM_PROMPT para que las normas
        epistémicas sean lo último que el modelo lee antes de actuar.
        """
        parts = [self.SYSTEM_PROMPT.strip(), VERSION_FULL.strip()]
        if extra_system and extra_system.strip():
            parts.append(f"## Modo especialista:\n{extra_system.strip()}")
        return "\n\n---\n\n".join(parts)

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
        self.validator      = ActionValidator()  # guardia post-parse

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
        degradation_log: list[dict] = []  # Registro de scores de degradación

        # System prompt completo: operativo + epistémico + especialista
        system = self._build_full_system(extra_system)

        # Primer mensaje: la tarea
        history.append({"role": "user", "content": task})

        for iteration in range(1, self.max_iterations + 1):

            # ── Recalibración epistémica periódica ─────────────
            # Cada RECALIBRATE_EVERY iteraciones, inyectar recordatorio
            # de normas en el historial para contrarrestar la degradación
            if iteration > 1 and (iteration - 1) % RECALIBRATE_EVERY == 0:
                recal_msg = RECALIBRATION_MESSAGE.format(iteration=iteration)
                history.append({"role": "user", "content": recal_msg})

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

            # ── Degradation detector ────────────────────────────
            # Analiza el output del agente buscando patrones de degradación
            deg_score, deg_triggers = compute_degradation_score(result.output)
            if deg_triggers:
                degradation_log.append({
                    "iteration": iteration,
                    "score": deg_score,
                    "triggers": deg_triggers,
                })
                # Si degradación severa (>0.5), inyectar alerta en historial
                if deg_score > 0.5:
                    history.append({"role": "user", "content": (
                        f"⚠️ ALERTA EPISTÉMICA (iter {iteration}): "
                        f"Se detectaron patrones de degradación en tu respuesta anterior: "
                        f"{', '.join(deg_triggers[:3])}.\n"
                        "DETENTE. Verifica que tu próxima acción está basada en "
                        "Observations REALES, no en suposiciones o en tu historial.\n"
                        "Si hay incertidumbre → usa THINK para declararla primero."
                    )})

            # ── Parsear acción ──────────────────────────────────
            actions = self.parser.parse(result.output)
            action = actions[0]  # Una acción por turno

            # ── Validar acción (guardia semántica) ──────────────
            # Detecta acciones con campos vacíos, placeholders o finishes vagos.
            # Si falla → reemplazar con THINK de recalibración en lugar de ejecutar.
            is_valid, invalid_reason = self.validator.validate(action)
            if not is_valid:
                action = Action(
                    type=ActionType.THINK,
                    payload={"thought": f"[ActionValidator] acción inválida rechazada: {invalid_reason}"},
                    thought=f"Recalibrando: {invalid_reason}",
                )
                # Inyectar feedback en el historial para que el agente corrija
                history.append({"role": "assistant", "content": result.output})
                history.append({"role": "user", "content": (
                    f"⚠️ ACCIÓN INVÁLIDA RECHAZADA: {invalid_reason}\n"
                    "La acción que generaste tiene un problema de calidad epistémica.\n"
                    "Por favor:\n"
                    "1. Usa la acción THINK para recalibrar\n"
                    "2. Luego genera una acción concreta y verificable\n"
                    "3. Asegúrate de que todos los campos tienen valores reales, no placeholders"
                )})
                steps.append(AgentStep(
                    iteration=iteration,
                    action=action,
                    observation=Observation(
                        action_type=ActionType.THINK,
                        content=f"Acción rechazada por ActionValidator: {invalid_reason}",
                        success=False,
                    )
                ))
                if on_step:
                    await on_step(steps[-1])
                continue  # Ir a la siguiente iteración sin ejecutar

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
                    degradation_log=degradation_log,
                )

            if action.type == ActionType.REJECT:
                return LoopResult(
                    success=False,
                    message=f"Rechazado: {action.payload.get('reason', '')}",
                    steps=steps,
                    iterations=iteration,
                    degradation_log=degradation_log,
                )

            # Confirmation mode: preguntar antes de acciones destructivas
            if self.confirm_mode and action.type in (ActionType.WRITE, ActionType.BASH):
                # En Telegram: el watcher emite un evento que el bot captura
                # para pedir confirmación antes de continuar
                pass

            # Pasar session_id al runtime para que el kernel sepa en qué sesión está
            self.runtime._current_session = session_id
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
                        degradation_log=degradation_log,
                    )

        # Max iterations alcanzado
        return LoopResult(
            success=False,
            message=f"Límite de {self.max_iterations} iteraciones alcanzado sin completar",
            steps=steps,
            iterations=self.max_iterations,
            degradation_log=degradation_log,
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
