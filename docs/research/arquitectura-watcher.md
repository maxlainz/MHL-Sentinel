# Research: arquitectura del watcher (detección de cambios, scheduling, GUI, Docker, DB)

*Informe de subagente Opus, 2026-10-01. En inglés. Las cifras de coste son estimaciones de escritorio: se miden sobre el NAS real antes de usarse (norma `prediccion-antes-de-medir.md`).*

*Decisiones del owner posteriores al informe (mandan sobre él): §6 sustituido por D18/D19 (niveles, no patrones); «quiet windows» → horario laboral durante el que la app se detiene (D33); sin throttle (D34); contenedor en el NAS, no sobre SMB (D32); settle 168 h (D31); MHL 1.x sí se tratan (D39); resto de la tabla final aceptado en D28–D36.*

## TL;DR en español
- **Detección de cambios**: ni inotify ni watchdog (no ven cambios hechos desde otros clientes SMB/NFS). Scan programado propio con `os.scandir` + snapshot `(size, mtime_ns)` en SQLite; dos niveles (discovery barato y frecuente; scan por proyecto en ventana de inactividad); settle time antes de hashear.
- **Hashing**: no apoyarse en `ascmhl create` para lo incremental (rehashea todo). Hasher propio con throttle y checkpoint por fichero; escribir la generación con `mhllib` al final, nunca parcial.
- **Scheduling**: bucle asyncio propio con predicado `is_active(now, tz)` (zoneinfo); pausa cooperativa entre chunks en el límite de ventana.
- **GUI**: FastAPI + Jinja2 + HTMX + SSE + Pico.css, sin build de Node.
- **DB**: `sqlite3` stdlib, WAL, en `/config/state.db` en disco local; rechazar arrancar si `/config` está en cifs/nfs.
- **Docker**: `python:3.12-slim`, multi-arch amd64+arm64 en GHCR, PUID/PGID y `user:`, tini, `/archive` rw (ascmhl escribe `ascmhl/` dentro del árbol).
- **Nicho libre**: no existe ningún daemon/imagen Docker de vigilancia ASC MHL.

---

## 1. Change detection on network mounts
- inotify/fanotify live in the client kernel; changes made on the NAS or by other SMB/NFS clients never fire inside the container (Docker forums, moby #18246). Only works for local ext4/ZFS/btrfs on the Docker host for writes made on that host (e.g. container running on Unraid/TrueNAS and Samba clients writing through that kernel). Not reliable in general.
- Options: watchdog InotifyObserver (misses remote changes); watchdog PollingObserver/PollingObserverVFS (full in-memory DirectorySnapshot every poll, no persistence/throttle/schedule: wrong tool); **own `os.scandir` + (size, mtime_ns) snapshot in SQLite (recommended)**; hybrid inotify-hint later, never as source of truth.
- Cost estimate, 500k files over SMB (unmeasured): directory listing carries size+timestamps (SMB2 QueryDirectory, NFS READDIRPLUS); ~10k dirs ≈ 20–40k round trips ≈ 10–40 s warm. Worst case attribute cache expired (`actimeo` 1 s on CIFS) → one stat per file ≈ 4–8 min single-threaded ("GETATTR storm"). Cold HDD metadata adds seeks and a working-hours I/O spike → quiet hours must govern scans too. Hashing dominates: 1 GbE ≈ 110 MB/s ≈ 2.5 h/TB; 50 MB/s throttle ≈ 5.5 h/TB.
- Reducing load: (1) per-project snapshots, streaming diff; (2) two tiers: discovery scan (hourly, only down to project depth, compares project set + root mtimes) and project scan (full stat walk, nightly, staggered 1/7 per night, weekly full pass); (3) directory mtime pruning only as optimization: dir mtime changes only on direct entry create/delete/rename, not on in-place content overwrite, does not propagate up, and NFS/CIFS caches can serve stale values → still stat files; (4) token-bucket rate limit on dir ops; (5) `os.nice` fine, `ionice` useless for SMB/NFS; (6) read throttle in bytes/s (8–16 MiB chunks), 1 worker by default; (7) settle time: project dirty → eligible after N min (default 60) with two identical scans; skip `*.part`, `.~lock*`, `._*`, `.DS_Store`, `Thumbs.db`, `@eaDir`, `#recycle`, `.snapshot`, `.zfs`, `.Trash-*`; re-stat before and after hashing each file.

## 2. Scheduling and pause/resume
- APScheduler 3.x: triggers, no window/pause concept; 4.0 still alpha. `schedule`: not TZ/DST robust. `croniter`: building block. **Custom asyncio loop recommended** (~50 lines, unit-testable with zoneinfo).
- Config sketch:
```yaml
schedule:
  timezone: Europe/Madrid
  quiet_windows:
    - { days: [mon,tue,wed,thu,fri], start: "22:00", end: "07:00" }   # crosses midnight
    - { days: [sat,sun], start: "00:00", end: "24:00" }
  discovery_scan_interval: 1h      # cheap, allowed anytime
  allow_manual_override: true
```
- Hasher in a worker thread checks `allowed`/`stop` events between chunks; on window close it **pauses** (closes handle, keeps offset + in-memory xxh state; on restart re-hashes that file). Per-file checkpoint in SQLite `file_hashes (project, path, size, mtime_ns, algo, digest)`; resume skips cached matches. Generation written per project only when every file has a fresh hash.
- ascmhl atomicity problem: `create` re-hashes everything, a run interrupted by a window produces nothing, root `create` touches every nested history. Options: (v0) subprocess + SIGSTOP/SIGCONT; (-sf deltas after diff: loses completeness); **(target) own checkpointed hasher + mhllib writer, spike in week 1**. Re-verify policy: periodic verify job for bitrot (results in DB, or as generation if proof-of-check wanted). Root manifest: references-only generation (hash only child manifests) or `flatten` per project into external aggregated manifest.

## 3. Web GUI stack
| Stack | Verdict |
|---|---|
| **FastAPI + Jinja2 + HTMX (+ htmx-ext-sse, Pico.css)** | **Recommended**: ~14 KB vendored, no Node, SSE via `sse-starlette`, JSON API + HTML fragments from one app, classless CSS mobile-friendly, optional `HTTPBasic` |
| Flask + HTMX | OK; SSE awkward under WSGI |
| NiceGUI | fastest to write; 180–260 MB image, opinionated state |
| Streamlit/Gradio | rerun model, poor fit for daemon |
| React/Vite SPA | overkill |
Pages: dashboard (window state, current job, last results), projects table (state, last generation, last verify, size, needs-review), project detail (generations; Scan / Hash now / Verify), jobs+log (SSE tail + DB history), settings (YAML form; env-set keys locked). References: **checkrr** (closest analogue: scheduled media integrity, hash DB, web UI, Unraid template), integrityCheckarr, Scrutiny, Syncthing.

## 4. Docker packaging for NAS
- Base `python:3.12-slim` (alpine possible: xxhash has musllinux wheels, but lxml risk; size gain small; ~60–80 MB compressed).
- Multi-arch amd64+arm64 via setup-qemu/buildx/metadata-action (semver tags, `edge` on main) → GHCR with `GITHUB_TOKEN`. armv7 optional.
- Users: support **both** PUID/PGID (entrypoint as root → create user → chown `/config` only → `UMASK` → `gosu`/`setpriv`) and `user:` (skip all). On SMB mounts ownership comes from mount options (`uid=`, `gid=`, `file_mode=`).
- Volumes: `/archive` **rw** (in-tree `ascmhl/`); optional `READ_ONLY=true` verify-only mode with sidecars in `/data/manifests/` (non-standard placement, label it). `/config`: `config.yaml` + `state.db`, local disk. `/data` optional (exports).
- Healthcheck via `urllib` to `/healthz` (slim has no curl); `/healthz` checks `/archive` listable (stale SMB) and DB writable.
- Graceful shutdown: exec-form ENTRYPOINT + tini/`init: true`; explicit SIGTERM handler → stop event → finish chunk → commit checkpoint → never write partial generation → exit 0; `stop_grace_period: 30s`; generations written tmp+rename, orphan temps cleaned at startup.
- Config precedence: defaults < `/config/config.yaml` < env (`MHLW_` prefix, nested `__`) via pydantic-settings; GUI writes YAML, env keys locked.
- Logging: stdout text, `LOG_FORMAT=json`; job logs in SQLite with capped retention.
- Distribution: Unraid CA template XML; compose examples for Synology Container Manager, TrueNAS SCALE, Portainer; compose with native CIFS volume driver for hosts without pre-mount.

## 5. State DB
stdlib `sqlite3`, `PRAGMA user_version` migrations, WAL, `synchronous=NORMAL`, `busy_timeout=5000`, single writer. **Never on SMB/NFS** (sqlite.org/wal.html); refuse to start if `/config` is cifs/nfs/smb3/fuse.sshfs.
```sql
projects(id, rel_path UNIQUE, detected_by, state /*new|dirty|settling|queued|hashing|paused|ok|error|needs_review|ignored*/,
         first_seen, last_change_seen, last_scan, last_generation_at, last_generation_no,
         last_verify_at, last_verify_status, file_count, total_bytes, error)
files(project_id, rel_path, size, mtime_ns, inode NULL, seen_scan_id, PK(project_id, rel_path))
file_hashes(project_id, rel_path, size, mtime_ns, algo, digest, hashed_at, PK(project_id, rel_path, algo))
scans(id, kind /*discovery|project|full*/, project_id NULL, started, finished, dirs, files, added, removed, modified, status)
jobs(id, kind /*hash|verify|root_manifest|scan*/, project_id NULL, trigger /*auto|manual*/, state, started, finished,
     bytes_done, bytes_total, files_done, files_total, error)
job_log(job_id, ts, level, msg)
verify_results(job_id, project_id, rel_path, expected, actual, status /*ok|mismatch|missing|new*/)
settings_kv(key, value)
```

## 6. Project-root detection
Order: (1) explicit markers (`.mhlproject`, existing `ascmhl/`, configured marker files at candidate top level only); (2) layout mode: `depth: N` | `containers` (folders matching `^\d{4}$`, `^\d{2}$`, `^\d{4}-\d{2}$`… are transparent; first non-container folder is the project; `max_container_depth`) | `glob`; (3) exclusions (NAS junk, recycle, snapshots, `_`/`@`/`#`/`.` prefixes); (4) ambiguity (project named `2024` under `2024/`; container-looking folder that directly contains media or has no subfolders → project) → **`needs_review`, never auto-hashed**; GUI "confirm as project / treat as container" stored as override (highest priority). Projects inside projects: outermost wins.
```yaml
archive:
  root: /archive
  layout: { mode: containers, depth: 2, container_patterns: ['^\d{4}$', '^\d{2}$', '^\d{4}-\d{2}$'], max_container_depth: 2, globs: [] }
  markers: { project: [".mhlproject", "ascmhl/"], container: [".mhlcontainer"], ignore: [".mhlignore"] }
  overrides: { "2024/2024": project }
  exclude: ["@eaDir", "#recycle", "#snapshot", ".snapshot", ".zfs", ".Trash-*", "$RECYCLE.BIN", "System Volume Information",
            ".DS_Store", "._*", "Thumbs.db", "*.part", "*.tmp", ".~lock*"]
  min_project_age: 24h
hashing:
  algorithm: xxh128
  settle_time: 60m
  throttle: { quiet_bps: 0, active_bps: 0 }
  workers: 1
  verify_interval: 90d
  on_missing_files: flag        # flag | new_generation | fail
root_manifest: { enabled: true, mode: references }   # references | flatten
```

## 7. Comparable projects
ascmitc/mhl (library + oracle); chkbit (split vs atom index; mtime rule); scorch (status taxonomy); cshatag (xattrs: unreliable over SMB → keep state in SQLite + MHL); yabitrot (inode keys: not stable over SMB → key by path + size/mtime); checkrr (closest UX/ops model). **No MHL daemon/watcher/Docker image exists.**

## 8. Notifications (later)
One `notify(event, payload)` interface backed by Apprise (ntfy, Gotify, SMTP, Discord, Slack, Telegram, Pushover, webhooks) + raw `webhook_url`. Events: hash mismatch/bitrot, missing files, generation written, verify finished, archive unreachable/stale mount, needs-review.

## Recommended architecture
```
container (python:3.12-slim, tini, uid PUID)
  FastAPI (uvicorn, 1 worker): HTML (Jinja2+HTMX) / JSON API / SSE /events / /healthz
  Supervisor (asyncio main loop)
    WindowGate: is_active(now) → asyncio.Event + threading.Event
    Discoverer: lists root→project depth, applies rules → projects table
    Scanner: per-project scandir walk, streaming diff → files/scans
    Queue: dirty→settling→queued (priority: manual > new > changed > verify)
    Hasher (1 thread): throttled reads, xxh128, per-file checkpoint
    MHLWriter: mhllib generation per project (tmp+rename); root manifest
    EventBus → SSE + job_log (+ Notifier later)
  SQLite /config/state.db (WAL, local disk only)
  /archive (SMB/NFS/bind, rw)      /config (local)
```
Main loop (pseudocode): load config, open DB (refuse if network FS), recover interrupted jobs; install SIGTERM; start web + hasher + gate; every 60 s: discovery if due (anytime, rate-limited); if gate active or manual: scan projects due (staggered), mark dirty/clean, enqueue settled projects for hash, due projects for verify, root manifest if stale. Hasher thread: next job → for each file needing hash (skip cached size/mtime matches): wait gate, stat, read chunks with throttle, check stop/gate between chunks, re-stat, commit hash; when all done → write generation (tmp+rename) or record verify results.

## Decisions for the owner (defaults recommended)
| # | Decision | Recommended default |
|---|---|---|
| 1 | How generations are produced | own hasher + mhllib writer (spike first); `ascmhl create` subprocess as week-1 prototype |
| 2 | Top-level archive MHL | references-only root generation, rebuilt after any project generation |
| 3 | `/archive` mount mode | rw; also ship ro verify-only mode |
| 4 | Missing/deleted files | flag + notify, no auto-generation; user confirms in GUI |
| 5 | Re-hash unchanged files (bitrot) | periodic verify every 90 days, quiet windows only |
| 6 | Hash | xxh128 |
| 7 | Change detection | own scandir snapshot in SQLite, two tiers, weekly full-stat backstop |
| 8 | Settle / min age | 60 min settle, 24 h min age for new projects |
| 9 | Project detection | containers mode with year/month patterns, `max_container_depth: 2`, markers first, ambiguous → needs_review |
| 10 | Quiet windows | weekdays 22:00–07:00 + weekends, TZ-aware, manual override |
| 11 | Throttle | unlimited inside windows, 1 worker, configurable cap |
| 12 | GUI | FastAPI + Jinja2 + HTMX + SSE + Pico.css |
| 13 | Auth | optional basic auth via env; document reverse proxy |
| 14 | DB | stdlib sqlite3 + user_version migrations |
| 15 | Image/arch | python:3.12-slim, amd64 + arm64 |
| 16 | Privileges | PUID/PGID default, `user:` supported |
| 17 | Config | defaults < YAML < env; GUI writes YAML |
| 18 | Logs | stdout text, JSON option; job logs in SQLite |
| 19 | Notifications | event hooks now, Apprise in v1.1 |
Risks to prototype first: mhllib with precomputed hashes; nested/root reference semantics; real scan timings on the NAS (`actimeo` default vs raised, cold/warm); stale-mount detection.
