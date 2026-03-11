# 🧠 CLAUDE-BRAIN

> Autonomous AI Agent powered by **Claude Code Max** — Zero extra API billing.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Max%20%24200%2Fmo-orange)](https://claude.ai)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue)](docker-compose.yml)

## 🎯 Concepto clave

| ❌ API billing tradicional | ✅ CLAUDE-BRAIN |
|---|---|
| `anthropic.Anthropic()` → paga por token | `claude --print` vía subprocess → usa Max OAuth |
| Claude Agent SDK → requiere API key | CLI built-in tools → incluido en plan |
| OpenAI embeddings → $$/mes | `nomic-embed-text` local → $0/mes |
| Supabase Cloud → $25/mes | Supabase self-hosted → $0/mes |
| **Total: $300-800/mes** | **Total: €200 (plan) + €25 VPS** |

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────────────┐
│                  VPS Ubuntu + Docker                  │
│                                                       │
│  NGINX ──► LibreChat (UI) ──► Agent API (FastAPI)    │
│                                    │                  │
│                         ClaudeMaxRunner               │
│                    subprocess("claude --print")       │
│                    ← Max OAuth, $0 extra →            │
│                                    │                  │
│         ┌──────────────────────────┼──────────────┐  │
│      Sandbox    GitHub    n8n    Supabase  Memory  │  │
│      (snekbox)  (PyGH)  (REST)  (pgvector)(Redis)  │  │
└─────────────────────────────────────────────────────┘
```

## 🚀 Capacidades

- **🔧 Ejecución de código** — Python, JS, Bash en sandbox aislado (nsjail)
- **🔍 Búsqueda web** — WebSearch + WebFetch (built-in del CLI, gratis)
- **📁 GitHub** — Clone, commit, push, PR automáticos
- **🧠 Memoria persistente** — Redis (working) + pgvector (long-term)
- **⚡ Next.js + Supabase** — Desarrollo full-stack integrado
- **🔄 n8n** — Automatización de workflows bidireccional
- **📦 Skills modulares** — Sistema SKILL.md extensible
- **🤝 Multi-agente** — Orquestador + subagentes paralelos

## 📋 Requisitos

- VPS Ubuntu 22.04/24.04 con **8GB RAM mínimo** (16GB recomendado)
- Docker + Docker Compose v2
- **Claude Code Max plan** ($100 o $200/mes) — autenticado con OAuth
- GitHub Personal Access Token (scope: `repo`)

## ⚡ Instalación rápida

```bash
# 1. Clonar el repo
git clone https://github.com/semaes111/CLAUDE-BRAIN.git
cd CLAUDE-BRAIN

# 2. Configurar variables de entorno
cp .env.example .env
nano .env   # Añadir tus credenciales

# 3. Autenticar Claude Code con Max OAuth (en el VPS)
npm install -g @anthropic-ai/claude-code
claude auth login   # Abre URL en tu browser local

# 4. Verificar autenticación (debe usar Max, no API billing)
unset ANTHROPIC_API_KEY
claude --print "di VERIFICADO"

# 5. Levantar stack completo
docker compose up -d --build

# 6. Verificar
curl http://localhost:8000/v1/status
```

## 📁 Estructura del proyecto

```
CLAUDE-BRAIN/
├── agent/
│   ├── core/           # ClaudeMaxRunner — motor central (subprocess CLI)
│   ├── memory/         # Redis + pgvector + extracción automática
│   ├── skills/         # SkillManager (SKILL.md loader)
│   ├── orchestrator/   # Multi-agent coordinator
│   └── api/            # FastAPI REST + WebSocket streaming
├── sandbox/            # Sandbox executor (snekbox/nsjail)
├── skills/             # Skills SKILL.md (nextjs, github, n8n, etc.)
├── config/
│   ├── nginx/          # Reverse proxy + SSL
│   ├── supabase/       # Init SQL (pgvector, memories)
│   └── librechat/      # Configuración UI
├── webui/              # LibreChat config
├── scripts/            # install.sh, setup-ssl.sh
├── docs/               # Arquitectura técnica completa
├── docker-compose.yml  # Stack completo
└── .env.example        # Template de variables
```

## 🧩 Sistema de Skills

Las skills extienden las capacidades del agente. Añade nuevas copiando el patrón:

```bash
# Activar una skill en una petición
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Crea una app Next.js con auth", "skill_names": ["nextjs-supabase-dev"]}'
```

Skills incluidas:
- `nextjs-supabase-dev` — Next.js 15 + Supabase + Auth + RLS
- `github-ops` — Clone, PR, branch, commit automáticos
- `n8n-workflows` — Crear y ejecutar workflows n8n
- `code-review` — Revisión de código con mejoras
- `web-research` — Investigación web profunda

## 📊 Estimación de costos

| Componente | Costo/mes |
|---|---|
| Claude Code Max (ya lo tienes) | €200 |
| VPS 8-16GB RAM | €20-50 |
| nomic-embed-text (local) | €0 |
| Supabase self-hosted | €0 |
| n8n self-hosted | €0 |
| **TOTAL adicional** | **€20-50** |

## 🔗 API Endpoints

| Endpoint | Método | Descripción |
|---|---|---|
| `/v1/chat` | POST | Chat con agente (usa Max OAuth) |
| `/v1/chat/stream` | GET | Streaming SSE en tiempo real |
| `/ws/{session_id}` | WS | WebSocket bidireccional |
| `/v1/skills` | GET | Listar skills disponibles |
| `/v1/memory/search` | GET | Búsqueda semántica en memoria |
| `/v1/status` | GET | Health check del sistema |

## 📖 Documentación

- [Arquitectura técnica completa](docs/ARCHITECTURE.md)
- [Guía de instalación detallada](docs/INSTALLATION.md)
- [Crear skills personalizadas](docs/SKILLS.md)
- [Multi-agente: patrones y ejemplos](docs/MULTI_AGENT.md)
- [FAQ y troubleshooting](docs/FAQ.md)

## ⚠️ Limitaciones conocidas

1. **Rate limits del Max plan**: Compartidos entre claude.ai, Desktop y CLI. Max 20x tiene límites altos pero no ilimitados.
2. **Agent SDK no compatible**: El SDK Python requiere API Key. Usamos CLI subprocess como workaround.
3. **Subagentes comparten cuota**: Máximo 3-5 subagentes simultáneos recomendado.

## 📄 Licencia

MIT — Ver [LICENSE](LICENSE)
