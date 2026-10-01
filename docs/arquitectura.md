# Arquitectura (contrato de módulos, hitos 1–4)

Fuente de verdad para quien implementa. Decisiones en `docs/decisiones.md`; conceptos en el vault (`Detección de cambios en un volumen de red`, `Quiescencia de ficheros (settle time)`, `Distinguir corrupción de modificación por mtime`, `Ventana de inactividad (quiet hours)`, `SQLite sobre un volumen de red`, `Historial ASC MHL anidado`). Se actualiza cuando cambia un contrato.

## Paquete `mhl_sentinel/`
| Módulo | Responsabilidad | Hito |
|---|---|---|
| `config.py` | `Settings` (pydantic-settings): defaults < `/config/config.yaml` < env `MHLS_` (anidado con `__`). `load_settings()`, `save_yaml()`. | 1 |
| `models.py` | Enums y dataclasses compartidos: `ProjectState`, `JobKind`, `JobState`, `ChangeKind`, `ScanResult`, `FileStat`. | 1 |
| `db.py` | `Database` sobre `sqlite3` (WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`, migraciones por `PRAGMA user_version`). Se niega a abrir si el directorio está en un FS de red (`/proc/mounts`: cifs, smb3, nfs, nfs4, fuse.sshfs). Un solo hilo escritor (lock). | 1 |
| `schedule.py` | `WorkingHours(days, start, end, tz)`: `is_working(now)`; `next_change(now)`. Franjas que cruzan medianoche. D33. | 1 |
| `discovery.py` | Lista proyectos: raíz + `project_depth` niveles (D18); ignora nombres con prefijo `_ @ # .` en cualquier nivel (D19) y los marcados `ignored` en DB; detecta `ascmhl/` existente. Marca `preexisting` a lo que aparece en la **primera** discovery de una instalación (D15). | 1 |
| `scanner.py` | Walk `os.scandir` por proyecto → `files` (relpath POSIX, size, mtime_ns); respeta los patrones de ignore del manifiesto y `exclude_globs` (D14). Diff contra `sealed_files` → added/modified/deleted (tolerancia mtime 1 s). Diff contra el scan anterior → `stable` (idéntico) y `newest_mtime`. | 1 |
| `legacy_mhl.py` | Lee MHL 1.x (`<hashlist version="1.0|1.1">`): `<file>`, `<size>`, `md5|sha1|xxhash64be|xxhash64|xxhash`. Devuelve `{relpath_del_proyecto: {formato_ascmhl: hex}}` normalizado: `xxhash64be`→`xxh64`; `xxhash64` (little-endian) → bytes invertidos → `xxh64`; `xxhash` (XXH32 decimal) se ignora con aviso. Rutas relativas a la carpeta del `.mhl`. D39. | 1 |
| `hasher.py` | `hash_file(path, formats, gate, stop, chunk=8 MiB)`: stat antes y después (si cambia → `FileChanged`); comprueba `gate.wait()`/`stop` entre bloques; al pausar cierra el fichero y el fichero se rehace entero al reanudar. `hash_project(...)` recorre los ficheros pendientes, salta los que tienen caché `(size, mtime_ns)` en `file_hashes`, escribe un checkpoint por fichero. Un solo lector (D34). | 1 |
| `mhlwriter.py` | Ya existe (hito 0). Añadir `partial=True`: generación solo con los ficheros dados, sin hashes de directorio ni `roothash`, sin comprobar completitud (D48). | 1 |
| `sealer.py` | Trabajos: `seal` (todos los ficheros, `original`, hereda hashes legacy como `verified`; cualquier discrepancia con un legacy → `needs_review`, nada se escribe), `append` (solo añadidos, parcial, D48), `accept_new_version` (mueve `ascmhl/` a `ascmhl_superseded/<AAAA-MM-DDTHHMMSSZ>/` (D50, H13: la referencia carga `ascmhl/` en recursivo) y hace `seal`; se hashea y se comprueba lo legacy **antes** de apartar el historial, así una revisión deja el historial intacto), `verify` (hito 4: relee todo, compara con `sealed_files`; todo igual → generación completa `verified`; mtime igual y hash distinto → `corrupt`; mtime distinto → `modified`; ambos → `needs_review`). Tras cada generación actualiza `sealed_files` desde el manifiesto escrito. | 1, 4 |
| `supervisor.py` | Bucle: cada `tick` (60 s) si no es horario laboral (o hay petición manual de scan): discovery si toca → scan de proyectos que tocan → transiciones → encolar. Hilo hasher consume `jobs` por prioridad (manual > seal > append > verify) solo con `gate` abierto. SIGTERM → `stop`, termina el bloque, guarda checkpoint, no escribe generación. Recupera trabajos `running` al arrancar (→ `queued`). | 2 |
| `rootmanifest.py` | Raíz de solo referencias (D29, D43), regenerada cuando cambia cualquier historial de proyecto. | 4 |
| `cli.py` | `mhl-sentinel run-once [--ignore-working-hours]` (hito 1), `serve` (hito 2), `settings show`. | 1, 2 |
| `web/` | FastAPI + Jinja2 + HTMX + SSE; D11, D45, D46. | 3 |
| `events.py` | `EventBus.publish(event, payload)` → SSE y `job_log`; punto único para Apprise después (D22). | 2 |

## Estados de un proyecto (`ProjectState`)
```
unsealed   sin ascmhl/. Si preexisting: solo botón Seal (D15). Si no: se encola solo tras settle_hours sin cambios (D31). Seal siempre disponible (D31).
queued     sellado/append pedido; espera a horario no laboral.
hashing    trabajo en curso (pausado en horario laboral).
sealed     tiene historial y el último scan coincide con sealed_files.
changed    solo ficheros añadidos desde el último sellado → append automático tras settle_hours (D9).
needs_review ficheros modificados o borrados (D9) o discrepancia con un legacy o corrupción en verify: nada se escribe hasta Accept/Postpone (D17).
ignored    botón Ignore (D19).
error      excepción registrada en jobs.error; se reintenta al siguiente tick.
```
Transiciones las decide `sealer.classify(project, scan_diff, settings, now)`; el scan no cambia estados por sí solo. Detalles fijados en el hito 1: un scan con errores de lectura deja el proyecto en `error` sin clasificar (un fichero ilegible parecería borrado); una carpeta que desaparece del disco → `error "folder missing"`, la fila no se borra; en la comprobación legacy un fichero que falta es `deleted` y uno con hash distinto `modified`; `append` también comprueba legacy sobre los añadidos; `Accept` no se salta la comprobación legacy; un historial ASC MHL creado por otra herramienta aparece como `unsealed` y `Seal` le añade una generación completa (si no cuadra, revisión). Los `exclude_globs` (D14) se escriben como patrones de ignore del manifiesto. Prioridades de cola: manual +100; `seal`/`accept` 30, `append` 20, `verify` 10. Manifiestos huérfanos (`ascmhl/*.mhl` fuera de la cadena) se renombran a `*.mhl.orphan` en cada scan (issue #1).

## Tablas (`db.py`, `user_version` 1)
```sql
projects(id INTEGER PK, rel_path TEXT UNIQUE, name TEXT, state TEXT, preexisting INTEGER, first_seen TEXT, last_scan_at TEXT,
         last_change_at TEXT, stable_since TEXT, last_generation_no INTEGER, last_sealed_at TEXT, last_verified_at TEXT,
         file_count INTEGER, total_bytes INTEGER, error TEXT, review_reason TEXT)
files(project_id, rel_path, size, mtime_ns, PRIMARY KEY(project_id, rel_path))            -- último scan
sealed_files(project_id, rel_path, size, mtime_ns, xxh128, PRIMARY KEY(project_id, rel_path)) -- lo que dice el último manifiesto
file_hashes(project_id, rel_path, size, mtime_ns, fmt, digest, hashed_at, PRIMARY KEY(project_id, rel_path, fmt)) -- checkpoint (D28)
scans(id PK, project_id NULL, kind, started_at, finished_at, files, bytes, added, modified, deleted, status)
jobs(id PK, kind, project_id, trigger, state, priority, created_at, started_at, finished_at, files_done, files_total, bytes_done, bytes_total, error)
job_log(job_id, ts, level, msg)
review_items(project_id, rel_path, change, old_size, new_size, old_mtime_ns, new_mtime_ns, PRIMARY KEY(project_id, rel_path))
verify_results(job_id, project_id, rel_path, expected, actual, status)   -- ok|modified|corrupt|missing (hito 4)
settings_kv(key PK, value)   -- p. ej. first_discovery_done, ignored overrides
```
Fechas ISO-8601 UTC. Rutas POSIX relativas (a la raíz para `projects.rel_path`, al proyecto para el resto).

## Configuración (`Settings`, defaults)
```yaml
archive_root: /archive            # fijo por el montaje; solo env
project_depth: 1                  # D18
ignore_prefixes: "_@#."           # D19
exclude_globs: []                 # D14, p. ej. ["*.md", "*.txt"]; solo crecen (spec)
working_hours: {days: [mon,tue,wed,thu,fri], start: "09:00", end: "19:00"}   # D33
timezone: UTC                     # zona del horario laboral (env MHLS_TIMEZONE); el proceso corre con TZ=UTC
settle_hours: 168                 # D31
verify_interval_days: 90          # D23
scan_interval_minutes: 60
log_level: info
hash_format: xxh128               # D30, no editable en GUI
config_dir: /config               # config.yaml + state.db; solo env
port: 8080                        # solo env
```

## Reglas transversales
- Nunca una generación parcial a medias: temporal + rename (`mhlwriter._commit`). Ver issue #1 para huérfanos.
- El contenedor corre con `TZ=UTC` (ascmhl escribe fechas con el offset actual); la zona del horario laboral es `settings.timezone` y es la que muestra la GUI.
- Todo manifiesto que se escriba en tests se valida con `ascmhl-debug verify` y `xsd-schema-check` (norma `conformidad-mhl.md`).
- Nada de rutas absolutas ni nombres del estudio en código, tests ni docs (normas `sin-rutas-absolutas.md`, `repo-publico.md`).

## Hito 2: `events.py`, `supervisor.py`, `serve`
- `clock.py`: `utcnow()`, `utcnow_iso()`, `to_iso()`, `from_iso()` (ISO-8601 UTC con microsegundos y sufijo `Z`).
- `events.py`: `EventBus` seguro entre hilos: `publish(kind: str, payload: dict, *, job_id: int | None = None)` (si hay `job_id`, también `db.log`), `subscribe() -> asyncio.Queue[Event]` / `unsubscribe(q)` para SSE. Kinds: `cycle.started|finished`, `project.state` (`{id, rel_path, state}`), `job.started|progress|finished|failed`, `archive.unreachable|ok`, `log`.
- `supervisor.py`: `Supervisor(db, settings, bus)`: `start()`/`stop()` (coroutines; `stop` cierra `gate`, pone `stop_event`, espera al hilo hasher ≤ 30 s). Tarea asyncio `_loop`: cada `tick_seconds` (60) → `gate` abierta ⇔ no `is_working(now)`; si `gate` abierta o `scan_requested`: `run_scan_cycle` (en `asyncio.to_thread`), y `scan_requested=False`; cada `tick` publica `cycle.*`. Hilo hasher: `while not stop: job = db.next_job(); if job and gate.is_set(): sealer.run_job(...)` (sleep 5 s si no hay trabajo). `request_scan_now()` (D26: permitido en horario laboral; es solo scan). `status() -> SupervisorStatus(working_now, next_change, gate_open, current_job: JobRow | None, last_cycle_at, archive_reachable, queued_jobs: int)`. `archive_reachable`: `os.listdir(root)` con timeout 10 s en un hilo (montaje colgado). Al arrancar: `db.requeue_running_jobs()`. SIGTERM/SIGINT → `stop()`.
- `cli.py serve`: carga settings, abre DB, construye `EventBus`, `Supervisor`, `web.create_app(...)`; `uvicorn` programático en `0.0.0.0:settings.port`, con `lifespan` que arranca y para el supervisor. Un solo proceso.

## Hito 3: `web/` (FastAPI + Jinja2 + HTMX + SSE; inglés, D12)
`web/app.py`: `create_app(db, settings_ref, supervisor, bus) -> FastAPI` (`settings_ref` es un contenedor mutable para que *Save* en Ajustes recargue sin reiniciar; los cambios en `working_hours`/`timezone` los lee el supervisor en el siguiente tick). Plantillas en `web/templates/`, estáticos en `web/static/` (ya vendorizados: `pico.min.css`, `htmx.min.js`, `htmx-ext-sse.js`). Sin login (D35).
| Ruta | Qué |
|---|---|
| `GET /` | Pantalla única (D11): cabecera (Archive OK/KO · working hours now? · next change · current job), contadores (projects, needs review, unsealed, queued), lista de proyectos con semáforo y fecha; botones `Run scan now`, `Settings`. Entradas fuera de sitio (D49) en un aviso plegable. Lista = fragmento HTMX refrescado por SSE (`hx-ext="sse"`, evento `project.state`/`cycle.finished` → `hx-get /fragments/projects`). |
| `GET /fragments/projects`, `GET /fragments/header` | Fragmentos HTMX. |
| `GET /projects/{id}` | Detalle: generaciones (desde `MHLHistory`), ficheros, estado; si `needs_review`, el boceto D46 con `Accept as new version` / `Postpone`; si `unsealed`, `Seal`; siempre `Ignore`/`Unignore`. Trabajo en curso con barra de progreso (SSE `job.progress`). |
| `POST /projects/{id}/seal|ignore|unignore|accept|postpone` | Llaman a `sealer.request_*`; devuelven el fragmento del proyecto. |
| `POST /scan-now` | `supervisor.request_scan_now()`. |
| `GET /settings`, `POST /settings` | Boceto D45. Valida con `Settings`; `save_yaml`; recarga `settings_ref`. Campos solo-env deshabilitados. |
| `GET /events` | SSE (`sse-starlette`): un evento por `Event` del bus, `event: <kind>`, `data: json`. |
| `GET /healthz` | 200 si DB escribible y `archive_reachable`; 503 si no. JSON `{status, archive, db, version}`. |
| `GET /api/status`, `GET /api/projects` | JSON para scripts. |
Semáforo: verde `sealed`; ámbar `unsealed`/`changed`/`queued`/`hashing`; rojo `needs_review`/`error`; gris `ignored`. Fechas en `settings.timezone`. Tamaños legibles (GB). Textos para producción (D10): nada de hashes ni rutas absolutas en la pantalla principal.
