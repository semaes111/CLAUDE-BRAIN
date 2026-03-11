#!/bin/bash
# setup-auth.sh — Configura Claude Code Max OAuth dentro de Docker
#
# Uso:
#   chmod +x scripts/setup-auth.sh
#   ./scripts/setup-auth.sh
#
# El script detecta si ya tienes Claude Code autenticado localmente
# y te ayuda a exportar las credenciales al contenedor Docker.

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}✅${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠️ ${NC} $1"; }
step()  { echo -e "${CYAN}──${NC} $1"; }

echo ""
echo "🧠 CLAUDE-BRAIN — Configuración de Auth"
echo "════════════════════════════════════════"
echo ""

LOCAL_CREDS="$HOME/.claude/.credentials.json"

# ─────────────────────────────────────────────
# OPCIÓN A: Credenciales locales encontradas
# ─────────────────────────────────────────────
if [[ -f "$LOCAL_CREDS" ]]; then
    info "Credenciales Claude Code encontradas en: $LOCAL_CREDS"
    echo ""
    step "Verificando que son credenciales Max..."
    
    SUB_TYPE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$LOCAL_CREDS'))
    oauth = d.get('claudeAiOauth', {})
    print(oauth.get('subscriptionType', 'unknown'))
except:
    print('error')
" 2>/dev/null || echo "unknown")
    
    if [[ "$SUB_TYPE" == "max" ]]; then
        info "Plan Max detectado ✓"
    else
        warn "Plan detectado: $SUB_TYPE (se recomienda Max para uso intensivo)"
    fi
    
    echo ""
    step "Exportando credenciales al .env..."
    
    # Exportar JSON como una línea
    CREDS_JSON=$(python3 -c "import json; print(json.dumps(json.load(open('$LOCAL_CREDS'))))" 2>/dev/null)
    
    if [[ -f ".env" ]]; then
        # Actualizar la línea existente
        if grep -q "^CLAUDE_CREDENTIALS_JSON=" .env; then
            # Usar Python para el reemplazo (evita problemas con caracteres especiales)
            python3 << PYEOF
import re

with open('.env', 'r') as f:
    content = f.read()

new_line = f'CLAUDE_CREDENTIALS_JSON={repr($CREDS_JSON)[1:-1]}'
content = re.sub(r'^CLAUDE_CREDENTIALS_JSON=.*$', f'CLAUDE_CREDENTIALS_JSON={repr("$CREDS_JSON")[1:-1]}', content, flags=re.MULTILINE)

with open('.env', 'w') as f:
    f.write(content)
PYEOF
            info "CLAUDE_CREDENTIALS_JSON actualizado en .env"
        else
            echo "CLAUDE_CREDENTIALS_JSON=$CREDS_JSON" >> .env
            info "CLAUDE_CREDENTIALS_JSON añadido a .env"
        fi
    else
        warn ".env no encontrado. Ejecuta: cp .env.example .env"
        echo ""
        echo "Añade esta línea a tu .env:"
        echo "CLAUDE_CREDENTIALS_JSON=$CREDS_JSON"
    fi
    
    echo ""
    info "Configuración completa. Arranca el stack con:"
    echo "   docker compose up -d --build"

# ─────────────────────────────────────────────
# OPCIÓN B: Sin credenciales locales
# ─────────────────────────────────────────────
else
    warn "No se encontraron credenciales locales en: $LOCAL_CREDS"
    echo ""
    echo "Opciones:"
    echo ""
    echo "  1. Primero arranca el stack:"
    echo "     docker compose up -d --build"
    echo ""
    echo "  2. Luego autentica dentro del contenedor:"
    echo "     docker compose exec agent-api claude auth login"
    echo ""
    echo "     → El CLI genera una URL"
    echo "     → La abres en tu browser"
    echo "     → Autorizas con tu cuenta Max"
    echo "     → Las credenciales se guardan en el volumen cb-claude-auth"
    echo "     → Persisten entre reinicios del contenedor"
    echo ""
    echo "  3. Verifica la autenticación:"
    echo "     docker compose exec agent-api claude --print 'di hola'"
fi

echo ""
