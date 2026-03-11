#!/bin/bash
# entrypoint.sh — Gestión de auth Claude Code Max dentro de Docker
#
# Estrategias de autenticación (en orden de prioridad):
#
#   1. CLAUDE_CREDENTIALS_JSON (env var con JSON completo)
#      → Más simple: exportas el JSON como variable de entorno
#      → docker run -e CLAUDE_CREDENTIALS_JSON="$(cat ~/.claude/.credentials.json)"
#
#   2. Volumen persistente /root/.claude
#      → Montas el directorio .claude del host en el contenedor
#      → O el contenedor genera el auth una vez y lo persiste en volumen
#
#   3. Auth interactivo (primera vez)
#      → El contenedor abre un browser-less login URL
#      → Copias la URL en tu browser local, autorizas, y el contenedor continúa

set -euo pipefail

CLAUDE_DIR="/root/.claude"
CREDS_FILE="${CLAUDE_DIR}/.credentials.json"

echo "🧠 CLAUDE-BRAIN — Iniciando..."
echo "================================"

# ─────────────────────────────────────────────
# ESTRATEGIA 1: Credenciales via variable de entorno
# ─────────────────────────────────────────────
if [[ -n "${CLAUDE_CREDENTIALS_JSON:-}" ]]; then
    echo "✅ Auth: usando CLAUDE_CREDENTIALS_JSON (env var)"
    mkdir -p "$CLAUDE_DIR"
    echo "$CLAUDE_CREDENTIALS_JSON" > "$CREDS_FILE"
    chmod 600 "$CREDS_FILE"

# ─────────────────────────────────────────────
# ESTRATEGIA 2: Credenciales ya existen (volumen montado)
# ─────────────────────────────────────────────
elif [[ -f "$CREDS_FILE" ]]; then
    echo "✅ Auth: usando credenciales del volumen persistente"
    # Verificar que no están expiradas
    if command -v claude &>/dev/null; then
        TEST=$(timeout 15 claude --print "OK" 2>&1 || true)
        if echo "$TEST" | grep -qi "OK\|ok"; then
            echo "✅ Claude Max OAuth activo"
        else
            echo "⚠️  Credenciales pueden estar expiradas, intentando continuar..."
        fi
    fi

# ─────────────────────────────────────────────
# ESTRATEGIA 3: Auth interactivo (primera vez)
# ─────────────────────────────────────────────
else
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  PRIMERA VEZ — Autenticación requerida"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  Opciones:"
    echo ""
    echo "  OPCIÓN A (recomendada): Exporta tus credenciales existentes"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  En tu máquina local (donde ya tienes Claude Code):"
    echo ""
    echo "  export CLAUDE_CREDENTIALS_JSON=\$(cat ~/.claude/.credentials.json)"
    echo ""
    echo "  Luego añade en .env:"
    echo "  CLAUDE_CREDENTIALS_JSON=<pega el JSON aquí>"
    echo ""
    echo "  OPCIÓN B: Auth interactivo ahora mismo"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  Ejecuta en otra terminal:"
    echo "  docker compose exec agent-api claude auth login"
    echo ""
    echo "  Las credenciales se guardarán en el volumen 'claude_auth'"
    echo "  y persistirán entre reinicios del contenedor."
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  Iniciando API en modo degradado (sin auth)..."
    echo "  El agente responderá con error hasta completar la auth."
    echo ""
fi

# ─────────────────────────────────────────────
# VERIFICACIÓN FINAL
# ─────────────────────────────────────────────
echo ""
echo "─── Verificando entorno ───"

# Verificar que ANTHROPIC_API_KEY NO está seteada (evita billing extra)
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "⚠️  ADVERTENCIA: ANTHROPIC_API_KEY detectada en el entorno"
    echo "   Esto activará API billing (cobrado por token)"
    echo "   Si quieres usar Max OAuth, elimina ANTHROPIC_API_KEY del .env"
else
    echo "✅ ANTHROPIC_API_KEY ausente → usará Max OAuth (\$0 extra)"
fi

echo "✅ claude CLI: $(claude --version 2>/dev/null || echo 'instalado')"
echo "✅ Python: $(python3 --version)"
echo "✅ Workdir: /workspaces"
echo ""

# ─────────────────────────────────────────────
# ARRANCAR EL SERVIDOR
# ─────────────────────────────────────────────
echo "🚀 Arrancando Agent API..."
exec "$@"
