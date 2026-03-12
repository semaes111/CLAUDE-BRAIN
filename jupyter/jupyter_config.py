"""jupyter_server_config.py — Configuración del servidor Jupyter de CLAUDE-BRAIN"""

import os

# ── Seguridad ─────────────────────────────────────────────
c.ServerApp.token        = os.getenv("JUPYTER_TOKEN", "claude-brain-jupyter-token")
c.ServerApp.password     = ""
c.ServerApp.open_browser = False
c.ServerApp.ip           = "0.0.0.0"
c.ServerApp.port         = 8888

# ── CORS — solo red interna Docker ────────────────────────
c.ServerApp.allow_origin    = "*"
c.ServerApp.allow_credentials = True

# ── Sin restricciones de directorio ───────────────────────
c.ServerApp.root_dir = "/workspaces"
c.ContentsManager.allow_hidden = True

# ── Kernels ───────────────────────────────────────────────
# Tiempo de inactividad antes de matar kernel (segundos)
c.MappingKernelManager.cull_idle_timeout     = 3600   # 1 hora
c.MappingKernelManager.cull_interval         = 300    # revisar cada 5 min
c.MappingKernelManager.cull_connected        = False

# Máximo de kernels simultáneos
c.MappingKernelManager.default_kernel_name = "python3"

# ── Output ────────────────────────────────────────────────
c.ServerApp.log_level = "WARNING"
