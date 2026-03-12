"""
JupyterKernelManager — Kernels IPython con estado persistente por sesión

Arquitectura exacta de OpenHands:
  cb-jupyter (Docker) ─── Jupyter Server (puerto 8888)
       │                        │
       │   POST /api/kernels    │  ← crear kernel
       │   DELETE /api/kernels  │  ← matar kernel
       │                        │
       │   WS /api/kernels/{id}/channels  ← ejecutar celdas
       │        ↓
       │   Protocolo Jupyter Messaging Protocol v5
       │   execute_request → {stream, execute_result, display_data, error, execute_reply}
       │
  JupyterKernelManager
       │
       ├── kernels: dict[session_id → KernelSession]
       │        estado: variables, imports, plots — persiste entre celdas
       │
       └── execute(session, code) → CellResult(text, images, error, exec_count)

Diferencia con bash_sandbox:
  bash:   cada llamada es un proceso nuevo, sin estado previo
  kernel: un proceso Python vivo, las variables de la celda anterior están disponibles

Esto permite:
  celda 1: df = pd.read_csv('data.csv')
  celda 2: df.describe()          ← df sigue disponible
  celda 3: plt.plot(df['x'])      ← genera imagen PNG → Telegram
  celda 4: model.fit(X, y)        ← model sigue disponible para predicciones
"""

import asyncio
import base64
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import httpx

# ─────────────────────────────────────────────────────────
# MODELOS
# ─────────────────────────────────────────────────────────

@dataclass
class CellResult:
    """Output completo de ejecutar una celda IPython."""
    text:        str              # stdout + stderr + repr del resultado
    images:      list[str]        # lista de base64 PNG (matplotlib, etc.)
    error:       Optional[str]    # traceback si hubo excepción
    exec_count:  int              # número de ejecución (In [N])
    success:     bool
    duration_ms: int = 0

    def to_observation_text(self) -> str:
        """Texto para inyectar como Observation en el AgenticLoop."""
        parts = []
        if self.text.strip():
            parts.append(self.text)
        if self.images:
            parts.append(f"[{len(self.images)} imagen(es) generada(s)]")
        if self.error:
            parts.append(f"ERROR:\n{self.error}")
        if not parts:
            parts.append("[Celda ejecutada sin output]")
        return f"In [{self.exec_count}]:\n" + "\n".join(parts)


@dataclass
class KernelSession:
    """Sesión de kernel Jupyter para una conversación."""
    session_id:  str
    kernel_id:   str
    ws_url:      str
    created_at:  float = field(default_factory=time.time)
    exec_count:  int = 0
    last_used:   float = field(default_factory=time.time)
    _ws = None  # websocket connection


# ─────────────────────────────────────────────────────────
# MANAGER PRINCIPAL
# ─────────────────────────────────────────────────────────

class JupyterKernelManager:
    """
    Gestiona kernels Python con estado persistente por sesión.
    Un kernel por sesión de conversación.
    
    Protocolo: Jupyter Messaging Protocol v5
    https://jupyter-client.readthedocs.io/en/latest/messaging.html
    """

    JUPYTER_URL = os.getenv("JUPYTER_URL", "http://jupyter:8888")
    JUPYTER_TOKEN = os.getenv("JUPYTER_TOKEN", "claude-brain-jupyter-token")

    # Tiempo máximo de inactividad antes de matar el kernel (30 min)
    KERNEL_TTL = 1800

    # Setup code que se ejecuta al crear cada kernel
    KERNEL_INIT_CODE = '''
import warnings
warnings.filterwarnings('ignore')

import sys, os

# Científicas
import numpy as np
import pandas as pd
import json, re, pathlib
from pathlib import Path

# Matplotlib con backend no-display (para generar imágenes)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# Seaborn opcional
try:
    import seaborn as sns
    sns.set_theme(style="whitegrid", palette="husl")
except ImportError:
    pass

# ML opcional
try:
    import sklearn
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report
except ImportError:
    pass

# Ajustar display de pandas
pd.set_option('display.max_columns', 20)
pd.set_option('display.max_rows', 50)
pd.set_option('display.width', 120)
pd.set_option('display.float_format', '{:.4f}'.format)

# Directorio de trabajo
os.chdir('/workspaces')
print(f"✅ Kernel inicializado | Python {sys.version.split()[0]} | numpy {np.__version__} | pandas {pd.__version__}")
'''.strip()

    def __init__(self):
        self._kernels: dict[str, KernelSession] = {}
        self._headers = {
            "Authorization": f"Token {self.JUPYTER_TOKEN}",
            "Content-Type": "application/json",
        }
        # Task de limpieza de kernels inactivos
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """Inicia el manager y la tarea de limpieza."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        """Detiene el manager y mata todos los kernels."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        for session in list(self._kernels.values()):
            await self._kill_kernel(session.kernel_id)
        self._kernels.clear()

    # ─────────────────────────────────────────────
    # API PÚBLICA
    # ─────────────────────────────────────────────

    async def execute(
        self,
        session_id: str,
        code: str,
        timeout: int = 120,
    ) -> CellResult:
        """
        Ejecuta código en el kernel de la sesión.
        Si no hay kernel para la sesión, lo crea automáticamente.
        El estado (variables, imports) persiste entre llamadas.
        """
        t0 = time.time()

        session = await self._get_or_create_kernel(session_id)
        session.last_used = time.time()

        try:
            result = await asyncio.wait_for(
                self._execute_via_websocket(session, code),
                timeout=timeout,
            )
            result.duration_ms = int((time.time() - t0) * 1000)
            session.exec_count += 1
            result.exec_count = session.exec_count
            return result
        except asyncio.TimeoutError:
            # Interrumpir kernel
            await self._interrupt_kernel(session.kernel_id)
            return CellResult(
                text=f"⏱️ Timeout ({timeout}s). El kernel fue interrumpido.",
                images=[], error=None,
                exec_count=session.exec_count,
                success=False,
                duration_ms=timeout * 1000,
            )
        except Exception as e:
            return CellResult(
                text="", images=[],
                error=f"Error de conexión con el kernel: {e}",
                exec_count=session.exec_count,
                success=False,
            )

    async def restart(self, session_id: str) -> bool:
        """Reinicia el kernel de una sesión (limpia variables)."""
        if session_id not in self._kernels:
            return False
        session = self._kernels[session_id]
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.JUPYTER_URL}/api/kernels/{session.kernel_id}/restart",
                headers=self._headers,
            )
            if resp.status_code == 200:
                session.exec_count = 0
                await self._init_kernel(session)
                return True
        return False

    async def kill(self, session_id: str):
        """Mata el kernel de una sesión."""
        if session_id in self._kernels:
            session = self._kernels.pop(session_id)
            await self._kill_kernel(session.kernel_id)

    async def list_kernels(self) -> list[dict]:
        """Lista todos los kernels activos."""
        return [
            {
                "session_id": k,
                "kernel_id":  v.kernel_id,
                "exec_count": v.exec_count,
                "age_min":    round((time.time() - v.created_at) / 60),
                "idle_min":   round((time.time() - v.last_used) / 60),
            }
            for k, v in self._kernels.items()
        ]

    async def is_available(self) -> bool:
        """Verifica que el servidor Jupyter está accesible."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.JUPYTER_URL}/api",
                    headers=self._headers,
                )
                return resp.status_code == 200
        except Exception:
            return False

    # ─────────────────────────────────────────────
    # GESTIÓN DE KERNELS
    # ─────────────────────────────────────────────

    async def _get_or_create_kernel(self, session_id: str) -> KernelSession:
        if session_id in self._kernels:
            return self._kernels[session_id]

        async with httpx.AsyncClient(timeout=30) as client:
            # Crear kernel en el servidor Jupyter
            resp = await client.post(
                f"{self.JUPYTER_URL}/api/kernels",
                headers=self._headers,
                json={"name": "python3"},
            )
            resp.raise_for_status()
            kernel_data = resp.json()
            kernel_id   = kernel_data["id"]

        # Construir URL WebSocket
        ws_base = self.JUPYTER_URL.replace("http://", "ws://").replace("https://", "wss://")
        ws_url  = f"{ws_base}/api/kernels/{kernel_id}/channels?token={self.JUPYTER_TOKEN}"

        session = KernelSession(
            session_id=session_id,
            kernel_id=kernel_id,
            ws_url=ws_url,
        )
        self._kernels[session_id] = session

        # Inicializar con imports estándar
        await self._init_kernel(session)
        return session

    async def _init_kernel(self, session: KernelSession):
        """Ejecuta el setup code de inicialización."""
        result = await self._execute_via_websocket(session, self.KERNEL_INIT_CODE, timeout=60)
        if not result.success:
            print(f"[Jupyter] Warning: init falló para {session.session_id}: {result.error}")

    async def _kill_kernel(self, kernel_id: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.delete(
                    f"{self.JUPYTER_URL}/api/kernels/{kernel_id}",
                    headers=self._headers,
                )
        except Exception:
            pass

    async def _interrupt_kernel(self, kernel_id: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{self.JUPYTER_URL}/api/kernels/{kernel_id}/interrupt",
                    headers=self._headers,
                    json={},
                )
        except Exception:
            pass

    # ─────────────────────────────────────────────
    # PROTOCOLO JUPYTER MESSAGING v5 via WebSocket
    # ─────────────────────────────────────────────

    async def _execute_via_websocket(
        self,
        session: KernelSession,
        code: str,
        timeout: int = 120,
    ) -> CellResult:
        """
        Ejecuta código usando el Jupyter Messaging Protocol v5.
        
        Flujo de mensajes:
          → execute_request   (enviamos el código)
          ← stream            (stdout/stderr en tiempo real)
          ← execute_result    (valor retornado, si lo hay)
          ← display_data      (imágenes matplotlib, etc.)
          ← error             (traceback si hay excepción)
          ← execute_reply     (señal de que terminó)
        """
        import websockets

        msg_id = uuid.uuid4().hex

        execute_request = {
            "header": {
                "msg_id":   msg_id,
                "username": "claude-brain",
                "session":  session.session_id,
                "msg_type": "execute_request",
                "version":  "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code":             code,
                "silent":           False,
                "store_history":    True,
                "user_expressions": {},
                "allow_stdin":      False,
            },
        }

        text_parts:  list[str] = []
        images:      list[str] = []
        error_text:  Optional[str] = None

        try:
            async with websockets.connect(
                session.ws_url,
                ping_interval=20,
                ping_timeout=30,
                close_timeout=10,
                max_size=50 * 1024 * 1024,  # 50MB para imágenes grandes
            ) as ws:
                # Enviar el request
                await ws.send(json.dumps(execute_request))

                # Leer respuestas hasta execute_reply
                deadline = time.time() + timeout
                while time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        continue

                    msg = json.loads(raw)
                    msg_type  = msg.get("msg_type", "")
                    parent_id = msg.get("parent_header", {}).get("msg_id", "")

                    # Solo procesar mensajes de nuestra petición
                    if parent_id != msg_id:
                        continue

                    content = msg.get("content", {})

                    if msg_type == "stream":
                        # stdout / stderr en tiempo real
                        text_parts.append(content.get("text", ""))

                    elif msg_type in ("execute_result", "display_data"):
                        # Resultado de la expresión / display()
                        data = content.get("data", {})
                        if "text/plain" in data:
                            text_parts.append(data["text/plain"])
                        if "image/png" in data:
                            images.append(data["image/png"])  # base64 PNG
                        if "text/html" in data and "text/plain" not in data:
                            # Tablas HTML → texto plano
                            text_parts.append(_html_to_text(data["text/html"]))

                    elif msg_type == "error":
                        # Excepción con traceback
                        tb = "\n".join(content.get("traceback", []))
                        error_text = _strip_ansi(tb)
                        break

                    elif msg_type == "execute_reply":
                        # Fin de la ejecución
                        status = content.get("status", "ok")
                        if status == "error":
                            tb = "\n".join(content.get("traceback", []))
                            if not error_text:
                                error_text = _strip_ansi(tb)
                        break

        except Exception as e:
            return CellResult(
                text="", images=[],
                error=f"WebSocket error: {type(e).__name__}: {e}",
                exec_count=0, success=False,
            )

        full_text = "".join(text_parts)
        # Limpiar ANSI del texto
        full_text = _strip_ansi(full_text)
        # Truncar si es muy largo
        if len(full_text) > 8000:
            full_text = full_text[:4000] + "\n...[truncado]...\n" + full_text[-2000:]

        return CellResult(
            text=full_text,
            images=images,
            error=error_text,
            exec_count=0,  # se rellena en execute()
            success=error_text is None,
        )

    # ─────────────────────────────────────────────
    # LIMPIEZA AUTOMÁTICA
    # ─────────────────────────────────────────────

    async def _cleanup_loop(self):
        """Mata kernels que llevan más de KERNEL_TTL segundos inactivos."""
        while True:
            await asyncio.sleep(300)  # revisar cada 5 min
            now = time.time()
            to_kill = [
                sid for sid, s in self._kernels.items()
                if now - s.last_used > self.KERNEL_TTL
            ]
            for sid in to_kill:
                print(f"[Jupyter] Killing idle kernel: {sid}")
                await self.kill(sid)


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────

def _strip_ansi(text: str) -> str:
    """Elimina secuencias de escape ANSI del texto."""
    return re.sub(r'\x1B\[[\d;]*[mGKHF]|\x1B\[\?[\d]*[lh]', '', text)


def _html_to_text(html: str) -> str:
    """Conversión básica de tabla HTML a texto ASCII."""
    try:
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = True
        h.body_width = 120
        return h.handle(html)
    except ImportError:
        # Fallback: strip tags
        return re.sub(r'<[^>]+>', ' ', html)
