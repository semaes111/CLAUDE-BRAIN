---
name: web-research
description: >
  Investigación web profunda: busca, analiza y sintetiza información de múltiples fuentes.
  Ideal para investigar tecnologías, documentación, best practices y comparativas.
  Activar cuando se necesite información actualizada de Internet.
license: MIT
metadata:
  version: "1.0.0"
  category: research
x-tools-required:
  - WebSearch
  - WebFetch
x-system-prompt-addition: |
  En investigación web:
  - Usa WebSearch para encontrar fuentes relevantes
  - Usa WebFetch para leer el contenido completo de páginas clave
  - Sintetiza información de múltiples fuentes (mínimo 3)
  - Cita las fuentes con URL
  - Distingue entre información verificada y especulativa
  - Prioriza documentación oficial sobre blogs o foros
---

# Web Research Methodology

## Proceso de investigación
1. **Búsqueda inicial**: 2-3 queries diferentes para encontrar fuentes
2. **Evaluación**: Priorizar docs oficiales, papers, repos activos
3. **Lectura profunda**: WebFetch en las 3-5 fuentes más relevantes
4. **Síntesis**: Combinar información, identificar consenso y discrepancias
5. **Reporte**: Conclusión clara con fuentes citadas

## Tipos de búsqueda
- Documentación oficial: `site:docs.example.com [topic]`
- Últimas noticias: `[topic] 2025 release`
- Comparativas: `[A] vs [B] benchmark 2025`
- Best practices: `[topic] best practices production`

## Template de reporte de investigación

```markdown
## Research Report: [Tema]

### TL;DR
Resumen ejecutivo en 2-3 oraciones.

### Hallazgos principales
1. ...
2. ...

### Fuentes consultadas
- [Título](URL) — descripción breve
- ...

### Conclusiones y recomendaciones
...
```
