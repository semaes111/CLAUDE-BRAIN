---
name: code-review
description: >
  Revisión exhaustiva de código: bugs, seguridad, performance, legibilidad.
  Genera sugerencias concretas y mejoras con ejemplos de código.
  Activar cuando se necesite revisar o auditar código existente.
license: MIT
metadata:
  version: "1.0.0"
  category: quality
x-tools-required:
  - Read
  - Glob
  - Grep
x-system-prompt-addition: |
  En revisiones de código:
  - Prioriza: 1) seguridad, 2) bugs, 3) performance, 4) legibilidad
  - Para cada issue: severidad (critical/high/medium/low), descripción, código corregido
  - Busca: SQL injection, XSS, auth bypass, race conditions, memory leaks
  - Sugiere tests para los casos encontrados
  - Usa formato claro con secciones por archivo
---

# Code Review Methodology

## Checklist de seguridad
- [ ] SQL injection / NoSQL injection
- [ ] XSS (Cross-Site Scripting)
- [ ] Auth bypass / missing authorization
- [ ] Secrets hardcodeados
- [ ] Input validation faltante
- [ ] Race conditions
- [ ] Dependency vulnerabilities

## Checklist de calidad
- [ ] Error handling completo
- [ ] Logging apropiado
- [ ] Tests unitarios e integración
- [ ] Documentación de funciones públicas
- [ ] Naming claro y consistente
- [ ] DRY (Don't Repeat Yourself)

## Formato de reporte

```markdown
## Code Review Report

### 🔴 Critical Issues
**[archivo:línea]** Descripción del problema
```código problemático```
**Fix:** ```código corregido```

### 🟡 Medium Issues
...

### 💡 Suggestions
...
```
