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

## Hito 4 — Raíz de referencias y verificación periódica (`v0.4.0` = MVP)
- Opus: `rootmanifest.py`, trabajo `verify`, `schedule_maintenance`, GUI. 155 tests. La referencia acepta la raíz de solo referencias sobre carpetas de año (`info -v` y `verify` exit 0, XSD ok).
- **H14** — `MHLHistory.load_from_path` resuelve las referencias de todas las generaciones y falla con `assert referenced_hash_list is not None` si una apunta a un manifiesto que ya no existe (tras un Accept o al desaparecer un proyecto). Reproducir: `uv run pytest -q tests/test_rootmanifest.py -k superseded`. → D51.
- **H15** — Los patrones de ignore se comparan con rutas absolutas en la referencia: `/_*` no casa nunca; hay que usar `_*` sin anclar. Reproducir: test de patrones en `tests/test_rootmanifest.py`.
- **H16** — Un fichero suelto entre proyectos hace que `ascmhl-debug verify <raíz>` dé exit 21 («found new file»). Reproducir: e2e con el `00_README.md` de `SIN-CATEGORIA_CLIENTE-E`. Consecuencia de D49: el archivo debe estar ordenado; la GUI lo señala como entrada fuera de sitio.
- **H17** — Docker Desktop de este Mac no alcanza Docker Hub ni GHCR (proxy `http.docker.internal:3128`; `docker pull` se cuelga). Reproducir: `docker info --format '{{.HTTPProxy}}'` y `docker pull python:3.12-slim-bookworm`. El contenedor se prueba con el job `smoke` de `release.yml`.
- **H18** — La imagen `0.4.0` publicada no arrancaba: `ModuleNotFoundError: mhl_sentinel`. `uv sync` instala el proyecto en modo editable (un `.pth` hacia `/app/src`) y la etapa final del Dockerfile solo copia `.venv`. Lo cazó el job `smoke` de `release.yml` en su primera ejecución; `v0.4.1` instala con `--no-editable`. Reproducir: `docker run --rm ghcr.io/maxlainz/mhl-sentinel:0.4.0 mhl-sentinel --help`.
- Vault: `MHL (Media Hash List)` ampliada (H13, generación parcial, historial apartado); `Historial ASC MHL anidado` ampliada (H14, H15, H16).

## Pendiente tras el MVP
- Owner: hacer público el paquete en GHCR; abrir la raíz del archivo real con MediaVerify (issue #3); medir sobre el NAS (norma `prediccion-antes-de-medir.md`): tiempo de scan de ~100 proyectos y MB/s de hashing en el QNAP; actualizar el protocolo del estudio.
- Repo: issue #2 (upstream, D47); plantilla QNAP probada en Container Station; notificaciones Apprise (D22).

## Siguiente paso
Instalar `v0.4.0` en el QNAP con `deploy/docker-compose.yml`, predecir y medir (bitácora 03), y pulsar `Seal` en un proyecto pequeño antes de sellar el backlog.
