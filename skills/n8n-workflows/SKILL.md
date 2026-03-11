---
name: n8n-workflows
description: >
  Crea, gestiona y ejecuta workflows de automatización en n8n.
  Integra el agente con triggers externos y automatiza tareas repetitivas.
  Activar cuando se necesite automatización, webhooks o integraciones n8n.
license: MIT
metadata:
  version: "1.0.0"
  category: automation
x-tools-required:
  - Bash
  - WebFetch
x-system-prompt-addition: |
  Cuando trabajes con n8n:
  - La API de n8n está en http://n8n:5678/api/v1/
  - Autenticación: header X-N8N-API-KEY: ${N8N_API_KEY}
  - Para webhooks: http://n8n:5678/webhook/WEBHOOK_PATH
  - El agente puede ser llamado desde n8n via HTTP Request node a http://agent-api:8000/v1/chat
---

# n8n Workflow Automation

## Trigger de workflow via webhook

```bash
# Ejecutar workflow con webhook
curl -X POST http://n8n:5678/webhook/MI_WEBHOOK_PATH \
  -H "Content-Type: application/json" \
  -d '{"task": "descripcion de la tarea", "session_id": "n8n-session"}'
```

## Llamar al agente desde n8n

En n8n, usa un nodo **HTTP Request**:
- Method: POST
- URL: `http://agent-api:8000/v1/chat`
- Body: `{"message": "{{ $json.task }}", "session_id": "n8n-auto"}`

## Crear workflow via API

```bash
curl -X POST http://n8n:5678/api/v1/workflows \
  -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Agent Auto Task",
    "nodes": [
      {"type": "n8n-nodes-base.webhook", "name": "Trigger", "position": [0,0]},
      {"type": "n8n-nodes-base.httpRequest", "name": "Call Agent", "position": [200,0]}
    ],
    "connections": {},
    "active": true
  }'
```

## Listar workflows activos

```bash
curl -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
  http://n8n:5678/api/v1/workflows?active=true
```
