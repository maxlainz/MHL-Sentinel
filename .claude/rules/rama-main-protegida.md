# `main` está protegida: nada entra sin PR y CI verde
*Norma del owner, 2026-10-01 (D55).*

- Nadie commitea en `main`, ni personas ni agentes, ni para docs, ni para una línea. Todo cambio nace en una rama (`feat/<issue#>-slug`, `fix/...`, `docs/...`, `chore/release-vX.Y.Z`) y entra por PR con la CI de GitHub verde (`ci.yml`) y `make ci` local.
- La protección está activa en GitHub (`gh api repos/maxlainz/MHL-Sentinel/branches/main/protection`): PR obligatorio, check `ci` obligatorio, rama al día con `main` antes de integrar, sin force-push ni borrado, aplicada también al administrador. No se desactiva «un momento».
- Un PR se integra con `gh pr merge --squash --delete-branch` cuando la CI está verde; el owner trabaja solo, así que no hay revisor obligatorio: la revisión la hacen los agentes antes de abrir el PR (norma `subagentes.md`).
- Las releases también pasan por PR: el commit `chore(release): vX.Y.Z` va en `chore/release-vX.Y.Z`, se integra, y el tag anotado se pone sobre el commit resultante en `main` (skill `release`).
- El hook `Stop` sigue empujando la rama actual; si la rama es `main` y hay commits locales, el push fallará: es la señal de que algo se hizo mal. Se mueve el commit a una rama (`git branch fix/... && git reset --hard origin/main`) y se abre PR.

**Por qué:** durante el MVP se commiteó directo en `main` por velocidad; con la app instalada en el NAS y auto-actualizable (D52), un commit roto en `main` no publica imagen, pero sí rompe el punto de partida de la siguiente sesión y el historial que `latest` promete.
