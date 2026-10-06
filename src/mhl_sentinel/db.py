"""SQLite state store (D36, docs/arquitectura.md "Tablas").

WAL, ``synchronous=NORMAL``, ``busy_timeout=5000``, ``foreign_keys=ON``; migrations by
``PRAGMA user_version``. The database must live on a local disk: :meth:`Database.open` refuses a
network filesystem (vault note `SQLite sobre un volumen de red`). One connection shared by all
threads, serialised by a single ``RLock``. Dates are ISO-8601 UTC strings (:mod:`clock`).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from mhl_sentinel.clock import to_iso
from mhl_sentinel.models import ChangeKind, FileStat, JobKind, JobState, ProjectState, Trigger

NETWORK_FS_TYPES: frozenset[str] = frozenset({"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs"})
PROC_MOUNTS = Path("/proc/mounts")
SCHEMA_VERSION = 2


class NetworkFilesystemError(RuntimeError):
    """The database directory is on a network filesystem (SQLite + WAL is unsafe there)."""


def _unescape_mount(field: str) -> str:
    # /proc/mounts escapes space, tab, newline and backslash as octal (\040 ...).
    out: list[str] = []
    i = 0
    while i < len(field):
        if field[i] == "\\" and field[i + 1 : i + 4].isdigit():
            out.append(chr(int(field[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def is_network_fs(path: Path, mounts_file: Path = PROC_MOUNTS) -> bool:
    """True if ``path`` lives on a network filesystem according to ``mounts_file``.

    Uses the longest mount point that contains ``path``. Without a mounts file (macOS, tests)
    it returns ``False``: the check is a Linux/container safeguard.
    """
    if not mounts_file.is_file():
        return False
    target = Path(path).resolve(strict=False)
    best_len = -1
    best_type = ""
    for line in mounts_file.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point = Path(_unescape_mount(parts[1]))
        if target == mount_point or mount_point in target.parents:
            depth = len(mount_point.parts)
            if depth >= best_len:  # later lines shadow earlier mounts on the same point
                best_len = depth
                best_type = parts[2]
    return best_type in NETWORK_FS_TYPES


_SCHEMA_V1 = """
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    rel_path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    preexisting INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_scan_at TEXT,
    last_change_at TEXT,
    stable_since TEXT,
    last_generation_no INTEGER,
    last_sealed_at TEXT,
    last_verified_at TEXT,
    file_count INTEGER,
    total_bytes INTEGER,
    error TEXT,
    review_reason TEXT
);
CREATE INDEX projects_state ON projects(state);

CREATE TABLE files (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    PRIMARY KEY (project_id, rel_path)
) WITHOUT ROWID;

CREATE TABLE sealed_files (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    xxh128 TEXT,
    PRIMARY KEY (project_id, rel_path)
) WITHOUT ROWID;

CREATE TABLE file_hashes (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    fmt TEXT NOT NULL,
    digest TEXT NOT NULL,
    hashed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, rel_path, fmt)
) WITHOUT ROWID;

CREATE TABLE scans (
    id INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    files INTEGER,
    bytes INTEGER,
    added INTEGER,
    modified INTEGER,
    deleted INTEGER,
    status TEXT NOT NULL
);
CREATE INDEX scans_project ON scans(project_id, started_at);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    trigger TEXT NOT NULL,
    state TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    files_done INTEGER NOT NULL DEFAULT 0,
    files_total INTEGER NOT NULL DEFAULT 0,
    bytes_done INTEGER NOT NULL DEFAULT 0,
    bytes_total INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX jobs_queue ON jobs(state, priority, created_at);
CREATE INDEX jobs_project ON jobs(project_id, kind, state);

CREATE TABLE job_log (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    msg TEXT NOT NULL
);
CREATE INDEX job_log_job ON job_log(job_id, id);

CREATE TABLE review_items (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    change TEXT NOT NULL,
    old_size INTEGER,
    new_size INTEGER,
    old_mtime_ns INTEGER,
    new_mtime_ns INTEGER,
    PRIMARY KEY (project_id, rel_path)
) WITHOUT ROWID;

CREATE TABLE verify_results (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    expected TEXT,
    actual TEXT,
    status TEXT NOT NULL
);
CREATE INDEX verify_results_job ON verify_results(job_id);

CREATE TABLE settings_kv (
    key TEXT PRIMARY KEY,
    value TEXT
) WITHOUT ROWID;
"""

# v2 (D58, D63): a project whose folder vanished keeps its row as ``missing`` with the date it was
# first missed and the state to go back to; a job may run inside working hours (Verify now).
_SCHEMA_V2 = """
ALTER TABLE projects ADD COLUMN missing_since TEXT;
ALTER TABLE projects ADD COLUMN state_before_missing TEXT;
ALTER TABLE jobs ADD COLUMN bypass_hours INTEGER NOT NULL DEFAULT 0;
"""

# Index i holds the script that takes user_version from i to i + 1. A future schema change
# appends a script here (never edits an old one): a database left by the previous image is
# migrated forward on the next start (auto-updated container, /config persists). Each script runs
# with the ``user_version`` bump in one transaction, so a kill mid-migration leaves the old version.
MIGRATIONS: tuple[str, ...] = (_SCHEMA_V1, _SCHEMA_V2)

_TERMINAL_JOB_STATES = (JobState.DONE, JobState.FAILED, JobState.CANCELLED)
_PROJECT_COLUMNS: frozenset[str] = frozenset(
    {
        "name",
        "state",
        "preexisting",
        "last_scan_at",
        "last_change_at",
        "stable_since",
        "last_generation_no",
        "last_sealed_at",
        "last_verified_at",
        "file_count",
        "total_bytes",
        "error",
        "review_reason",
        "missing_since",
        "state_before_missing",
    }
)


@dataclass(frozen=True, slots=True)
class ProjectRow:
    id: int
    rel_path: str
    name: str
    state: ProjectState
    preexisting: bool
    first_seen: str
    last_scan_at: str | None
    last_change_at: str | None
    stable_since: str | None
    last_generation_no: int | None
    last_sealed_at: str | None
    last_verified_at: str | None
    file_count: int | None
    total_bytes: int | None
    error: str | None
    review_reason: str | None
    missing_since: str | None = None  # D58: first scan that did not find the folder
    state_before_missing: ProjectState | None = None  # D58: where Retry/reappearance go back

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ProjectRow:
        data = dict(row)
        data["state"] = ProjectState(data["state"])
        data["preexisting"] = bool(data["preexisting"])
        before = data.get("state_before_missing")
        data["state_before_missing"] = ProjectState(before) if before is not None else None
        return cls(**data)


@dataclass(frozen=True, slots=True)
class JobRow:
    id: int
    kind: JobKind
    project_id: int | None
    trigger: Trigger
    state: JobState
    priority: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    files_done: int
    files_total: int
    bytes_done: int
    bytes_total: int
    error: str | None
    bypass_hours: bool = False  # D63: runs even while the working-hours gate is closed

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> JobRow:
        data = dict(row)
        data["kind"] = JobKind(data["kind"])
        data["trigger"] = Trigger(data["trigger"])
        data["state"] = JobState(data["state"])
        data["bypass_hours"] = bool(data.get("bypass_hours", 0))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class SealedFile:
    """A file as recorded by the last manifest generation."""

    rel_path: str
    size: int
    mtime_ns: int
    xxh128: str | None


@dataclass(frozen=True, slots=True)
class ReviewItem:
    rel_path: str
    change: ChangeKind
    old_size: int | None = None
    new_size: int | None = None
    old_mtime_ns: int | None = None
    new_mtime_ns: int | None = None


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """One file of a ``verify`` job (hito 4): ``ok``, ``added`` (fine), ``modified``,
    ``corrupt`` or ``missing`` (vault note `Distinguir corrupción de modificación por mtime`)."""

    rel_path: str
    expected: str | None
    actual: str | None
    status: str


@dataclass(frozen=True, slots=True)
class LogEntry:
    job_id: int
    ts: str
    level: str
    msg: str


def _sql_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return to_iso(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Path):
        return value.as_posix()
    return value


class Database:
    """Thin typed repository over ``sqlite3``; no ORM."""

    def __init__(self, path: Path, *, mounts_file: Path = PROC_MOUNTS) -> None:
        self.path = path
        self._mounts_file = mounts_file
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._tx_depth = 0

    # -- lifecycle ---------------------------------------------------------------------------

    def open(self) -> Self:
        if self._conn is not None:
            return self
        if is_network_fs(self.path.parent, self._mounts_file):
            raise NetworkFilesystemError(
                f"refusing to open the state database on a network filesystem ({self.path.name});"
                " mount the config directory from a local disk"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        self._conn = conn
        self.migrate()
        return self

    def close(self) -> None:
        """Checkpoint the WAL into the main file (best effort) and close.

        After an unclean stop (SIGKILL) the ``-wal`` file stays and SQLite replays it on the
        next open; a clean close leaves a self-contained ``state.db``."""
        with self._lock:
            if self._conn is not None:
                with suppress(sqlite3.Error):
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.close()
                self._conn = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("database is not open")
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction (``BEGIN IMMEDIATE``); nested calls join the outer one."""
        with self._lock:
            conn = self.conn
            if self._tx_depth:
                self._tx_depth += 1
                try:
                    yield conn
                finally:
                    self._tx_depth -= 1
                return
            conn.execute("BEGIN IMMEDIATE")
            self._tx_depth = 1
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
            finally:
                self._tx_depth = 0

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def _query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self.conn.execute(sql, tuple(params)).fetchone()
            return row

    # -- migrations --------------------------------------------------------------------------

    @property
    def user_version(self) -> int:
        row = self._query_one("PRAGMA user_version")
        assert row is not None
        return int(row[0])

    def migrate(self, migrations: Sequence[str] = MIGRATIONS) -> int:
        """Apply pending migrations; idempotent. Returns the resulting ``user_version``.

        ``migrations`` exists for tests (a future v2 applied to a v1 database)."""
        with self._lock:
            current = self.user_version
            if current > len(migrations):
                raise RuntimeError(
                    f"database schema v{current} is newer than this app (v{len(migrations)})"
                )
            for version in range(current, len(migrations)):
                self.conn.executescript(
                    f"BEGIN IMMEDIATE;\n{migrations[version]}\n"
                    f"PRAGMA user_version = {version + 1};\nCOMMIT;"
                )
            return self.user_version

    # -- projects ----------------------------------------------------------------------------

    def upsert_project(self, rel_path: str, name: str, preexisting: bool, now: datetime) -> int:
        """Insert a newly discovered project (state ``unsealed``) or refresh its name.

        ``preexisting`` and ``first_seen`` are only set on first sight (D15).
        """
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO projects (rel_path, name, state, preexisting, first_seen)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(rel_path) DO UPDATE SET name = excluded.name",
                (rel_path, name, ProjectState.UNSEALED.value, int(preexisting), to_iso(now)),
            )
            row = conn.execute("SELECT id FROM projects WHERE rel_path = ?", (rel_path,)).fetchone()
            return int(row[0])

    def get_project(self, key: int | str) -> ProjectRow | None:
        """By id (``int``) or by ``rel_path`` (``str``)."""
        column = "id" if isinstance(key, int) else "rel_path"
        row = self._query_one(f"SELECT * FROM projects WHERE {column} = ?", (key,))
        return ProjectRow.from_row(row) if row is not None else None

    def list_projects(self, state: ProjectState | None = None) -> list[ProjectRow]:
        if state is None:
            rows = self._query("SELECT * FROM projects ORDER BY rel_path")
        else:
            rows = self._query(
                "SELECT * FROM projects WHERE state = ? ORDER BY rel_path", (state.value,)
            )
        return [ProjectRow.from_row(r) for r in rows]

    def set_state(
        self,
        project_id: int,
        state: ProjectState,
        *,
        error: str | None = None,
        review_reason: str | None = None,
    ) -> None:
        """Set the state; ``error`` and ``review_reason`` are overwritten (``None`` clears)."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE projects SET state = ?, error = ?, review_reason = ? WHERE id = ?",
                (state.value, error, review_reason, project_id),
            )

    def rename_project(self, project_id: int, rel_path: str, name: str) -> None:
        """D62: the folder was moved or renamed; the row (history, caches, jobs) follows it."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE projects SET rel_path = ?, name = ? WHERE id = ?",
                (rel_path, name, project_id),
            )

    def delete_project(self, project_id: int) -> None:
        """D60 (Retire): drop the row; ``ON DELETE CASCADE`` removes its files, sealed files,
        cached hashes, scans, review items, verify results and jobs (with their log)."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def update_project_fields(self, project_id: int, **fields: Any) -> None:
        """Update arbitrary ``projects`` columns (datetimes, enums and bools are converted)."""
        if not fields:
            return
        unknown = set(fields) - _PROJECT_COLUMNS
        if unknown:
            raise ValueError(f"unknown project columns: {sorted(unknown)}")
        names = sorted(fields)
        assignments = ", ".join(f"{n} = ?" for n in names)
        values = [_sql_value(fields[n]) for n in names]
        with self.transaction() as conn:
            conn.execute(f"UPDATE projects SET {assignments} WHERE id = ?", (*values, project_id))

    # -- files (last scan) -------------------------------------------------------------------

    def replace_files(self, project_id: int, files: Iterable[FileStat]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM files WHERE project_id = ?", (project_id,))
            conn.executemany(
                "INSERT INTO files (project_id, rel_path, size, mtime_ns) VALUES (?, ?, ?, ?)",
                ((project_id, f.rel_path, f.size, f.mtime_ns) for f in files),
            )

    def get_files(self, project_id: int) -> dict[str, FileStat]:
        rows = self._query(
            "SELECT rel_path, size, mtime_ns FROM files WHERE project_id = ?", (project_id,)
        )
        return {r["rel_path"]: FileStat(r["rel_path"], r["size"], r["mtime_ns"]) for r in rows}

    # -- sealed_files (what the last manifest says) ------------------------------------------

    def replace_sealed_files(self, project_id: int, rows: Iterable[SealedFile]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM sealed_files WHERE project_id = ?", (project_id,))
            conn.executemany(
                "INSERT INTO sealed_files (project_id, rel_path, size, mtime_ns, xxh128)"
                " VALUES (?, ?, ?, ?, ?)",
                ((project_id, s.rel_path, s.size, s.mtime_ns, s.xxh128) for s in rows),
            )

    def get_sealed_files(self, project_id: int) -> dict[str, SealedFile]:
        rows = self._query(
            "SELECT rel_path, size, mtime_ns, xxh128 FROM sealed_files WHERE project_id = ?",
            (project_id,),
        )
        return {
            r["rel_path"]: SealedFile(r["rel_path"], r["size"], r["mtime_ns"], r["xxh128"])
            for r in rows
        }

    # -- file_hashes (checkpoint cache, D28) -------------------------------------------------

    def get_cached_hashes(
        self, project_id: int, rel_path: str, size: int, mtime_ns: int
    ) -> dict[str, str]:
        """``{fmt: digest}`` valid only for this exact ``(size, mtime_ns)``."""
        rows = self._query(
            "SELECT fmt, digest FROM file_hashes"
            " WHERE project_id = ? AND rel_path = ? AND size = ? AND mtime_ns = ?",
            (project_id, rel_path, size, mtime_ns),
        )
        return {r["fmt"]: r["digest"] for r in rows}

    def put_hash(
        self,
        project_id: int,
        rel_path: str,
        size: int,
        mtime_ns: int,
        fmt: str,
        digest: str,
        now: datetime,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO file_hashes"
                " (project_id, rel_path, size, mtime_ns, fmt, digest, hashed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, rel_path, size, mtime_ns, fmt, digest, to_iso(now)),
            )

    def prune_hashes(self, project_id: int, keep: Iterable[FileStat]) -> int:
        """Drop cached hashes whose ``(rel_path, size, mtime_ns)`` is not in ``keep``."""
        wanted = {(f.rel_path, f.size, f.mtime_ns) for f in keep}
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT DISTINCT rel_path, size, mtime_ns FROM file_hashes WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            stale = [
                (project_id, r[0], r[1], r[2]) for r in rows if (r[0], r[1], r[2]) not in wanted
            ]
            conn.executemany(
                "DELETE FROM file_hashes"
                " WHERE project_id = ? AND rel_path = ? AND size = ? AND mtime_ns = ?",
                stale,
            )
            return len(stale)

    # -- scans -------------------------------------------------------------------------------

    def start_scan(self, kind: str, project_id: int | None, now: datetime) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO scans (project_id, kind, started_at, status) VALUES (?, ?, ?, ?)",
                (project_id, kind, to_iso(now), "running"),
            )
            assert cur.lastrowid is not None
            return cur.lastrowid

    def finish_scan(
        self,
        scan_id: int,
        *,
        files: int,
        bytes: int,
        added: int = 0,
        modified: int = 0,
        deleted: int = 0,
        status: str = "ok",
        now: datetime,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE scans SET finished_at = ?, files = ?, bytes = ?, added = ?,"
                " modified = ?, deleted = ?, status = ? WHERE id = ?",
                (to_iso(now), files, bytes, added, modified, deleted, status, scan_id),
            )

    # -- jobs --------------------------------------------------------------------------------

    def enqueue_job(
        self,
        kind: JobKind,
        project_id: int | None,
        trigger: Trigger,
        priority: int,
        now: datetime,
        *,
        bypass_hours: bool = False,
    ) -> int:
        """Queue a job; if one of the same kind for the same project is queued or running,
        return its id instead (no duplicates). A manual request finding a queued automatic one
        promotes it (trigger, priority, ``bypass_hours``): e.g. Verify now (D63) over the
        verification the scheduler already queued."""
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT id, state FROM jobs WHERE kind = ? AND project_id IS ? AND state IN (?, ?)"
                " ORDER BY id LIMIT 1",
                (kind.value, project_id, JobState.QUEUED.value, JobState.RUNNING.value),
            ).fetchone()
            if existing is not None:
                if trigger is Trigger.MANUAL and existing["state"] == JobState.QUEUED.value:
                    conn.execute(
                        "UPDATE jobs SET trigger = ?, priority = MAX(priority, ?),"
                        " bypass_hours = MAX(bypass_hours, ?) WHERE id = ?",
                        (trigger.value, priority, int(bypass_hours), existing["id"]),
                    )
                return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO jobs (kind, project_id, trigger, state, priority, created_at,"
                " bypass_hours) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    kind.value,
                    project_id,
                    trigger.value,
                    JobState.QUEUED.value,
                    priority,
                    to_iso(now),
                    int(bypass_hours),
                ),
            )
            assert cur.lastrowid is not None
            return cur.lastrowid

    def record_finished_job(
        self,
        kind: JobKind,
        project_id: int | None,
        trigger: Trigger,
        state: JobState,
        now: datetime,
    ) -> int:
        """Insert a job that is already over (``started_at = finished_at = now``); never seen by
        the hasher. Used for Retire (D60), whose job row is the only trace left."""
        stamp = to_iso(now)
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO jobs (kind, project_id, trigger, state, priority, created_at,"
                " started_at, finished_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (kind.value, project_id, trigger.value, state.value, stamp, stamp, stamp),
            )
            assert cur.lastrowid is not None
            return cur.lastrowid

    def get_job(self, job_id: int) -> JobRow | None:
        row = self._query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return JobRow.from_row(row) if row is not None else None

    def next_job(self, now: datetime, *, bypass_only: bool = False) -> JobRow | None:
        """Highest-priority queued job, oldest first. ``now`` is reserved for delayed jobs.
        ``bypass_only``: only jobs allowed inside working hours (D63, the gate is closed)."""
        del now
        extra = " AND bypass_hours = 1" if bypass_only else ""
        row = self._query_one(
            f"SELECT * FROM jobs WHERE state = ?{extra}"
            " ORDER BY priority DESC, created_at, id LIMIT 1",
            (JobState.QUEUED.value,),
        )
        return JobRow.from_row(row) if row is not None else None

    def set_job_state(
        self, job_id: int, state: JobState, now: datetime, *, error: str | None = None
    ) -> None:
        """``running`` stamps ``started_at``; terminal states stamp ``finished_at``."""
        stamp = to_iso(now)
        with self.transaction() as conn:
            if state is JobState.RUNNING:
                conn.execute(
                    "UPDATE jobs SET state = ?, started_at = ?, finished_at = NULL, error = ?"
                    " WHERE id = ?",
                    (state.value, stamp, error, job_id),
                )
            elif state in _TERMINAL_JOB_STATES:
                conn.execute(
                    "UPDATE jobs SET state = ?, finished_at = ?, error = ? WHERE id = ?",
                    (state.value, stamp, error, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET state = ?, error = ? WHERE id = ?",
                    (state.value, error, job_id),
                )

    def set_job_bypass_hours(self, job_id: int) -> bool:
        """Let a job run inside working hours (Seal now / Accept now, D73; Verify now is born
        with it, D63). True if the flag changed, False if it was already set (or no such job)."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE jobs SET bypass_hours = 1 WHERE id = ? AND bypass_hours = 0", (job_id,)
            )
            return cur.rowcount == 1

    def update_job_progress(
        self, job_id: int, files_done: int, files_total: int, bytes_done: int, bytes_total: int
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET files_done = ?, files_total = ?, bytes_done = ?, bytes_total = ?"
                " WHERE id = ?",
                (files_done, files_total, bytes_done, bytes_total, job_id),
            )

    def requeue_running_jobs(self) -> int:
        """At startup: jobs left ``running`` by a crash or SIGTERM go back to ``queued``."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state = ?, started_at = NULL WHERE state = ?",
                (JobState.QUEUED.value, JobState.RUNNING.value),
            )
            return cur.rowcount

    def cancel_queued_jobs(self, project_id: int, now: datetime) -> int:
        """Cancel every ``queued`` job of a project (e.g. the project was ignored)."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state = ?, finished_at = ? WHERE project_id = ? AND state = ?",
                (JobState.CANCELLED.value, to_iso(now), project_id, JobState.QUEUED.value),
            )
            return cur.rowcount

    def queued_job(self, project_id: int) -> JobRow | None:
        """The ``queued`` job of a project (the one the hasher would pick first), if any."""
        row = self._query_one(
            "SELECT * FROM jobs WHERE project_id = ? AND state = ?"
            " ORDER BY priority DESC, created_at, id LIMIT 1",
            (project_id, JobState.QUEUED.value),
        )
        return JobRow.from_row(row) if row is not None else None

    def running_job(self, project_id: int) -> JobRow | None:
        """The ``running`` job of a project, if any (one hasher: at most one, D34)."""
        row = self._query_one(
            "SELECT * FROM jobs WHERE project_id = ? AND state = ? ORDER BY id DESC LIMIT 1",
            (project_id, JobState.RUNNING.value),
        )
        return JobRow.from_row(row) if row is not None else None

    def has_open_job(self, project_id: int | None, kind: JobKind) -> bool:
        """True if a job of ``kind`` for the project is ``queued`` or ``running``."""
        row = self._query_one(
            "SELECT 1 FROM jobs WHERE kind = ? AND project_id IS ? AND state IN (?, ?) LIMIT 1",
            (kind.value, project_id, JobState.QUEUED.value, JobState.RUNNING.value),
        )
        return row is not None

    def count_jobs(self, state: JobState) -> int:
        row = self._query_one("SELECT COUNT(*) FROM jobs WHERE state = ?", (state.value,))
        return int(row[0]) if row is not None else 0

    def list_jobs(self, limit: int = 50) -> list[JobRow]:
        """Most recent first."""
        rows = self._query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [JobRow.from_row(r) for r in rows]

    # -- job_log -----------------------------------------------------------------------------

    def log(self, job_id: int, level: str, msg: str, now: datetime) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO job_log (job_id, ts, level, msg) VALUES (?, ?, ?, ?)",
                (job_id, to_iso(now), level, msg),
            )

    def get_job_log(self, job_id: int) -> list[LogEntry]:
        rows = self._query(
            "SELECT job_id, ts, level, msg FROM job_log WHERE job_id = ? ORDER BY id", (job_id,)
        )
        return [LogEntry(r["job_id"], r["ts"], r["level"], r["msg"]) for r in rows]

    # -- review_items ------------------------------------------------------------------------

    def replace_review_items(self, project_id: int, items: Iterable[ReviewItem]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM review_items WHERE project_id = ?", (project_id,))
            conn.executemany(
                "INSERT INTO review_items (project_id, rel_path, change, old_size, new_size,"
                " old_mtime_ns, new_mtime_ns) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        project_id,
                        i.rel_path,
                        i.change.value,
                        i.old_size,
                        i.new_size,
                        i.old_mtime_ns,
                        i.new_mtime_ns,
                    )
                    for i in items
                ),
            )

    def get_review_items(self, project_id: int) -> list[ReviewItem]:
        rows = self._query(
            "SELECT rel_path, change, old_size, new_size, old_mtime_ns, new_mtime_ns"
            " FROM review_items WHERE project_id = ? ORDER BY rel_path",
            (project_id,),
        )
        return [
            ReviewItem(
                r["rel_path"],
                ChangeKind(r["change"]),
                r["old_size"],
                r["new_size"],
                r["old_mtime_ns"],
                r["new_mtime_ns"],
            )
            for r in rows
        ]

    def clear_review_items(self, project_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM review_items WHERE project_id = ?", (project_id,))

    # -- verify_results (hito 4) -------------------------------------------------------------

    def replace_verify_results(
        self, job_id: int, project_id: int, rows: Iterable[VerifyResult]
    ) -> None:
        """Results of one verify job; earlier jobs of the project keep theirs."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM verify_results WHERE job_id = ?", (job_id,))
            conn.executemany(
                "INSERT INTO verify_results (job_id, project_id, rel_path, expected, actual,"
                " status) VALUES (?, ?, ?, ?, ?, ?)",
                ((job_id, project_id, r.rel_path, r.expected, r.actual, r.status) for r in rows),
            )

    def get_verify_results(self, project_id: int, job_id: int | None = None) -> list[VerifyResult]:
        """Results of ``job_id``, or of the project's latest verify job if ``None``."""
        if job_id is None:
            row = self._query_one(
                "SELECT MAX(job_id) FROM verify_results WHERE project_id = ?", (project_id,)
            )
            if row is None or row[0] is None:
                return []
            job_id = int(row[0])
        rows = self._query(
            "SELECT rel_path, expected, actual, status FROM verify_results"
            " WHERE project_id = ? AND job_id = ? ORDER BY rel_path",
            (project_id, job_id),
        )
        return [VerifyResult(r["rel_path"], r["expected"], r["actual"], r["status"]) for r in rows]

    # -- settings_kv -------------------------------------------------------------------------

    def get_kv(self, key: str) -> str | None:
        row = self._query_one("SELECT value FROM settings_kv WHERE key = ?", (key,))
        return None if row is None else row[0]

    def set_kv(self, key: str, value: str | None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings_kv (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
