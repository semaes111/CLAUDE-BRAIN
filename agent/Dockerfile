FROM python:3.12-slim

# ─────────────────────────────────────────────
# Sistema base + Node.js 20 + Claude Code CLI
# ─────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git wget ca-certificates gnupg2 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─────────────────────────────────────────────
# Dependencias Python (incluye mem0ai)
# ─────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium --with-deps 2>/dev/null || true

# ─────────────────────────────────────────────
# Código del agente
# ─────────────────────────────────────────────
COPY . .

# ─────────────────────────────────────────────
# Directorios persistentes
# ─────────────────────────────────────────────
RUN mkdir -p /workspaces /app/skills /app/components/agents \
             /app/components/skills /app/components/commands /root/.claude

# ─────────────────────────────────────────────
# Descargar 119 componentes de aitmpl.com
# Se hace en build time para que estén disponibles al arrancar
# ─────────────────────────────────────────────
COPY ../scripts/install-aitmpl.sh /tmp/install-aitmpl.sh
RUN chmod +x /tmp/install-aitmpl.sh && /tmp/install-aitmpl.sh || true

# ─────────────────────────────────────────────
# Entrypoint — gestiona auth OAuth
# ─────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
