# 03 — Patch `v0.4.3` (Cancel) y propuestas de frontend (2026-10-01)

**TL;DR.** Dos encargos del owner en paralelo: (1) poder cancelar un `Seal` programado para fuera de horario, llevado a producción como patch `v0.4.3` (D53, issue #4, PR #6); (2) rehacer el frontend entero: cuatro subagentes Opus con libertad creativa producen cuatro propuestas independientes (ramas `feat/5-frontend-a..d`, documento con bocetos en texto en `docs/propuestas/`), se levantan en local en cuatro puertos y el owner elige (issue #5).

## Cancel (D53)
- `sealer.request_cancel` + `sealer.cancellable_job`: solo un `seal` o `accept_new_version` **manual** en cola. El job pasa a `cancelled`, el proyecto vuelve a `unsealed` o `needs_review` (conservando `review_reason`), los hashes de la caché (D28) se conservan. Un trabajo parado por el horario laboral vuelve a la cola y por tanto también se puede cancelar.
- No se cancelan trabajos automáticos (el siguiente ciclo los reencolaría) ni en marcha (exigiría abortar el hilo hasher; otro issue si hace falta).
- `POST /projects/{id}/cancel`; botón `Cancel` en la tarjeta. Tests en `test_sealer.py` y `test_web.py`; 161 tests.
- Vault: sin conceptos nuevos (cancelar un trabajo en cola es mecánica de la app, no concepto de archivo).

## Frontend (#5)
- Restricciones dadas a los subagentes: sin build de Node (D36), inglés (D12), mismas rutas y eventos SSE, sin tocar `sealer`/`supervisor`/`db`, `make ci` verde, boceto en texto de cada pantalla (D4).
- Direcciones sugeridas (con libertad para desviarse): A libre, B lenguaje de Silverstack/Hedge o tablero tranquilo, C tabla densa con panel lateral y tema oscuro, D bandeja de entrada / triaje.
- Resultado y elección del owner: se registran en esta entrada al cerrar.

## Siguiente paso
Presentar las cuatro propuestas (URLs locales y capturas), registrar la elección como `Dn`, integrar la elegida en `main` adaptando `tests/test_web.py`, y borrar las otras ramas.
