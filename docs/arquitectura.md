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
| `sealer.py` | Trabajos: `seal` (todos los ficheros, `original`, hereda hashes legacy como `verified`; cualquier discrepancia con un legacy → `needs_review`, nada se escribe), `append` (solo añadidos, parcial, D48), `accept_new_version` (mueve `ascmhl/` a `ascmhl_superseded/<AAAA-MM-DDTHHMMSSZ>/` (D50, H13: la referencia carga `ascmhl/` en recursivo) y hace `seal`; se hashea y se comprueba lo legacy **antes** de apartar el historial, así una revisión deja el historial intacto), `verify` (hito 4: relee todo con una caché que no acierta al leer pero sí guarda; compara con `sealed_files`; todo igual (añadidos permitidos) → generación completa con `verified`/`original` y `last_verified_at`; `corrupt` (mtime y tamaño iguales, hash distinto), `modified`, `missing` → `needs_review` con motivo `verification: …` y `verify_results`; se cancela si el proyecto ya no está `sealed`; si se para a medias vuelve a `sealed`). `cancellable_job` da el `seal`/`accept` manual en cola (D53) o en marcha (`hashing`, D57); `request_cancel` solo retira los que están en cola y rechaza uno en marcha (eso es del supervisor). `run_job(..., cancel=)`: si salta durante la lectura de un `seal`/`accept` manual, el trabajo acaba `cancelled` y el proyecto vuelve a su estado anterior con los hashes ya hechos en caché; para cualquier otro trabajo se ignora. `schedule_maintenance(db, settings, now)` tras cada ciclo: encola `verify` para los `sealed` con última verificación (o sellado) más antigua que `verify_interval_days`, los más antiguos primero, tope `ceil(sellados / intervalo)` por día local (`verify_day`, `verify_day_count` en `settings_kv`), nunca en horario laboral; y `root_manifest` si hace falta. Tras cada generación actualiza `sealed_files` desde el manifiesto escrito. | 1, 4 |
| `supervisor.py` | Bucle: cada `tick` (60 s) si no es horario laboral (o hay petición manual de scan): discovery si toca → scan de proyectos que tocan → transiciones → encolar. Hilo hasher consume `jobs` por prioridad (manual > seal > append > verify) solo con `gate` abierto. SIGTERM → `stop`, termina el bloque, guarda checkpoint, no escribe generación. `request_cancel(project_id)` (D57) aborta igual el `seal`/`accept` manual en curso de ese proyecto, pero el trabajo acaba `cancelled`. Recupera trabajos `running` al arrancar (→ `queued`). | 2 |
| `finder_tags.py` | D70: `TagSync.sync(db, archive_root, enabled)` lleva la etiqueta de Finder de cada carpeta de proyecto a la de su estado (verde `sealed`, amarillo pendiente, rojo revisión; `ignored` sin etiqueta, `missing` no se toca), escribiendo solo cuando cambia. Xattr `user.DosStream.com.apple.metadata:_kMDItemUserTags:$DATA` (Samba `streams_xattr`, NUL final); conserva las etiquetas ajenas; tras un cambio, `touch` de la carpeta madre para que el Finder refresque. Lo llama el tick del supervisor fuera del horario laboral. | 6 |
| `rootmanifest.py` | Raíz de solo referencias (D29, D43, D51): `refresh_root_manifest(db, settings, *, now, publish)`; se regenera si `root_manifest_stale` o cambia el conjunto referenciado (`root_manifest_projects` en `settings_kv`); si alguna generación referencia un manifiesto inexistente, aparta el historial (`mhlwriter.retire_history`) y empieza en 0001. `discovery` salta `ascmhl` y `ascmhl_superseded` en todos los niveles. Trabajo `root_manifest` (prioridad 5). | 4 |
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
missing    la carpeta no está en el disco o no tiene ningún fichero (D58, D67): desde el primer scan en que falta, con `missing_since` y `state_before_missing`. Botones `Forget permanently` (`retire`, D68; borra fila, caché y espejo; una línea en el log, D60) y `Retry` (stat al momento; si vuelve, estado anterior y verify automático, D61). Si vuelve en un scan, igual. Una carpeta nueva con la misma `ascmhl_chain.xml` que el espejo de un `missing` es el mismo proyecto movido (D62).
error      excepción registrada en jobs.error; se reintenta al siguiente tick.
```
Transiciones las decide `sealer.classify(project, scan_diff, settings, now)`; el scan no cambia estados por sí solo. Detalles fijados en el hito 1: un scan con errores de lectura deja el proyecto en `error` sin clasificar (un fichero ilegible parecería borrado); una carpeta que desaparece del disco → `missing` (D58; antes `error "folder missing"`), la fila no se borra; como discovery calla los errores de listado, la ausencia se confirma con un `lstat` de la propia carpeta (solo «no existe» cuenta; si sigue ahí o da otro error, nada cambia y queda un aviso en el log); si la raíz lista cero proyectos teniendo la DB alguno, la ronda es «archivo inaccesible» y nada cambia; en la comprobación legacy un fichero que falta es `deleted` y uno con hash distinto `modified`; `append` también comprueba legacy sobre los añadidos; `Accept` no se salta la comprobación legacy; un historial ASC MHL creado por otra herramienta aparece como `unsealed` y `Seal` le añade una generación completa (si no cuadra, revisión). Los `exclude_globs` (D14) se escriben como patrones de ignore del manifiesto. Prioridades de cola: manual +100; `seal`/`accept` 30, `append` 20, `verify` 10. `Verify now` (D63) encola un `verify` manual con `bypass_hours`: el supervisor lo ejecuta aunque la puerta del horario esté cerrada (si hay un trabajo pausado por el horario, se le pide que ceda: vuelve a la cola con sus checkpoints); cancelable como un Seal (vuelve a `sealed`). `Retire` se rechaza mientras un trabajo del proyecto siga `running`; el supervisor pide parar al trabajo en curso cuyo proyecto el scan marca `missing` o movido. El historial `ascmhl/` de cada proyecto se espeja en `<config>/history/<proyecto>/ascmhl/` tras cada generación y en el scan si difiere (D59, módulo `history_mirror.py`; un cerrojo serializa el hilo de scan y el hasher; un espejo con ficheros que el historial del disco ya no tiene es de otro historial y se aparta a `ascmhl_superseded/<stamp>/` antes de copiar, nunca se mezclan). Manifiestos huérfanos (`ascmhl/*.mhl` fuera de la cadena) se renombran a `*.mhl.orphan` en cada scan (issue #1).

## Tablas (`db.py`, `user_version` 2)
```sql
projects(id INTEGER PK, rel_path TEXT UNIQUE, name TEXT, state TEXT, preexisting INTEGER, first_seen TEXT, last_scan_at TEXT,
         last_change_at TEXT, stable_since TEXT, last_generation_no INTEGER, last_sealed_at TEXT, last_verified_at TEXT,
         file_count INTEGER, total_bytes INTEGER, error TEXT, review_reason TEXT,
         missing_since TEXT, state_before_missing TEXT)            -- v2 (D58)
files(project_id, rel_path, size, mtime_ns, PRIMARY KEY(project_id, rel_path))            -- último scan
sealed_files(project_id, rel_path, size, mtime_ns, xxh128, PRIMARY KEY(project_id, rel_path)) -- lo que dice el último manifiesto
file_hashes(project_id, rel_path, size, mtime_ns, fmt, digest, hashed_at, PRIMARY KEY(project_id, rel_path, fmt)) -- checkpoint (D28)
scans(id PK, project_id NULL, kind, started_at, finished_at, files, bytes, added, modified, deleted, status)
jobs(id PK, kind, project_id, trigger, state, priority, created_at, started_at, finished_at, files_done, files_total, bytes_done, bytes_total, error,
     bypass_hours INTEGER)                                    -- v2 (D63); kind `retire` = rastro de una baja, sin proyecto (D60)
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
theme: auto                       # D56: auto (sigue al sistema) | light | dark; Ajustes → Appearance
finder_tags: false                # D70: etiqueta de Finder por estado en cada carpeta de proyecto (opt-in)
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
- `supervisor.py`: `Supervisor(db, settings, bus)`: `start()`/`stop()` (coroutines; `stop` cierra `gate`, pone `stop_event`, espera al hilo hasher ≤ 8 s: cabe en los 10 s de SIGTERM de Docker/Watchtower, D52); `request_stop()` seguro desde el manejador de señal. Tarea asyncio `_loop`: cada `tick_seconds` (60) → `gate` abierta ⇔ no `is_working(now)`; si `gate` abierta o `scan_requested`: `run_scan_cycle` (en `asyncio.to_thread`), y `scan_requested=False`; cada `tick` publica `cycle.*`. Hilo hasher: `while not stop: job = db.next_job(); if job and gate.is_set(): sealer.run_job(...)` (sleep 5 s si no hay trabajo). `request_scan_now()` (D26: permitido en horario laboral; es solo scan). `status() -> SupervisorStatus(working_now, next_change, gate_open, current_job: JobRow | None, last_cycle_at, archive_reachable, queued_jobs: int)`. `archive_reachable`: `os.listdir(root)` con timeout 10 s en un hilo (montaje colgado). Al arrancar: `db.requeue_running_jobs()` y, en el primer tick que alcanza el archivo, `sealer.recover_history_dirs()` (borra `.*.tmp` en `ascmhl/` y aparta huérfanos a `*.mhl.orphan`) antes de que el hasher coja ningún trabajo (D52). `run_scan_cycle(stop=)` se corta entre proyectos al parar. SIGTERM/SIGINT → `stop()`. `Database.close()` hace `wal_checkpoint(TRUNCATE)`.
- `cli.py serve [--host]`: carga settings, abre DB, construye `SettingsRef`, `EventBus`, `Supervisor` y la app (`server.build_app`: importa `web.app.create_app` de forma perezosa; si falla, `web_fallback.create_fallback_app` solo con `/healthz`); `server.GracefulServer` (uvicorn) maneja SIGTERM/SIGINT y sale con 0; cierre HTTP con tope de 5 s para no quedar retenido por SSE. Un solo proceso. Opción oculta `--once-tick` para tests.
- Detalles fijados en el hito 2: el scan automático respeta `scan_interval_minutes` (no corre en cada tick; `cycle.*` sí se publica en cada tick con `scan: bool`); el hilo hasher no coge trabajos mientras el archivo no responde; `job.progress` se limita a uno cada 0,5 s; los eventos `job.*` no llevan `job_id` porque `sealer` ya escribe en `job_log`; `Supervisor.notify_job_queued()` despierta al hilo hasher (la GUI lo llama tras `Seal`/`Accept`); `settings_ref.py` (`SettingsRef.get()/replace()`) comparte los ajustes entre GUI y supervisor; `sealer.run_job(..., on_progress=)`.

## Hito 3: `web/` (FastAPI + Jinja2 + HTMX + SSE; inglés, D12)
`web/app.py`: `create_app(db, settings_ref, supervisor, bus) -> FastAPI` (`settings_ref` es un contenedor mutable para que *Save* en Ajustes recargue sin reiniciar; los cambios en `working_hours`/`timezone` los lee el supervisor en el siguiente tick). Plantillas en `web/templates/`, estáticos en `web/static/` (vendorizados: `htmx.min.js`, `htmx-ext-sse.js`; hoja y JS propios `sentinel.css`/`sentinel.js`, D54). Sin login (D35).
| Ruta | Qué |
|---|---|
| `GET /` | Pantalla única como bandeja (D11, D54; `docs/propuestas/frontend-D.md`): frase de estado (`#status-header`: titular según el estado de los proyectos, Archive OK/KO, última ronda, raíz, horario laboral, trabajo en curso); bandeja (`#projects`: *Needs your decision*, *Not sealed yet* con `Seal`, *In progress* con `Cancel`, *Everything else* plegado con la lista completa y la próxima verificación); log de actividad (`#activity`). Botones `Scan now` y `Settings`. Entradas fuera de sitio (D49) en un plegable. Todo refrescado por SSE (`hx-ext="sse"`). |
| `GET /fragments/projects`, `GET /fragments/header`, `GET /fragments/activity` | Fragmentos HTMX. El log de actividad lee solo la tabla `jobs` (más `job_log` y `verify_results` para marcar en rojo lo que acabó en revisión); no toca el NAS. |
| `GET /projects/{id}` | Detalle: generaciones (desde `MHLHistory`), ficheros, estado; si `needs_review`, el boceto D46 con `Accept as new version` / `Postpone`; si `unsealed`, `Seal`; siempre `Ignore`/`Unignore`. Trabajo en curso con barra de progreso (SSE `job.progress`). |
| `POST /projects/{id}/seal|ignore|unignore|accept|postpone|cancel` | Llaman a `sealer.request_*`. Desde la ficha devuelven el fragmento del proyecto (sin JS, redirigen a `/projects/{id}`); con `?from=inbox` (botones de la bandeja) devuelven el fragmento `#projects` (sin JS, redirigen a `/`). Siempre con la cabecera `HX-Trigger: inbox-changed`, que refresca titular y log porque los botones no publican eventos en el bus. `cancel` solo para un `seal`/`accept` manual: en cola lo retira `sealer.request_cancel` (D53); en marcha (`hashing`, también pausado por el horario laboral) lo pide `supervisor.request_cancel` y el hasher lo para en el siguiente fichero (D57; 409 si el trabajo en curso no es cancelable). |
| `POST /projects/{id}/retire|retry|verify`, `GET /projects/{id}/history.zip` | D60–D63: `retire` borra un `missing` (sin JS redirige a `/?retired=<nombre>`; con HTMX desde la ficha, `HX-Redirect`); `retry` un `stat` al momento; `verify` = `Verify now` (en horario laboral, diálogo de aviso antes); el zip sale del espejo (o del `ascmhl/` del disco si no hay espejo), 404 si no hay historial. |
| `POST /scan-now` | `supervisor.request_scan_now()`. |
| `GET /settings`, `POST /settings` | Boceto D45. Valida con `Settings`; `save_yaml`; recarga `settings_ref`. Campos solo-env deshabilitados. |
| `GET /events` | SSE (`sse-starlette`): un evento por `Event` del bus, `event: <kind>`, `data: json`. |
| `GET /healthz` | 200 si DB escribible y `archive_reachable`; 503 si no. JSON `{status, archive, db, version}`. |
| `GET /api/status`, `GET /api/projects` | JSON para scripts. |
Detalles fijados en el hito 3: fragmentos extra `GET /fragments/projects/{id}` y `.../progress` (la barra lee el progreso de `jobs` en DB, no del payload); el detalle de revisión muestra también los añadidos (`files` − `sealed_files`); las entradas fuera de sitio se calculan con `find_stray_entries` y se cachean 5 min (solo si el archivo responde); tras *Save* se recarga con `load_settings` conservando los campos solo-env; la lista se ordena por color y los ignorados no cuentan como proyectos; `HX-Request` devuelve fragmento, sin él redirige 303; `SealerError` → 409 con el mensaje en el fragmento.
Semáforo: verde `sealed`; ámbar `unsealed`/`changed`/`queued`/`hashing`; rojo `needs_review`/`error`/`missing`; gris `ignored`. Fechas en `settings.timezone`. Tamaños legibles (GB). Textos para producción (D10): nada de hashes ni rutas absolutas en la pantalla principal.
