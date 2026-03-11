#!/bin/bash
# install.sh — Instalación completa de CLAUDE-BRAIN en Ubuntu 22.04/24.04
# Uso: chmod +x install.sh && sudo ./install.sh

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Verificar Ubuntu
[[ "$(lsb_release -si 2>/dev/null)" == "Ubuntu" ]] || error "Solo Ubuntu 22.04/24.04"

info "═══ 1. Actualizar sistema ═══"
apt update && apt upgrade -y
apt install -y curl git jq unzip python3-pip lsb-release

info "═══ 2. Docker + Compose v2 ═══"
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "$SUDO_USER"
fi
apt install -y docker-compose-plugin
info "Docker: $(docker --version)"

info "═══ 3. Node.js 20 + Claude Code CLI ═══"
if ! command -v node &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt install -y nodejs
fi
npm install -g @anthropic-ai/claude-code
info "Claude Code: $(claude --version 2>/dev/null || echo 'instalado')"

info "═══ 4. AUTENTICACIÓN MAX OAUTH ═══"
echo ""
echo "  Claude Code debe autenticarse con tu cuenta Max ($200/mes)"
echo "  Esto permite usar el CLI sin API billing adicional."
echo ""
echo "  Método 1 (recomendado): Copia credenciales desde tu máquina local"
echo "    cat ~/.claude/.credentials.json  # en tu máquina local"
echo "    mkdir -p ~/.claude && nano ~/.claude/.credentials.json  # en el VPS"
echo ""
echo "  Método 2: Login interactivo (necesita acceso a browser)"
echo "    claude auth login"
echo ""

if [[ -f "$HOME/.claude/.credentials.json" ]]; then
    info "✅ Credenciales encontradas en ~/.claude/.credentials.json"
else
    warn "No hay credenciales. Ejecuta 'claude auth login' o copia desde tu máquina local."
fi

info "═══ 5. Verificar autenticación Max OAuth ═══"
if command -v claude &>/dev/null; then
    # Quitar API key del entorno para forzar OAuth
    unset ANTHROPIC_API_KEY 2>/dev/null || true
    RESULT=$(claude --print "responde solo OK" 2>&1 | head -5 || true)
    if echo "$RESULT" | grep -qi "ok"; then
        info "✅ Max OAuth funcionando — $0 API billing adicional"
    else
        warn "No se pudo verificar. Revisar autenticación manualmente."
        echo "  Output: $RESULT"
    fi
fi

info "═══ 6. Configurar proyecto ═══"
if [[ ! -f ".env" ]]; then
    cp .env.example .env
    # Generar secretos automáticamente
    POSTGRES_PASS=$(openssl rand -hex 32)
    JWT_SECRET=$(openssl rand -hex 64)
    REDIS_PASS=$(openssl rand -hex 16)
    N8N_PASS=$(openssl rand -base64 24 | tr -d '/')
    N8N_KEY=$(openssl rand -hex 32)

    sed -i "s/CAMBIAR_openssl_rand_hex_32/$POSTGRES_PASS/g" .env
    sed -i "s/CAMBIAR_openssl_rand_hex_64/$JWT_SECRET/g" .env
    sed -i "s/CAMBIAR_openssl_rand_hex_16/$REDIS_PASS/g" .env
    sed -i "s/CAMBIAR_openssl_rand_base64_24/$N8N_PASS/g" .env
    sed -i "s/CAMBIAR_openssl_rand_hex_32_n8n/$N8N_KEY/g" .env

    warn "Edita .env y añade:"
    echo "  - DOMAIN=tu.dominio.com"
    echo "  - GITHUB_TOKEN=ghp_..."
    echo "  - SUPABASE_SERVICE_KEY=eyJ..."
    echo ""
    read -p "¿Lista la configuración de .env? (y/n) " -r
    [[ $REPLY == "y" ]] || error "Configura .env antes de continuar"
fi

info "═══ 7. Levantar stack ═══"
docker compose pull
docker compose up -d --build

info "═══ 8. Esperar servicios (hasta 3 min) ═══"
echo "Descargando nomic-embed-text-v1.5 (~270MB)..."
RETRIES=36
for i in $(seq 1 $RETRIES); do
    if docker compose exec embeddings curl -sf http://localhost:8080/health &>/dev/null; then
        info "✅ Embeddings listos"
        break
    fi
    echo -n "."
    sleep 5
    [[ $i == $RETRIES ]] && warn "Embeddings tardando — puede seguir en background"
done

info "═══ 9. Inicializar base de datos ═══"
sleep 5
docker compose exec supabase-db psql -U postgres \
    -c "SELECT 'pgvector: ' || extversion FROM pg_extension WHERE extname='vector';" 2>/dev/null || true

info "═══ 10. Verificar sistema ═══"
sleep 3
STATUS=$(curl -s http://localhost:8000/v1/status 2>/dev/null || echo '{"status":"starting"}')
echo "$STATUS" | python3 -m json.tool 2>/dev/null || echo "$STATUS"

echo ""
echo "════════════════════════════════════════"
info "✅ CLAUDE-BRAIN instalado"
echo "════════════════════════════════════════"
echo ""
echo "  Web UI:      http://localhost:3080"
echo "  Agent API:   http://localhost:8000"
echo "  API Docs:    http://localhost:8000/docs"
echo "  n8n:         http://localhost:5678"
echo "  Supabase:    http://localhost:8001"
echo ""
echo "  Test rápido:"
echo "  curl -X POST http://localhost:8000/v1/chat \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"message\": \"di hola\"}'"
echo ""
echo "  Costos adicionales: ~€25/mes (VPS)"
echo "  Sin API billing extra ✅"
