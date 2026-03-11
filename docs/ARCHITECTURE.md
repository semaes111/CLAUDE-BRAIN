# Arquitectura técnica — CLAUDE-BRAIN

## El insight crítico de billing

```
┌──────────────────────────────────────────────────────────┐
│              MAPA DE BILLING DE ANTHROPIC                  │
├──────────────────────────────┬───────────────────────────┤
│  INCLUIDO en Max ($200/mes)  │  COBRA EXTRA (API billing) │
├──────────────────────────────┼───────────────────────────┤
│  claude --print "tarea"      │  anthropic.Anthropic()     │
│  claude -p "tarea"           │  claude-agent-sdk          │
│  Claude Desktop              │  curl /v1/messages         │
│  claude.ai web               │  @anthropic-ai/sdk         │
└──────────────────────────────┴───────────────────────────┘

SOLUCIÓN: subprocess("claude --print") sin ANTHROPIC_API_KEY
→ El CLI usa OAuth del Max plan → $0 adicional
```

**Dato real**: 10B tokens en 8 meses = $15,000 en API vs $800 con Max (93% ahorro).

## Componentes del sistema

### ClaudeMaxRunner (agent/core/claude_runner.py)

El motor central. Invoca `claude --print` vía `asyncio.create_subprocess_exec`.

**Clave**: El `_build_env()` construye el entorno de ejecución **sin** `ANTHROPIC_API_KEY`.
Sin esa variable, el CLI automáticamente usa las credenciales OAuth almacenadas en `~/.claude/.credentials.json`.

```
Usuario → FastAPI → ClaudeMaxRunner
                        ↓
              subprocess("claude --print <task>")
                        ↓
              ~/.claude/.credentials.json (OAuth Max)
                        ↓
              claude.ai API (cubierto por Max plan)
```

### Sistema de Memoria (3 capas)

```
Working Memory (Redis)          ← Conversación activa, TTL 24h
      ↕
Long-term Memory (pgvector)     ← Búsqueda semántica, persistente
      ↑
Extracción automática (Claude)  ← Claude analiza conversaciones y extrae memorias clave
```

**Embeddings**: `nomic-embed-text-v1.5` local via HuggingFace TGI
- 768 dimensiones
- 8192 tokens de contexto  
- ~270MB RAM
- $0/mes (vs $5-20/mes con OpenAI)

### Sistema de Skills (SKILL.md)

Progressive disclosure para gestionar el contexto del agente:

```
Carga siempre:    Índice (~50 tokens/skill) → Claude sabe qué skills hay
Carga on-demand:  Contenido completo → Solo cuando la skill se activa
```

Formato SKILL.md estándar 2025 (compatible con Claude Code, Cursor, Gemini CLI):
```yaml
---
name: nombre-skill
description: descripción concisa
x-tools-required: [Read, Write, Bash]
x-system-prompt-addition: |
  instrucciones adicionales para el system prompt
---
# Contenido completo de la skill...
```

### Coordinación Multi-Agente

```
Orquestador (Claude analiza la tarea)
    ↓
  Decide estrategia:
  • simple   → 1 agente directo
  • parallel → N subagentes concurrentes (asyncio.gather)
  • pipeline → N subagentes secuenciales (output → input)
    ↓
  Semáforo asyncio (max 4 concurrent) → evita rate limit
    ↓
  Síntesis: Claude combina los resultados
```

Cada subagente = un proceso `claude --print` independiente.
Todos usan Max OAuth → $0 por subagente.

### Sandbox de Ejecución

Python vía **snekbox** (nsjail):
- Sandbox a nivel de kernel con seccomp + namespaces
- Filesystem de solo lectura
- Sin acceso a red
- Sin root capabilities

JavaScript/Bash vía **Docker** con restricciones:
```
--network=none --read-only --memory=256m --cpus=0.5
--pids-limit=64 --cap-drop=ALL --user=nobody
--tmpfs=/tmp:size=50m,noexec
```

## Flujo de una petición completa

```
1. Usuario → POST /v1/chat {message, session_id, skill_names}
2. API → MemoryManager.build_context(session_id, message)
          → embed(message) con nomic-embed-text local
          → Supabase RPC match_memories() → top 5 memorias relevantes
          → Redis get_history() → últimos 20 mensajes
3. API → SkillManager.build_task_prompt(message, skills, memory_ctx)
          → Carga contenido SKILL.md de skills activas
          → Construye prompt XML estructurado
4. API → ClaudeMaxRunner.run_with_tools(task, tools)
          → subprocess("claude --print --allowedTools ... <task>")
          → Sin ANTHROPIC_API_KEY → OAuth Max → $0 extra
5. API → Guardar en Redis (historial)
          → asyncio.create_task(extract_and_save) → async, no bloquea
6. API → Retornar respuesta al usuario
```

## Stack tecnológico

| Componente | Tecnología | Costo |
|---|---|---|
| Motor IA | Claude Code Max CLI (OAuth) | $0 extra |
| API REST | FastAPI + uvicorn | $0 |
| Web UI | LibreChat | $0 |
| Working Memory | Redis 7 | $0 |
| Long-term Memory | Supabase (self-hosted) + pgvector | $0 |
| Embeddings | nomic-embed-text-v1.5 (HF TGI) | $0 |
| Sandbox Python | snekbox (nsjail) | $0 |
| Sandbox JS/Bash | Docker con restricciones | $0 |
| Automatización | n8n (self-hosted) | $0 |
| Proxy | nginx | $0 |
| SSL | Let's Encrypt (certbot) | $0 |

## Estimación de recursos VPS

| Servicio | RAM mínima | RAM recomendada |
|---|---|---|
| Agent API | 512MB | 1GB |
| LibreChat + MongoDB | 512MB | 1GB |
| Redis | 256MB | 512MB |
| Supabase (DB+Auth+REST+Kong+Studio) | 2GB | 3GB |
| n8n | 256MB | 512MB |
| Embeddings (nomic) | 1.5GB | 2GB |
| Sandbox (snekbox) | 256MB | 512MB |
| nginx | 64MB | 128MB |
| **Total** | **~5.5GB** | **~9GB** |

**VPS recomendado**: 16GB RAM, 4 vCPUs, 80GB SSD — ~€40-60/mes
