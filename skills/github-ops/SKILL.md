---
name: github-ops
description: >
  Gestiona repos GitHub: clone, branch, commit, push, PR, issues.
  Automatiza flujos de trabajo de Git y GitHub API.
  Activar para cualquier operación de control de versiones o GitHub.
license: MIT
metadata:
  version: "1.0.0"
  category: devops
x-tools-required:
  - Bash
  - Read
  - Write
x-system-prompt-addition: |
  Cuando trabajes con GitHub:
  - Usa GITHUB_TOKEN del entorno para autenticación
  - Crea branches descriptivos: feat/, fix/, chore/, docs/
  - Commits en formato Conventional Commits: feat(scope): descripción
  - PRs con descripción clara: qué cambia, por qué, cómo testar
  - Siempre hacer git status antes de commit
  - Nunca hardcodear credenciales en el código
---

# GitHub Operations

## Flujo de trabajo estándar

```bash
# Clonar repo
git clone https://${GITHUB_TOKEN}@github.com/OWNER/REPO.git
cd REPO

# Crear branch para feature
git checkout -b feat/nueva-funcionalidad

# Desarrollar... luego commit
git add -A
git commit -m "feat(auth): add JWT refresh token endpoint"

# Push con tracking
git push -u origin feat/nueva-funcionalidad
```

## Crear PR via GitHub API

```bash
curl -s -X POST \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  -H "Content-Type: application/json" \
  https://api.github.com/repos/OWNER/REPO/pulls \
  -d '{
    "title": "feat: nueva funcionalidad",
    "body": "## Cambios\n- Item 1\n\n## Testing\n- Tests añadidos",
    "head": "feat/nueva-funcionalidad",
    "base": "main"
  }'
```

## Crear Issue

```bash
curl -s -X POST \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  https://api.github.com/repos/OWNER/REPO/issues \
  -d '{"title": "Bug: ...", "body": "...", "labels": ["bug"]}'
```

## Conventional Commits

- `feat:` nueva funcionalidad
- `fix:` corrección de bug
- `docs:` documentación
- `chore:` mantenimiento
- `refactor:` refactorización
- `test:` añadir tests
- `perf:` mejora de rendimiento
