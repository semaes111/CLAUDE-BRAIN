# 🧠 CLAUDE-BRAIN

> Agente IA autónomo con **Claude Code Max** — 100% Docker, cero instalación en el host, cero API billing extra.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue)](docker-compose.yml)
[![Claude Code Max](https://img.shields.io/badge/Claude%20Code-Max%20OAuth-orange)](https://claude.ai)

## 🎯 Concepto

El agente corre **completamente dentro de Docker**. El host solo necesita Docker instalado.

```
HOST (VPS o local)
└── Docker Engine
    ├── cb-agent      ← claude CLI instalado aquí (Max OAuth)
    ├── cb-redis      ← Working memory
    ├── cb-postgres   ← Long-term memory (pgvector)
    ├── cb-embeddings ← nomic-embed-text-v1.5 ($0/mes)
    ├── cb-n8n        ← Automatización
    ├── cb-webui      ← LibreChat UI
    ├── cb-snekbox    ← Sandbox Python (nsjail)
    └── ...
```

### Por qué $0 de API extra

```
❌  anthropic.Anthropic()   → Cobra por token (API billing separado)
✅  claude --print "tarea"  → Usa Max OAuth ($0 extra)
```

El `ClaudeMaxRunner` invoca el CLI vía `subprocess` **sin** `ANTHROPIC_API_KEY` en el entorno.
Sin esa variable, el CLI usa automáticamente las credenciales OAuth del Max plan.

## 🚀 Inicio rápido

### Prerequisitos
- Docker Engine >= 24 + Docker Compose v2
- Cuenta Claude Code Max ($100 o $200/mes)
- Eso es todo

### Setup en 3 pasos

```bash
# 1. Clonar
git clone https://github.com/semaes111/CLAUDE-BRAIN.git
cd CLAUDE-BRAIN

# 2. Configurar
cp .env.example .env
# Editar .env: añadir GITHUB_TOKEN, credenciales Supabase, etc.

# 3. Arrancar todo
chmod +x scripts/install.sh && ./scripts/install.sh
```

### Autenticar Claude Code Max (una vez)

**Opción A — Exportar credenciales existentes (recomendada):**
```bash
# Si ya tienes Claude Code autenticado en tu máquina:
./scripts/setup-auth.sh
# → Detecta ~/.claude/.credentials.json y lo exporta al .env automáticamente
```

**Opción B — Auth dentro del contenedor:**
```bash
docker compose up -d --build
docker compose exec agent-api claude auth login
# → Genera URL → la abres en tu browser → autorizas
# → Credenciales guardadas en volumen Docker persistente (cb-claude-auth)
```

**Verificar:**
```bash
docker compose exec agent-api claude --print "di hola"
# → Responde sin pedir API key = está usando Max OAuth ✅
```

## 📡 Endpoints

| URL | Descripción |
|-----|-------------|
| `http://localhost:3080` | Web UI (LibreChat) |
| `http://localhost:8000` | Agent API |
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:5678` | n8n workflows |
| `http://localhost:8001` | Supabase Studio |

### Uso básico de la API

```bash
# Chat simple
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "crea un endpoint FastAPI para listar usuarios"}'

# Con skill activada
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "crea auth con Supabase", "skill_names": ["nextjs-supabase-dev"]}'

# Streaming en tiempo real
curl -N "http://localhost:8000/v1/chat/stream?message=explica+pgvector"

# Ejecutar código en sandbox
curl -X POST http://localhost:8000/v1/execute \
  -H "Content-Type: application/json" \
  -d '{"code": "print(sum(range(100)))", "language": "python"}'

# Estado del sistema
curl http://localhost:8000/v1/status
```

## 🧩 Skills incluidas

| Skill | Activar con |
|-------|-------------|
| `nextjs-supabase-dev` | Next.js 15 + App Router + Supabase + RLS |
| `github-ops` | Clone, PR, branch, commit automático |
| `n8n-workflows` | Crear y ejecutar workflows n8n |
| `code-review` | Auditoría de seguridad y calidad |
| `web-research` | Investigación web multi-fuente |

## 📁 Estructura

```
CLAUDE-BRAIN/
├── agent/
│   ├── core/claude_runner.py        ← Motor: subprocess("claude --print")
│   ├── memory/memory_manager.py     ← Redis + pgvector + extracción auto
│   ├── skills/skill_manager.py      ← SKILL.md loader
│   ├── orchestrator/multi_agent.py  ← Parallel/pipeline con semáforo
│   ├── api/main.py                  ← FastAPI REST + SSE + WebSocket
│   ├── entrypoint.sh                ← Auth OAuth en Docker
│   └── Dockerfile
├── sandbox/                         ← snekbox + Docker executor
├── skills/                          ← 5 skills SKILL.md
├── config/
│   ├── nginx/                       ← Reverse proxy + SSL
│   └── supabase/                    ← SQL pgvector + memorias
├── scripts/
│   ├── install.sh                   ← Setup en un comando
│   └── setup-auth.sh                ← Export credenciales Max
├── docs/ARCHITECTURE.md
├── docker-compose.yml               ← 11 servicios
└── .env.example
```

## 💰 Costos

| Componente | Costo |
|------------|-------|
| Claude Code Max (ya lo tienes) | €200/mes |
| VPS o servidor local | €20-60/mes |
| Todos los demás servicios | €0 (self-hosted en Docker) |
| **Total adicional** | **€20-60/mes** |

## 🔧 Comandos útiles

```bash
# Ver logs del agente
docker compose logs -f agent-api

# Reiniciar solo el agente
docker compose restart agent-api

# Abrir shell en el agente
docker compose exec agent-api bash

# Ver uso de recursos
docker stats

# Parar todo
docker compose down

# Parar y borrar volúmenes (¡elimina datos!)
docker compose down -v
```

## 📄 Licencia

MIT
