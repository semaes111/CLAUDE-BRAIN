#!/bin/bash
# install.sh — Instalación de CLAUDE-BRAIN en Docker (VPS o local)
#
# Prerequisitos en el host:
#   - Docker Engine >= 24
#   - Docker Compose v2 (plugin)
#   - Eso es todo. Nada más se instala en el host.
#
# Uso: chmod +x scripts/install.sh && ./scripts/install.sh

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()  { echo -e "${CYAN}[→]${NC} $1"; }

echo ""
echo "🧠 CLAUDE-BRAIN — Instalación Docker"
echo "══════════════════════════════════════"
echo "  Todo corre en contenedores."
echo "  El host solo necesita Docker."
echo ""

# ─────────────────────────────────────────────
# 1. Verificar Docker
# ─────────────────────────────────────────────
step "Verificando Docker..."

if ! command -v docker &>/dev/null; then
    warn "Docker no encontrado. Instalando..."
    curl -fsSL https://get.docker.com | sh
    # Solo si hay sudo disponible
    [[ -n "${SUDO_USER:-}" ]] && usermod -aG docker "$SUDO_USER" || true
fi

if ! docker compose version &>/dev/null; then
    error "Docker Compose v2 no encontrado.\nInstalar: https://docs.docker.com/compose/install/"
fi

info "Docker: $(docker --version)"
info "Compose: $(docker compose version)"

# ─────────────────────────────────────────────
# 2. Configurar .env
# ─────────────────────────────────────────────
step "Configurando variables de entorno..."

if [[ ! -f ".env" ]]; then
    cp .env.example .env
    
    # Generar secretos automáticamente
    PG_PASS=$(openssl rand -hex 32)
    JWT_SEC=$(openssl rand -hex 64)
    REDIS_PASS=$(openssl rand -hex 16)
    N8N_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)
    N8N_KEY=$(openssl rand -hex 32)
    
    sed -i "s/CAMBIAR_openssl_rand_hex_32/$PG_PASS/g" .env
    sed -i "s/CAMBIAR_openssl_rand_hex_64/$JWT_SEC/g" .env
    sed -i "s/CAMBIAR_openssl_rand_hex_16/$REDIS_PASS/g" .env
    sed -i "s/CAMBIAR_openssl_rand_base64_24/$N8N_PASS/g" .env
    
    info "Secretos generados automáticamente"
    warn "Edita .env para añadir: GITHUB_TOKEN, SUPABASE_SERVICE_KEY, y CLAUDE_CREDENTIALS_JSON"
    echo ""
    read -p "¿Continuar sin CLAUDE_CREDENTIALS_JSON? (puedes autenticar después) [y/n] " -r
    [[ $REPLY == "y" ]] || echo "Edita .env y vuelve a ejecutar."
fi

info ".env configurado"

# ─────────────────────────────────────────────
# 3. Auth Claude Code (opcional aquí, requerido antes de usar)
# ─────────────────────────────────────────────
step "Verificando credenciales Claude Code Max..."

LOCAL_CREDS="$HOME/.claude/.credentials.json"
if [[ -f "$LOCAL_CREDS" ]]; then
    info "Credenciales locales encontradas. Ejecuta:"
    echo "   ./scripts/setup-auth.sh"
    echo "   Para exportarlas automáticamente al .env"
else
    warn "Sin credenciales locales. Después de arrancar el stack:"
    echo "   docker compose exec agent-api claude auth login"
fi

# ─────────────────────────────────────────────
# 4. Arrancar stack
# ─────────────────────────────────────────────
step "Arrancando stack Docker..."
docker compose pull --quiet
docker compose up -d --build

# ─────────────────────────────────────────────
# 5. Esperar embeddings (descarga ~270MB primera vez)
# ─────────────────────────────────────────────
step "Esperando embeddings (primera vez descarga ~270MB)..."
echo -n "  "
RETRIES=60
for i in $(seq 1 $RETRIES); do
    if docker compose exec embeddings curl -sf http://localhost:8080/health &>/dev/null 2>&1; then
        echo ""
        info "nomic-embed-text-v1.5 listo"
        break
    fi
    echo -n "."
    sleep 5
    [[ $i == $RETRIES ]] && warn "Embeddings tardando — continúan en background"
done

# ─────────────────────────────────────────────
# 6. Verificar
# ─────────────────────────────────────────────
step "Verificando servicios..."
docker compose ps

echo ""
echo "══════════════════════════════════════════════"
info "CLAUDE-BRAIN arrancado en Docker"
echo "══════════════════════════════════════════════"
echo ""
echo "  Web UI:        http://localhost:3080"
echo "  Agent API:     http://localhost:8000"
echo "  API Docs:      http://localhost:8000/docs"
echo "  n8n:           http://localhost:5678"
echo "  Supabase:      http://localhost:8001"
echo ""
echo "  Si no autenticaste Claude Code aún:"
echo "  docker compose exec agent-api claude auth login"
echo ""
echo "  Test del agente:"
echo "  curl -X POST http://localhost:8000/v1/chat \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"message\": \"di hola\"}'"
echo ""
