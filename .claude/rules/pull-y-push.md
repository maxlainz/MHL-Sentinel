# Pull al abrir, push al cerrar
*Norma del owner, 2026-10-01 (D5).*

- Al abrir sesión: `git fetch --prune` y `git pull --ff-only` si el árbol está limpio (hook `SessionStart`).
- Al cerrar sesión y tras cada tarea commiteada: `git push` de la rama actual (hook `Stop`).
- `main` está protegida (norma `rama-main-protegida.md`): un push a `main` siempre falla; el trabajo va en ramas y entra por PR.
- Si `main` ha divergido: `git cherry origin/main main`; si no hay líneas `+`, `git reset --hard origin/main`; si las hay, preguntar al owner.
- Ramas de trabajo se rebasan sobre `origin/main` antes de abrir el PR.
- Sin remoto configurado (hito 0 antes de publicar) los hooks no hacen nada.

**Por qué:** el owner trabaja desde varias máquinas; un push olvidado es trabajo perdido.
