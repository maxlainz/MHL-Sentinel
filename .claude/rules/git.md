# Git
*Norma del owner, 2026-10-01 (D5, confirmada en D37).*

- Conventional Commits en inglés con scope: `feat(scanner): ...`, `fix(mhl): ...`, `docs(rules): ...`, `chore(release): vX.Y.Z`, `test(...)`, `ci(...)`. Cuerpo en prosa explicando el porqué, citando `Dn`, `Hn` o `#issue`.
- Un commit por tarea. El repo nunca se deja roto: `make ci` verde antes de cada commit.
- SemVer. Hito 0 = `v0.0.1`, hito N = `v0.N.0`. Tags anotados `vX.Y.Z`; nunca se reescribe un tag publicado.
- Ramas: `feat/<issue#>-slug`, `fix/...`, `docs/...`. PR con `Closes #N`. Las ramas integradas se borran sin preguntar (`gh pr merge --delete-branch`). Durante el hito 0 se puede commitear directo a `main`.
- Sin trailers de atribución (`Co-Authored-By`, `Signed-off-by`): `includeCoAuthoredBy: false` en `.claude/settings.json`. Confirmado para este repo público (D37).
- Nunca `git push --force` a `main`.

**Por qué:** historial legible por humanos y por `git log --grep`, y releases reproducibles desde el tag.
