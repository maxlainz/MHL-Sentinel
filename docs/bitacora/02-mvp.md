# 02 — MVP: hitos 1 a 4 (2026-10-01)

**TL;DR.** Misma sesión que la 01, tras el encargo del owner: «continúa hasta tener el MVP publicado en GHCR» (D44: hitos 1–4). Preguntas hechas antes de cada ambigüedad de producto (D45–D50). Contrato de módulos en `docs/arquitectura.md`; implementación delegada por módulos (Opus para cimientos e integración, Sonnet para módulos bien especificados) y verificada por el orquestador con `make ci` y lectura del código. Esta entrada se amplía por hito.

## Hito 1 — Núcleo sin GUI (`v0.1.0`)
- Módulos: `config`, `db`, `clock`, `schedule`, `discovery`, `scanner`, `legacy_mhl`, `hasher`, `sealer`, `cli` (`run-once`, `projects`, `settings`). 105 tests; e2e: 5 proyectos de fixtures sellados, append parcial (D48), revisión por modificación, `Accept as new version`, legacy con discrepancia → revisión sin escribir nada; cada manifiesto validado por `ascmhl-debug verify` y `xsd-schema-check`.
- Decisiones del hito: D48 (append parcial), D49 (solo niveles), D50 (`ascmhl_superseded/`). Issue #1 cerrado (huérfanos renombrados a `*.mhl.orphan`).
- **H13** — `MHLHistory.load_from_path` recorre `ascmhl/` en recursivo y carga cualquier `.mhl` de subcarpetas como generación: un historial apartado dentro de `ascmhl/` hace fallar `info` y `verify` (FileNotFoundError). Reproducir: `uv run pytest -q tests/test_sealer.py -k superseded`.
- Reproducción manual del ciclo completo: ver docstring de `tests/test_e2e_run_once.py` o la secuencia `run-once` del informe (en la bitácora al cerrar el MVP).
- Vault: sin conceptos nuevos (H13 es un detalle de implementación de la referencia; se añade como limitación en `MHL (Media Hash List)` al cerrar el MVP).

## Hitos 2 y 3 — Daemon, Docker y GUI (`v0.3.0`; el hito 2 no cortó tag propio porque ambos llegaron a la vez)
- Hito 2 (Opus): `events`, `settings_ref`, `supervisor`, `server`, `serve`. 15 tests nuevos, incluido uno que lanza `serve` como subproceso, espera `/healthz`, envía SIGTERM y comprueba salida 0 con el trabajo reencolado y sin `ascmhl/` a medias.
- Hito 3 (Opus): `web/` con todas las rutas del contrato; 17 tests. Cableado por el orquestador: `notify_job_queued` tras `Seal`/`Accept`; recarga de ajustes con precedencia real conservando los campos solo-env (fallo detectado porque el gate se saltó por un `tail` que enmascaró el código de salida: un commit rojo en `main`, corregido en el siguiente; desde entonces el gate corre con `pipefail`).
- Prueba real en local sobre los fixtures (puerto 8089): `/healthz` 200, 5 proyectos, `Seal` por HTMX → `queued` → `sealed` con generación 1 en ~10 s, `ascmhl-debug verify` exit 0, Ajustes guarda `config.yaml` y el supervisor pasa a «working now» al cambiar a Europe/Madrid, SIGTERM cierra limpio.
- `release.yml` construyó y publicó `ghcr.io/maxlainz/mhl-sentinel:0.1.0` (amd64 + arm64) al primer intento. El paquete nace privado: hacerlo público es acción del owner (el token de `gh` local no tiene `read:packages`).
- Desvíos de contrato de ambos hitos registrados en `docs/arquitectura.md`.

## Siguiente paso
Hito 4 (`v0.4.0`): `rootmanifest.py`, trabajo `verify` escalonado (90 días), resultados en la GUI.
