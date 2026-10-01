# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Versionado: [SemVer](https://semver.org/lang/es/). Categorías: Añadido · Cambiado · Corregido · Decidido · Medido · Eliminado.

## [Unreleased]

## [0.4.1] - 2026-10-01
### Corregido
- La imagen `0.4.0` no arrancaba (`ModuleNotFoundError: mhl_sentinel`): `uv sync` instalaba el paquete en modo editable apuntando a `/app/src`, que no existe en la etapa final. Ahora se instala con `--no-editable`. Lo detectó la prueba de humo del pipeline (H18).

## [0.4.0] - 2026-10-01 — Hito 4: raíz de referencias y verificación periódica (MVP)
### Añadido
- Hito 4: `rootmanifest.py` (historial de solo referencias en la raíz, D29/D43/D51), trabajo `verify` (relectura completa cada 90 días escalonada, regla mtime para distinguir corrupción, generación `verified` como prueba, D8/D23) y planificador de mantenimiento; GUI con fecha de la raíz, «verified» por proyecto y tabla de resultados por fichero en revisión. 155 tests. Prueba de humo del contenedor publicado en `release.yml`.
### Decidido
- D51: la raíz reinicia su historial cuando una referencia deja de existir; referencia todo proyecto con historial.
### Medido
- H14: la referencia resuelve las referencias de todas las generaciones y revienta con un assert si alguna apunta a un manifiesto inexistente. H15: compara patrones de ignore con rutas absolutas (`/_*` no casa). H16: `verify` en la raíz da exit 21 ante un fichero suelto entre proyectos. H17: Docker Desktop de este Mac no alcanza ningún registro (proxy `http.docker.internal:3128`); la imagen se prueba en GitHub.

## [0.3.0] - 2026-10-01 — Hitos 2 y 3: daemon, Docker y GUI
### Añadido
- Hito 2, daemon: `events.py` (bus entre hilos hacia SSE), `settings_ref.py`, `supervisor.py` (bucle asyncio + hilo hasher con puerta por horario laboral, D33; `Run scan now` en cualquier momento, D26; recuperación de trabajos al arrancar), `server.py` (uvicorn con SIGTERM limpio: termina el bloque, reencola y sale con 0), `mhl-sentinel serve`. Imagen `v0.1.0` publicada en GHCR por `release.yml` (amd64 + arm64) como prueba del pipeline.
- Hito 3, GUI: `web/` (FastAPI + Jinja2 + HTMX + SSE + Pico.css) con la pantalla única (D11), detalle de revisión (D46), Ajustes (D45) con guardado a YAML y recarga en caliente, `/healthz`, `/api/status`, `/api/projects`, `/events`. Probado de extremo a extremo sobre los fixtures: `Seal` desde la GUI → trabajo → manifiesto aceptado por `ascmhl-debug verify`.

## [0.1.0] - 2026-10-01 — Hito 1: núcleo sin GUI
### Añadido
- Hito 1, núcleo sin GUI: `config.py` (defaults < YAML < env `MHLS_`, horario laboral y zona horaria), `db.py` (SQLite WAL, rechazo de sistemas de ficheros de red), `schedule.py`, `discovery.py` (niveles, D18/D19/D49), `scanner.py` (snapshot y diff con regla de estabilidad), `legacy_mhl.py` (MHL 1.x con normalización de `xxhash64`), `hasher.py` (un lector, pausa/parada, caché por fichero), `sealer.py` (máquina de estados, seal/append/accept, comprobación y herencia de hashes legacy, manifiestos huérfanos), CLI `mhl-sentinel run-once|projects|settings`. 105 tests, e2e sobre fixtures validado por `ascmhl-debug verify`.
- Empaquetado para el hito 2: `deploy/Dockerfile` multi-stage, `entrypoint.sh` PUID/PGID, `docker-compose.yml`, guía QNAP, `release.yml` (GHCR multi-arch), assets de GUI vendorizados (Pico 2.1.1, htmx 2.0.11).
### Decidido
- D44–D50: MVP hasta el hito 4; bocetos de Ajustes y revisión; issues upstream aplazados; generaciones parciales al añadir (D48); detección solo por niveles (D49); historial apartado en `ascmhl_superseded/` (D50).
### Medido
- H13: la referencia carga `ascmhl/` en recursivo; un historial viejo dentro rompe `info`/`verify`.

## [0.0.1] - 2026-10-01 — Hito 0: arranque
### Añadido
- Proyecto Python (`pyproject.toml`, `uv`, Python 3.12) con `ascmhl==1.2` fijado como dependencia y oráculo (D1, D36); `mhl_sentinel.__version__` desde los metadatos del paquete.
- `make fixtures`: generador determinista de un archivo sintético (`scripts/make_fixtures.py`, salida gitignored) con proyectos de plantilla, carpetas a ignorar, basura de macOS y dos MHL 1.x de origen (xxhash64be y md5) con hashes reales; test de determinismo.
- Targets reales del Makefile (`setup`, `lint`, `format`, `typecheck`, `test`) sobre `uv`; `ci.yml` en push/PR (D38).
- Skill `release` (modelo: LMT-Composer) adaptada al stack.
- `src/mhl_sentinel/mhlwriter.py`: escritura de generaciones ASC MHL con hashes precalculados vía `mhllib`, raíz de solo referencias serializada con lxml, commit atómico (temporal + rename); 7 tests que validan cada manifiesto con `ascmhl-debug xsd-schema-check` y `verify`. XSD de la referencia (tag v1.2) copiados en `tests/xsd/`.
### Decidido
- D42: `mhllib` sirve para D28/D29 con escritura propia de la raíz y del commit. D43: la raíz no lleva `roothash` hasta el hito 4.
### Medido
- H7–H12: el escritor de la referencia siempre emite `<hashes>`; `append_multiple_format_file_hashes` está roto; `verify -dh` revienta sin `roothash` y compara contra todas las generaciones; `#recycle` sin escapar es un comentario gitignore; una referencia sin hijo corta la resolución del resto. Detalle en bitácora 01.
- Esqueleto del repo: router `CLAUDE.md`, normas en `.claude/rules/`, hooks en `.claude/settings.json`, docs de decisiones, roadmap, contexto del archivo, dos informes de research, bitácora 00.
- Normas `obsidian.md` y `vault-accesible.md`, skill `obsidian-vault`; sección «Notas de Obsidian clave» en el router.
### Decidido
- D1–D6: base ASC MHL con la referencia como oráculo; contenedor Docker con GUI mínima; repo público desde el día 0; workflow heredado de la familia de repos; git (Conventional Commits, SemVer); alcance funcional v1.
- D27: el vault de Obsidian es la base de conocimiento; área nueva `#archivo`, nota de proyecto `MHL Sentinel`, 9 conceptos documentados.
- D28–D41 (entrevista técnica): hasher propio + `mhllib`; raíz de solo referencias; xxh128; settle 168 h y `Seal` para cualquier proyecto sin manifiesto; contenedor en el NAS (QNAP x86-64); se configura el horario laboral y la app se detiene en él; sin throttle; sin login; stack en bloque (Python 3.12, FastAPI+HTMX, sqlite3, `MHLS_`); sin atribución en commits; CI en push/PR/tags; MHL 1.x de origen se verifican y heredan; el nombre del estudio no aparece; primer commit y repo público.
- D7–D26 (entrevista de producto): genérico desde el día 0; promesa integridad + completitud + prueba; añadir automático, modificar/borrar a revisión humana; GUI de una pantalla en inglés para producción; solo `ascmhl/` en el proyecto; sin informes, exclusión por tipo de fichero; preexistentes con botón `Seal`; revisión con `Accept as new version` / `Postpone`; detección por niveles de carpeta; exclusiones por defecto + `Ignore`; nombre MHL Sentinel; MIT; avisos en GUI (Apprise después); verificación cada 90 días; segunda copia fuera; ajustes en GUI; `Run scan now` solo escanea.
