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
# Dependencias Python
# ─────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────
# Código del agente
# ─────────────────────────────────────────────
COPY . .

# ─────────────────────────────────────────────
# Directorios persistentes
# ─────────────────────────────────────────────
RUN mkdir -p /workspaces /app/skills /root/.claude

# ─────────────────────────────────────────────
# Entrypoint — gestiona auth automáticamente
# ─────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
