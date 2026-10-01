# 02 — MVP: hitos 1 a 4 (2026-10-01)

**TL;DR.** Misma sesión que la 01, tras el encargo del owner: «continúa hasta tener el MVP publicado en GHCR» (D44: hitos 1–4). Preguntas hechas antes de cada ambigüedad de producto (D45–D50). Contrato de módulos en `docs/arquitectura.md`; implementación delegada por módulos (Opus para cimientos e integración, Sonnet para módulos bien especificados) y verificada por el orquestador con `make ci` y lectura del código. Esta entrada se amplía por hito.

## Hito 1 — Núcleo sin GUI (`v0.1.0`)
- Módulos: `config`, `db`, `clock`, `schedule`, `discovery`, `scanner`, `legacy_mhl`, `hasher`, `sealer`, `cli` (`run-once`, `projects`, `settings`). 105 tests; e2e: 5 proyectos de fixtures sellados, append parcial (D48), revisión por modificación, `Accept as new version`, legacy con discrepancia → revisión sin escribir nada; cada manifiesto validado por `ascmhl-debug verify` y `xsd-schema-check`.
- Decisiones del hito: D48 (append parcial), D49 (solo niveles), D50 (`ascmhl_superseded/`). Issue #1 cerrado (huérfanos renombrados a `*.mhl.orphan`).
- **H13** — `MHLHistory.load_from_path` recorre `ascmhl/` en recursivo y carga cualquier `.mhl` de subcarpetas como generación: un historial apartado dentro de `ascmhl/` hace fallar `info` y `verify` (FileNotFoundError). Reproducir: `uv run pytest -q tests/test_sealer.py -k superseded`.
- Reproducción manual del ciclo completo: ver docstring de `tests/test_e2e_run_once.py` o la secuencia `run-once` del informe (en la bitácora al cerrar el MVP).
- Vault: sin conceptos nuevos (H13 es un detalle de implementación de la referencia; se añade como limitación en `MHL (Media Hash List)` al cerrar el MVP).

## Siguiente paso
Hito 2 (`v0.2.0`): `events.py`, `supervisor.py`, `serve`, SIGTERM, imagen en GHCR. En paralelo, hito 3 (GUI) contra el contrato.
