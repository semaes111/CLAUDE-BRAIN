#!/bin/bash
# install-aitmpl.sh — Descarga 119 componentes de aitmpl.com
# Adaptado para correr dentro del contenedor Docker del agente
# Fuente: github.com/davila7/claude-code-templates
#
# Destinos (dentro del contenedor):
#   /app/components/agents/     ← 36 agentes
#   /app/components/skills/     ← 70 skills
#   /app/components/commands/   ← 13 comandos

set -euo pipefail

BASE="https://raw.githubusercontent.com/davila7/claude-code-templates/main/cli-tool/components"
AGENT_DIR="/app/components/agents"
SKILL_DIR="/app/components/skills"
CMD_DIR="/app/components/commands"

mkdir -p "$AGENT_DIR" "$SKILL_DIR" "$CMD_DIR"

OK=0; FAIL=0

download_agent() {
  local name=$1 path=$2
  local content
  content=$(curl -sfL --retry 3 --retry-delay 1 "$BASE/agents/$path.md" 2>/dev/null) || true
  if [ -n "$content" ] && [ "$(echo "$content" | wc -c)" -gt 50 ]; then
    # Crear subdirectorio por categoría
    local category=$(dirname "$path")
    mkdir -p "$AGENT_DIR/$category"
    echo "$content" > "$AGENT_DIR/$path.md"
    ((OK++))
  else
    ((FAIL++))
  fi
}

download_skill() {
  local name=$1 path=$2 variant=${3:-lower}
  local url
  if [ "$variant" = "upper" ]; then
    url="$BASE/skills/$path/SKILL.MD"
  else
    url="$BASE/skills/$path/SKILL.md"
  fi
  local content
  content=$(curl -sfL --retry 3 --retry-delay 1 "$url" 2>/dev/null) || true
  if [ -n "$content" ] && [ "$(echo "$content" | wc -c)" -gt 50 ]; then
    mkdir -p "$SKILL_DIR/$name"
    echo "$content" > "$SKILL_DIR/$name/SKILL.md"
    ((OK++))
  else
    ((FAIL++))
  fi
}

download_command() {
  local name=$1 path=$2
  local content
  content=$(curl -sfL --retry 3 --retry-delay 1 "$BASE/commands/$path.md" 2>/dev/null) || true
  if [ -n "$content" ] && [ "$(echo "$content" | wc -c)" -gt 50 ]; then
    local category=$(dirname "$path")
    mkdir -p "$CMD_DIR/$category"
    echo "$content" > "$CMD_DIR/$name.md"
    ((OK++))
  else
    ((FAIL++))
  fi
}

echo "⏳ Descargando 119 componentes de aitmpl.com (github.com/davila7/claude-code-templates)..."

# ── 36 AGENTES ───────────────────────────────────────────────

echo "🤖 Agentes (36)..."

# Programming Languages
download_agent "python-pro"        "programming-languages/python-pro" &
download_agent "typescript-pro"    "programming-languages/typescript-pro" &
download_agent "javascript-pro"    "programming-languages/javascript-pro" &
download_agent "nextjs-developer"  "programming-languages/nextjs-developer" &
download_agent "sql-pro"           "programming-languages/sql-pro" &
download_agent "react-specialist"  "programming-languages/react-specialist" &
download_agent "vue-expert"        "programming-languages/vue-expert" &
download_agent "django-developer"  "programming-languages/django-developer" &
download_agent "flutter-expert"    "programming-languages/flutter-expert" &
download_agent "swift-expert"      "programming-languages/swift-expert" &
download_agent "kotlin-specialist" "programming-languages/kotlin-specialist" &
wait

# Data & AI
download_agent "data-analyst"   "data-ai/data-analyst" &
download_agent "data-scientist" "data-ai/data-scientist" &
download_agent "data-engineer"  "data-ai/data-engineer" &
download_agent "quant-analyst"  "data-ai/quant-analyst" &

# Database
download_agent "database-architect"        "database/database-architect" &
download_agent "supabase-schema-architect" "database/supabase-schema-architect" &
download_agent "postgres-pro"              "database/postgres-pro" &
wait

# Development Tools
download_agent "code-reviewer"          "development-tools/code-reviewer" &
download_agent "debugger"               "development-tools/debugger" &
download_agent "refactoring-specialist" "development-tools/refactoring-specialist" &
download_agent "performance-engineer"   "development-tools/performance-engineer" &

# Expert Advisors
download_agent "legal-advisor" "expert-advisors/legal-advisor" &
wait

# Web Tools
download_agent "expert-nextjs-developer"     "web-tools/expert-nextjs-developer" &
download_agent "react-performance-optimizer" "web-tools/react-performance-optimizer" &
download_agent "nextjs-architecture-expert"  "web-tools/nextjs-architecture-expert" &
download_agent "expert-react-frontend"       "web-tools/expert-react-frontend-engineer" &
wait

# Development Team
download_agent "frontend-developer"   "development-team/frontend-developer" &
download_agent "fullstack-developer"  "development-team/fullstack-developer" &
download_agent "backend-developer"    "development-team/backend-developer" &
download_agent "backend-architect"    "development-team/backend-architect" &
download_agent "ui-ux-designer"       "development-team/ui-ux-designer" &
download_agent "ui-designer"          "development-team/ui-designer" &
download_agent "mobile-developer"     "development-team/mobile-developer" &
download_agent "mobile-app-developer" "development-team/mobile-app-developer" &
download_agent "ios-developer"        "development-team/ios-developer" &
wait

# ── 70 SKILLS ────────────────────────────────────────────────

echo "🎨 Skills (70)..."

# Development (27)
download_skill "python-patterns"          "development/python-patterns" &
download_skill "typescript-expert"        "development/typescript-expert" &
download_skill "nextjs-best-practices"    "development/nextjs-best-practices" &
download_skill "react-best-practices"     "development/react-best-practices" &
download_skill "senior-fullstack"         "development/senior-fullstack" &
download_skill "senior-backend"           "development/senior-backend" &
download_skill "senior-frontend"          "development/senior-frontend" &
download_skill "clean-code"              "development/clean-code" &
download_skill "software-architecture"    "development/software-architecture" &
download_skill "best-practices"          "development/best-practices" &
wait

download_skill "performance"                "development/performance" &
download_skill "security-compliance"        "development/security-compliance" &
download_skill "systematic-debugging"       "development/systematic-debugging" &
download_skill "react-dev"                 "development/react-dev" &
download_skill "react-patterns"            "development/react-patterns" &
download_skill "react-ui-patterns"         "development/react-ui-patterns" &
download_skill "react-useeffect"           "development/react-useeffect" &
download_skill "cc-skill-frontend-patterns" "development/cc-skill-frontend-patterns" &
download_skill "frontend-dev-guidelines"    "development/frontend-dev-guidelines" &
download_skill "backend-dev-guidelines"     "development/backend-dev-guidelines" &
wait

download_skill "nextjs-supabase-auth"    "development/nextjs-supabase-auth" &
download_skill "graphql"                 "development/graphql" &
download_skill "mui"                     "development/mui" &
download_skill "core-web-vitals"         "development/core-web-vitals" &
download_skill "web-quality-audit"       "development/web-quality-audit" &
download_skill "artifacts-builder"       "development/artifacts-builder" &
download_skill "web-artifacts-builder"   "development/web-artifacts-builder" &
download_skill "senior-data-engineer"    "development/senior-data-engineer" &
download_skill "senior-data-scientist"   "development/senior-data-scientist" &
download_skill "nodejs-best-practices"   "development/nodejs-best-practices" &
wait

# Database (2)
download_skill "postgres-schema-design"          "database/postgres-schema-design" upper &
download_skill "supabase-postgres-best-practices" "database/supabase-postgres-best-practices" &

# Business (4)
download_skill "ceo-advisor"        "business-marketing/ceo-advisor" &
download_skill "cto-advisor"        "business-marketing/cto-advisor" &
download_skill "pricing-strategy"   "business-marketing/pricing-strategy" &
download_skill "analytics-tracking" "business-marketing/analytics-tracking" &
wait

# Document Processing (4)
download_skill "xlsx"        "document-processing/xlsx" &
download_skill "pdf"         "document-processing/pdf" &
download_skill "docx"        "document-processing/docx" &
download_skill "spreadsheet" "document-processing/spreadsheet" &
wait

download_skill "xlsx-official"    "document-processing/xlsx-official" &
download_skill "google-analytics" "analytics/google-analytics" &

# Web Development (3)
download_skill "web-performance-optimization" "web-development/web-performance-optimization" &
download_skill "fastapi-endpoint"             "web-development/fastapi-endpoint" &
wait

# More Development Skills
download_skill "api-patterns"       "development/api-patterns" &
download_skill "stripe-integration" "development/stripe-integration" &
download_skill "clerk-auth"         "development/clerk-auth" &
download_skill "firebase"           "development/firebase" &
download_skill "vercel-deploy"      "development/vercel-deploy" &
download_skill "cloudflare-deploy"  "development/cloudflare-deploy" &
download_skill "docker-expert"      "development/docker-expert" &
wait

# Creative Design (16)
download_skill "d3js-charts"           "creative-design/claude-d3js-skill" &
download_skill "frontend-design"       "creative-design/frontend-design" &
download_skill "canvas-design"         "creative-design/canvas-design" &
download_skill "web-design-guidelines" "creative-design/web-design-guidelines" &
download_skill "tailwind-patterns"     "creative-design/tailwind-patterns" &
download_skill "mermaid-diagrams"      "creative-design/mermaid-diagrams" &
download_skill "ui-design-system"      "creative-design/ui-design-system" &
download_skill "ui-ux-pro-max"         "creative-design/ui-ux-pro-max" &
download_skill "3d-web-experience"     "creative-design/3d-web-experience" &
download_skill "interactive-portfolio" "creative-design/interactive-portfolio" &
wait

download_skill "algorithmic-art"      "creative-design/algorithmic-art" &
download_skill "figma-implement"      "creative-design/figma-implement-design" &
download_skill "figma"                "creative-design/figma" &
download_skill "theme-factory"        "creative-design/theme-factory" &
download_skill "design-to-code"       "design-to-code" &
download_skill "mobile-design"        "creative-design/mobile-design" &
download_skill "scroll-experience"    "creative-design/scroll-experience" &
wait

# ── 13 COMANDOS ──────────────────────────────────────────────

echo "⚡ Comandos (13)..."

download_command "code-review"           "utilities/code-review" &
download_command "generate-tests"        "testing/generate-tests" &
download_command "refactor-code"         "utilities/refactor-code" &
download_command "debug-error"           "utilities/debug-error" &
download_command "explain-code"          "utilities/explain-code" &
download_command "fix-issue"             "utilities/fix-issue" &
download_command "ultra-think"           "utilities/ultra-think" &
download_command "performance-audit"     "performance/performance-audit" &
download_command "optimize-database"     "performance/optimize-database-performance" &
download_command "security-audit"        "security/security-audit" &
download_command "generate-api-docs"     "documentation/generate-api-documentation" &
download_command "architecture-explorer" "utilities/architecture-scenario-explorer" &
download_command "supabase-data-explorer" "database/supabase-data-explorer" &
wait

# ── RESULTADO ────────────────────────────────────────────────

AGENTS=$(find "$AGENT_DIR" -name "*.md" 2>/dev/null | wc -l)
SKILLS=$(find "$SKILL_DIR" -name "SKILL.md" 2>/dev/null | wc -l)
CMDS=$(find "$CMD_DIR" -name "*.md" 2>/dev/null | wc -l)

echo ""
echo "═══════════════════════════════════════════"
echo "  ✅ AITMPL instalado en /app/components"
echo "═══════════════════════════════════════════"
echo "  🤖 Agentes:  $AGENTS"
echo "  🎨 Skills:   $SKILLS"
echo "  ⚡ Comandos: $CMDS"
echo "  ❌ Fallos:   $FAIL"
echo "═══════════════════════════════════════════"
