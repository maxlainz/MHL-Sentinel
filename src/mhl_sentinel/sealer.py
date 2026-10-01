"""Project state machine and jobs (docs/arquitectura.md, "Estados de un proyecto").

This is the only module that changes ``projects.state``. Three layers:

- :func:`classify` / :func:`should_auto_enqueue`: pure decisions from a scan diff (D9, D15, D31).
- :func:`run_scan_cycle`: discovery → scan → classify → enqueue (no media file is read).
- :func:`run_job`: ``seal``, ``append`` and ``accept_new_version`` (D16, D17, D28, D39, D48).
  Only jobs read media files, through :mod:`hasher`, and only they write generations.

Orphan manifests (issue #1): a ``NNNN_*.mhl`` in ``ascmhl/`` that the chain does not list is
the trace of a crash between the two renames of ``mhlwriter._commit``. ``ascmhl`` would load it
as a generation (and number the next one after it), so it is renamed to ``<name>.orphan`` before
any history is loaded; the next generation number then comes from the chain.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
import threading
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ascmhl import chain_xml_parser
from ascmhl.history import MHLHistory

from mhl_sentinel import __version__, legacy_mhl
from mhl_sentinel.clock import to_iso, utcnow
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, JobRow, ProjectRow, ReviewItem, SealedFile
from mhl_sentinel.discovery import discover_projects
from mhl_sentinel.hasher import FileChanged, Gate, Stopped, hash_project
from mhl_sentinel.mhlwriter import (
    DEFAULT_IGNORE_PATTERNS,
    PRIMARY_HASH_FORMAT,
    SUPERSEDED_DIR,
    MHLReviewError,
    write_project_generation,
)
from mhl_sentinel.models import (
    ChangeKind,
    FileStat,
    JobKind,
    JobState,
    ProjectState,
    ScanDiff,
    Trigger,
)
from mhl_sentinel.scanner import diff_against_sealed, is_settled, scan_project

log = logging.getLogger(__name__)

HISTORY_DIR = "ascmhl"
CHAIN_FILE = "ascmhl_chain.xml"
ORPHAN_SUFFIX = ".orphan"
FIRST_DISCOVERY_KEY = "first_discovery_done"
ROOT_MANIFEST_STALE_KEY = "root_manifest_stale"  # consumed by the root manifest (hito 4)
MAX_LISTED_PATHS = 10
_SUPPORTED_LEGACY_FORMATS = frozenset({"xxh64", "md5", "sha1"})

# Queue priorities (contract: manual > seal > append > verify).
PRIORITY: dict[JobKind, int] = {
    JobKind.ACCEPT_NEW_VERSION: 30,
    JobKind.SEAL: 30,
    JobKind.APPEND: 20,
    JobKind.VERIFY: 10,
    JobKind.ROOT_MANIFEST: 5,
}
MANUAL_BONUS = 100

ProgressFn = Callable[[int, int, int, int], None]  # files_done, files_total, bytes_done, total

_UNTOUCHED = frozenset({ProjectState.IGNORED, ProjectState.QUEUED, ProjectState.HASHING})


class SealerError(RuntimeError):
    """A request that does not apply to the project in its current state."""


class ArchiveUnavailableError(RuntimeError):
    """The archive root does not exist (share not mounted?): nothing is touched."""


@dataclass(slots=True)
class ScanSummary:
    discovered: int = 0
    new_projects: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # folders gone from disk → error
    scanned: int = 0
    states: dict[str, ProjectState] = field(default_factory=dict)
    enqueued: list[tuple[str, JobKind]] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)  # archive-relative paths, now *.orphan
    errors: list[str] = field(default_factory=list)


# --- pure decisions --------------------------------------------------------------------------


def _is_sealed(project: ProjectRow) -> bool:
    return project.last_generation_no is not None


def _list_paths(paths: Iterable[str]) -> str:
    items = list(paths)
    shown = ", ".join(items[:MAX_LISTED_PATHS])
    more = len(items) - MAX_LISTED_PATHS
    return shown + (f" (+{more} more)" if more > 0 else "")


def review_reason_for(diff: ScanDiff) -> str:
    parts = []
    if diff.modified:
        parts.append(f"{len(diff.modified)} modified")
    if diff.deleted:
        parts.append(f"{len(diff.deleted)} deleted")
    paths = sorted(
        [cur.rel_path for _old, cur in diff.modified] + [d.rel_path for d in diff.deleted]
    )
    return f"{' and '.join(parts)} since the last seal: {_list_paths(paths)}"


def classify(
    project: ProjectRow, diff: ScanDiff, settings: Settings, now: datetime
) -> tuple[ProjectState, str | None]:
    """State implied by a fresh scan (contract state table). Returns ``(state, review_reason)``.

    ``ignored``/``queued``/``hashing`` are untouched; ``needs_review`` stays until Accept or
    Postpone; ``error`` is recomputed from the scan (retry). Enqueueing is decided separately by
    :func:`should_auto_enqueue`.
    """
    del settings, now  # part of the contract; settle time is the enqueue decision's business
    if project.state in _UNTOUCHED or project.state is ProjectState.NEEDS_REVIEW:
        return project.state, project.review_reason
    if not _is_sealed(project):
        return ProjectState.UNSEALED, None
    if diff.modified or diff.deleted:
        return ProjectState.NEEDS_REVIEW, review_reason_for(diff)
    if diff.added:
        return ProjectState.CHANGED, None
    return ProjectState.SEALED, None


def should_auto_enqueue(
    project: ProjectRow, diff: ScanDiff, settings: Settings, now: datetime
) -> bool:
    """``project.state`` is the state just decided by :func:`classify`.

    ``unsealed``: only if not preexisting (D15) and settled (D31). ``changed``: if settled (D9).
    """
    settled = is_settled(diff, int(now.timestamp() * 1e9), settings.settle_hours)
    if project.state is ProjectState.UNSEALED:
        return not project.preexisting and settled
    if project.state is ProjectState.CHANGED:
        return settled
    return False


def auto_job_kind(state: ProjectState) -> JobKind | None:
    return {ProjectState.UNSEALED: JobKind.SEAL, ProjectState.CHANGED: JobKind.APPEND}.get(state)


# --- history helpers -------------------------------------------------------------------------


def quarantine_orphan_manifests(project_root: Path) -> list[Path]:
    """Issue #1: rename every ``ascmhl/*.mhl`` not listed in the chain to ``*.mhl.orphan``.

    Returns the new paths. Without ``ascmhl/`` it does nothing.
    """
    asc_dir = project_root / HISTORY_DIR
    if not asc_dir.is_dir():
        return []
    chain = chain_xml_parser.parse(str(asc_dir / CHAIN_FILE))
    listed = {Path(str(g.ascmhl_filename)).name for g in chain.generations}
    moved: list[Path] = []
    for entry in sorted(asc_dir.iterdir()):
        if not entry.is_file() or entry.name.startswith("._") or entry.suffix != ".mhl":
            continue
        if entry.name in listed:
            continue
        target = entry.with_name(entry.name + ORPHAN_SUFFIX)
        n = 1
        while target.exists():
            target = entry.with_name(f"{entry.name}{ORPHAN_SUFFIX}.{n}")
            n += 1
        entry.rename(target)
        log.warning("orphan manifest %s (not in the chain) renamed to %s", entry.name, target.name)
        moved.append(target)
    return moved


def has_history(project_root: Path) -> bool:
    return (project_root / HISTORY_DIR / CHAIN_FILE).is_file()


def write_ignore_patterns(settings: Settings) -> list[str]:
    """Ignore patterns given to the writer: defaults + ``exclude_globs`` (D14: fixed in the
    manifest; basename globs mean the same in gitignore syntax). The writer merges them with
    the latest generation's patterns (they only grow)."""
    out = list(DEFAULT_IGNORE_PATTERNS)
    out.extend(g for g in settings.exclude_globs if g not in out)
    return out


def scan_ignore_patterns(project_root: Path, settings: Settings) -> list[str]:
    """Latest manifest's patterns (if a history exists) plus :func:`write_ignore_patterns`.

    Raises whatever ``MHLHistory.load_from_path`` raises on a broken history.
    """
    patterns = write_ignore_patterns(settings)
    if has_history(project_root):
        latest = MHLHistory.load_from_path(str(project_root)).latest_ignore_patterns() or []
        patterns = list(dict.fromkeys([*latest, *patterns]))
    return patterns


def retire_history(project_root: Path, now: datetime) -> Path | None:
    """D17: move ``ascmhl/`` to ``ascmhl_superseded/<YYYY-MM-DDTHHMMSSZ>/`` (never deleted)."""
    asc_dir = project_root / HISTORY_DIR
    if not asc_dir.exists():
        return None
    stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    target = project_root / SUPERSEDED_DIR / stamp
    n = 1
    while target.exists():
        target = project_root / SUPERSEDED_DIR / f"{stamp}-{n}"
        n += 1
    target.parent.mkdir(exist_ok=True)
    shutil.move(str(asc_dir), str(target))
    return target


def _generation_no(manifest: Path) -> int:
    return int(manifest.name.split("_", 1)[0])


# --- scan cycle ------------------------------------------------------------------------------


def _as_filestats(sealed: Mapping[str, SealedFile]) -> dict[str, FileStat]:
    return {p: FileStat(s.rel_path, s.size, s.mtime_ns) for p, s in sealed.items()}


def _review_items_from_diff(diff: ScanDiff) -> list[ReviewItem]:
    items = [
        ReviewItem(
            cur.rel_path, ChangeKind.MODIFIED, old.size, cur.size, old.mtime_ns, cur.mtime_ns
        )
        for old, cur in diff.modified
    ]
    items += [
        ReviewItem(d.rel_path, ChangeKind.DELETED, d.size, None, d.mtime_ns, None)
        for d in diff.deleted
    ]
    return items


def enqueue(db: Database, project_id: int, kind: JobKind, trigger: Trigger, now: datetime) -> int:
    priority = PRIORITY[kind] + (MANUAL_BONUS if trigger is Trigger.MANUAL else 0)
    return db.enqueue_job(kind, project_id, trigger, priority, now)


def run_scan_cycle(
    db: Database,
    settings: Settings,
    *,
    now: datetime,
    first_discovery_marker: str = FIRST_DISCOVERY_KEY,
) -> ScanSummary:
    """Discovery → per-project scan → classify → persist → auto-enqueue. Reads no media file."""
    root = settings.archive_root
    if not root.is_dir():
        raise ArchiveUnavailableError(f"archive root {root} is not a directory")
    summary = ScanSummary()
    first = db.get_kv(first_discovery_marker) is None
    candidates = discover_projects(root, settings.project_depth, settings.ignore_prefixes)
    summary.discovered = len(candidates)
    on_disk = {c.rel_path for c in candidates}
    for cand in candidates:
        if db.get_project(cand.rel_path) is None:
            summary.new_projects.append(cand.rel_path)
        db.upsert_project(cand.rel_path, cand.name, preexisting=first, now=now)
    if first:
        db.set_kv(first_discovery_marker, to_iso(now))

    for project in db.list_projects():
        if project.rel_path in on_disk:
            continue
        if project.state is not ProjectState.IGNORED and project.error != "folder missing":
            db.set_state(
                project.id,
                ProjectState.ERROR,
                error="folder missing",
                review_reason=project.review_reason,
            )
            log.warning("project folder missing: %s", project.rel_path)
        if project.state is not ProjectState.IGNORED:
            summary.missing.append(project.rel_path)
            summary.states[project.rel_path] = ProjectState.ERROR

    for project in db.list_projects():
        if project.rel_path not in on_disk or project.state is ProjectState.IGNORED:
            if project.state is ProjectState.IGNORED:
                summary.states[project.rel_path] = project.state
            continue
        _scan_one(db, settings, project, now, summary)
    return summary


def _scan_one(
    db: Database, settings: Settings, project: ProjectRow, now: datetime, summary: ScanSummary
) -> None:
    proj_root = settings.archive_root / project.rel_path
    scan_id = db.start_scan("project", project.id, now)
    try:
        for orphan in quarantine_orphan_manifests(proj_root):
            summary.orphans.append(f"{project.rel_path}/{HISTORY_DIR}/{orphan.name}")
        patterns = scan_ignore_patterns(proj_root, settings)
        outcome = scan_project(proj_root, patterns, settings.exclude_globs)
    except Exception as exc:  # broken history, unreadable folder...
        msg = f"scan failed: {exc}"
        db.finish_scan(scan_id, files=0, bytes=0, status="error", now=now)
        db.set_state(project.id, ProjectState.ERROR, error=msg, review_reason=project.review_reason)
        summary.errors.append(f"{project.rel_path}: {msg}")
        summary.states[project.rel_path] = ProjectState.ERROR
        return
    if outcome.errors:
        # An unreadable entry would look deleted: do not classify on an incomplete snapshot.
        msg = "scan errors: " + _list_paths(outcome.errors)
        db.finish_scan(scan_id, files=len(outcome.files), bytes=0, status="error", now=now)
        db.set_state(project.id, ProjectState.ERROR, error=msg, review_reason=project.review_reason)
        summary.errors.append(f"{project.rel_path}: {msg}")
        summary.states[project.rel_path] = ProjectState.ERROR
        return

    previous = db.get_files(project.id) if project.last_scan_at is not None else None
    sealed = _as_filestats(db.get_sealed_files(project.id))
    diff = diff_against_sealed(outcome.files, sealed, previous)
    db.replace_files(project.id, diff.files)
    db.finish_scan(
        scan_id,
        files=len(diff.files),
        bytes=diff.total_bytes,
        added=len(diff.added),
        modified=len(diff.modified),
        deleted=len(diff.deleted),
        now=now,
    )
    fields: dict[str, object] = {
        "last_scan_at": now,
        "file_count": len(diff.files),
        "total_bytes": diff.total_bytes,
    }
    if not diff.stable:
        fields["last_change_at"] = now
        fields["stable_since"] = now
    elif project.stable_since is None:
        fields["stable_since"] = now
    db.update_project_fields(project.id, **fields)
    summary.scanned += 1

    state, reason = classify(project, diff, settings, now)
    error = project.error if state in _UNTOUCHED else None
    if (state, reason, error) != (project.state, project.review_reason, project.error):
        db.set_state(project.id, state, error=error, review_reason=reason)
    if state is ProjectState.NEEDS_REVIEW:
        items = _review_items_from_diff(diff) if _is_sealed(project) else []
        if items:  # otherwise keep the items of the original cause (e.g. legacy mismatch)
            db.replace_review_items(project.id, items)
    elif state not in _UNTOUCHED:
        db.clear_review_items(project.id)
    summary.states[project.rel_path] = state

    decided = dataclasses.replace(project, state=state)
    kind = auto_job_kind(state)
    if kind is not None and should_auto_enqueue(decided, diff, settings, now):
        enqueue(db, project.id, kind, Trigger.AUTO, now)
        db.set_state(project.id, ProjectState.QUEUED)
        summary.enqueued.append((project.rel_path, kind))
        summary.states[project.rel_path] = ProjectState.QUEUED


# --- user requests (GUI buttons) -------------------------------------------------------------


def _require(db: Database, project_id: int) -> ProjectRow:
    project = db.get_project(project_id)
    if project is None:
        raise SealerError(f"no project with id {project_id}")
    return project


def request_seal(db: Database, project_id: int, now: datetime) -> int:
    """Seal button (D16, D31): any ``unsealed`` project, preexisting or not."""
    project = _require(db, project_id)
    if project.state is not ProjectState.UNSEALED:
        raise SealerError(f"{project.rel_path}: Seal needs state unsealed, not {project.state}")
    job_id = enqueue(db, project_id, JobKind.SEAL, Trigger.MANUAL, now)
    db.set_state(project_id, ProjectState.QUEUED)
    log.info("seal requested for %s (job %d)", project.rel_path, job_id)
    return job_id


def request_ignore(db: Database, project_id: int, now: datetime) -> None:
    """Ignore button (D19). Queued jobs are cancelled; refused while hashing."""
    project = _require(db, project_id)
    if project.state is ProjectState.HASHING:
        raise SealerError(f"{project.rel_path}: cannot ignore while hashing")
    db.cancel_queued_jobs(project_id, now)
    db.set_state(project_id, ProjectState.IGNORED)
    log.info("project ignored: %s", project.rel_path)


def request_unignore(db: Database, project_id: int, now: datetime) -> None:
    """Back to ``unsealed`` or ``sealed``; the next scan classifies it for real."""
    del now
    project = _require(db, project_id)
    if project.state is not ProjectState.IGNORED:
        raise SealerError(f"{project.rel_path}: not ignored")
    state = ProjectState.SEALED if _is_sealed(project) else ProjectState.UNSEALED
    db.set_state(project_id, state)
    log.info("project un-ignored: %s", project.rel_path)


def request_accept_new_version(db: Database, project_id: int, now: datetime) -> int:
    """Accept as new version (D17): ``needs_review`` → queued ``accept_new_version`` job."""
    project = _require(db, project_id)
    if project.state is not ProjectState.NEEDS_REVIEW:
        raise SealerError(f"{project.rel_path}: Accept needs state needs_review")
    job_id = enqueue(db, project_id, JobKind.ACCEPT_NEW_VERSION, Trigger.MANUAL, now)
    db.set_state(project_id, ProjectState.QUEUED, review_reason=project.review_reason)
    log.info("accept as new version requested for %s (job %d)", project.rel_path, job_id)
    return job_id


def request_postpone(db: Database, project_id: int, now: datetime) -> None:
    """Postpone (D17): nothing changes; the project stays in review."""
    del now
    project = _require(db, project_id)
    if project.state is not ProjectState.NEEDS_REVIEW:
        raise SealerError(f"{project.rel_path}: Postpone needs state needs_review")
    log.info("review postponed for %s", project.rel_path)


def recover_after_restart(db: Database) -> int:
    """Jobs left ``running`` go back to ``queued``, and so do their ``hashing`` projects."""
    count = db.requeue_running_jobs()
    for project in db.list_projects(ProjectState.HASHING):
        db.set_state(project.id, ProjectState.QUEUED)
    return count


# --- jobs ------------------------------------------------------------------------------------


class _DbHashCache:
    def __init__(self, db: Database, project_id: int, now_fn: Callable[[], datetime]) -> None:
        self._db = db
        self._pid = project_id
        self._now = now_fn

    def get(self, rel_path: str, size: int, mtime_ns: int) -> dict[str, str]:
        return self._db.get_cached_hashes(self._pid, rel_path, size, mtime_ns)

    def put(self, rel_path: str, size: int, mtime_ns: int, fmt: str, digest: str) -> None:
        self._db.put_hash(self._pid, rel_path, size, mtime_ns, fmt, digest, self._now())


@dataclass(slots=True)
class _Ctx:
    db: Database
    settings: Settings
    job: JobRow
    project: ProjectRow
    root: Path
    gate: Gate
    stop: threading.Event
    now_fn: Callable[[], datetime]
    on_progress: ProgressFn | None = None

    def log(self, level: str, msg: str) -> None:
        self.db.log(self.job.id, level, msg, self.now_fn())
        getattr(log, "warning" if level == "warning" else "info")(
            "job %d %s %s: %s", self.job.id, self.job.kind, self.project.rel_path, msg
        )

    def finish(self, state: JobState, error: str | None = None) -> None:
        self.db.set_job_state(self.job.id, state, self.now_fn(), error=error)


def run_job(
    db: Database,
    settings: Settings,
    job: JobRow,
    *,
    gate: Gate,
    stop: threading.Event,
    now_fn: Callable[[], datetime] = utcnow,
    on_progress: ProgressFn | None = None,
) -> None:
    """Run one queued job to completion, review, requeue (``Stopped``) or failure.

    ``on_progress(files_done, files_total, bytes_done, bytes_total)`` is called after each
    progress write to the DB (the supervisor turns it into ``job.progress`` events).
    """
    if job.project_id is None or job.kind not in (
        JobKind.SEAL,
        JobKind.APPEND,
        JobKind.ACCEPT_NEW_VERSION,
    ):
        db.set_job_state(job.id, JobState.FAILED, now_fn(), error=f"{job.kind}: not implemented")
        return
    project = db.get_project(job.project_id)
    if project is None:
        db.set_job_state(job.id, JobState.FAILED, now_fn(), error="project not found")
        return
    ctx = _Ctx(
        db,
        settings,
        job,
        project,
        settings.archive_root / project.rel_path,
        gate,
        stop,
        now_fn,
        on_progress,
    )
    db.set_job_state(job.id, JobState.RUNNING, now_fn())
    db.set_state(project.id, ProjectState.HASHING, review_reason=project.review_reason)
    ctx.log("info", f"start {job.kind} ({job.trigger})")
    try:
        if not ctx.root.is_dir():
            raise FileNotFoundError("folder missing")
        for orphan in quarantine_orphan_manifests(ctx.root):
            ctx.log("warning", f"orphan manifest renamed to {orphan.name} (issue #1)")
        if job.kind is JobKind.APPEND:
            _run_append(ctx)
        else:
            _run_seal(ctx, accept=job.kind is JobKind.ACCEPT_NEW_VERSION)
    except Stopped:
        db.set_job_state(job.id, JobState.QUEUED, now_fn())
        db.set_state(project.id, ProjectState.QUEUED, review_reason=project.review_reason)
        ctx.log("info", "stopped; back in the queue (hashes already done are kept)")
    except FileChanged as exc:
        msg = f"file kept changing while being read: {exc}"
        ctx.finish(JobState.FAILED, msg)
        db.set_state(project.id, ProjectState.ERROR, error=msg, review_reason=project.review_reason)
        ctx.log("error", msg)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        ctx.finish(JobState.FAILED, msg)
        db.set_state(project.id, ProjectState.ERROR, error=msg, review_reason=project.review_reason)
        ctx.log("error", msg)


def _scan_for_job(ctx: _Ctx, patterns: list[str]) -> list[FileStat]:
    outcome = scan_project(ctx.root, patterns, ctx.settings.exclude_globs)
    if outcome.errors:
        raise OSError("scan errors: " + _list_paths(outcome.errors))
    return outcome.files


def _hash(
    ctx: _Ctx, files: list[FileStat], expected: Mapping[str, Mapping[str, str]]
) -> dict[str, dict[str, str]]:
    def formats_for(rel_path: str) -> Collection[str]:
        legacy = set(expected.get(rel_path, {})) & _SUPPORTED_LEGACY_FORMATS
        return {PRIMARY_HASH_FORMAT, *legacy}

    def progress(files_done: int, files_total: int, bytes_done: int, bytes_total: int) -> None:
        ctx.db.update_job_progress(ctx.job.id, files_done, files_total, bytes_done, bytes_total)
        if ctx.on_progress is not None:
            ctx.on_progress(files_done, files_total, bytes_done, bytes_total)

    progress(0, len(files), 0, sum(f.size for f in files))
    digests = hash_project(
        ctx.root,
        files,
        formats_for,
        _DbHashCache(ctx.db, ctx.project.id, ctx.now_fn),
        gate=ctx.gate,
        stop=ctx.stop,
        progress=progress,
    )
    for f in files:  # changed between the scan and the read (cache hit or not): not quiescent
        st = (ctx.root / f.rel_path).stat()
        if (st.st_size, st.st_mtime_ns) != (f.size, f.mtime_ns):
            raise FileChanged(f.rel_path)
    return digests


def _legacy_problems(
    ctx: _Ctx,
    expected: Mapping[str, Mapping[str, str]],
    present: Mapping[str, FileStat],
    digests: Mapping[str, Mapping[str, str]],
) -> list[ReviewItem]:
    """D39: every file a legacy MHL 1.x lists must exist and match (only hashed ones checked)."""
    items: list[ReviewItem] = []
    for rel in sorted(expected):
        fs = present.get(rel)
        if fs is None:
            if (ctx.root / rel).is_file():
                ctx.log("warning", f"legacy MHL lists {rel}, excluded by the ignore patterns")
                continue
            items.append(ReviewItem(rel, ChangeKind.DELETED))
            continue
        got = digests.get(rel)
        if got is None:
            continue
        for fmt, digest in expected[rel].items():
            if fmt in _SUPPORTED_LEGACY_FORMATS and got.get(fmt) != digest:
                items.append(ReviewItem(rel, ChangeKind.MODIFIED, None, fs.size, None, fs.mtime_ns))
                break
    return items


def _to_review(ctx: _Ctx, reason: str, items: list[ReviewItem]) -> None:
    ctx.db.replace_review_items(ctx.project.id, items)
    ctx.db.set_state(ctx.project.id, ProjectState.NEEDS_REVIEW, review_reason=reason)
    ctx.finish(JobState.DONE)
    ctx.log("warning", f"needs review, nothing written: {reason}")


def _legacy_expectations(ctx: _Ctx) -> dict[str, dict[str, str]]:
    expected, warnings = legacy_mhl.expected_hashes_with_warnings(ctx.root)
    for w in warnings:
        ctx.log("warning", f"legacy MHL: {w}")
    if expected:
        ctx.log("info", f"legacy MHL 1.x: {len(expected)} files with origin hashes")
    return expected


def _inherited(expected: Mapping[str, Mapping[str, str]]) -> set[str]:
    return {fmt for hashes in expected.values() for fmt in hashes} & _SUPPORTED_LEGACY_FORMATS


def _after_generation(
    ctx: _Ctx, manifest: Path, sealed: Iterable[SealedFile], files: list[FileStat]
) -> None:
    now = ctx.now_fn()
    db, pid = ctx.db, ctx.project.id
    db.replace_sealed_files(pid, sealed)
    db.update_project_fields(
        pid,
        last_generation_no=_generation_no(manifest),
        last_sealed_at=now,
        file_count=len(files),
        total_bytes=sum(f.size for f in files),
    )
    db.clear_review_items(pid)
    db.set_state(pid, ProjectState.SEALED)
    db.prune_hashes(pid, files)
    db.set_kv(ROOT_MANIFEST_STALE_KEY, "1")
    ctx.finish(JobState.DONE)
    ctx.log("info", f"wrote {manifest.name}")


def _run_seal(ctx: _Ctx, *, accept: bool) -> None:
    """``seal`` and ``accept_new_version`` (= retire the history, then seal from scratch)."""
    if accept or not has_history(ctx.root):
        patterns = write_ignore_patterns(ctx.settings)  # fresh history
    else:
        patterns = scan_ignore_patterns(ctx.root, ctx.settings)
    files = _scan_for_job(ctx, patterns)
    expected = _legacy_expectations(ctx)
    digests = _hash(ctx, files, expected)
    present = {f.rel_path: f for f in files}
    problems = _legacy_problems(ctx, expected, present, digests)
    if problems:
        reason = f"{len(problems)} files do not match their legacy MHL 1.x: " + _list_paths(
            i.rel_path for i in problems
        )
        _to_review(ctx, reason, problems)
        return
    if accept:
        retired = retire_history(ctx.root, ctx.now_fn())
        if retired is not None:
            ctx.log("info", f"previous history moved to {retired.relative_to(ctx.root).as_posix()}")
    elif (ctx.root / HISTORY_DIR).is_dir() and not has_history(ctx.root):
        # Only orphans left (crash during the very first generation): set them aside.
        retired = retire_history(ctx.root, ctx.now_fn())
        ctx.log("warning", f"ascmhl/ without chain moved to {retired}")
    try:
        manifest = write_project_generation(
            ctx.root,
            digests,
            patterns,
            __version__,
            inherited_formats=_inherited(expected),
        )
    except MHLReviewError as exc:  # an existing (foreign) history disagrees with the tree
        _to_review(ctx, str(exc), [])
        return
    sealed = [
        SealedFile(f.rel_path, f.size, f.mtime_ns, digests[f.rel_path][PRIMARY_HASH_FORMAT])
        for f in files
    ]
    ctx.log("info", f"sealed {len(files)} files, {sum(f.size for f in files)} bytes")
    _after_generation(ctx, manifest, sealed, files)


def _run_append(ctx: _Ctx) -> None:
    """D48: partial generation with only the files added since the last seal."""
    patterns = scan_ignore_patterns(ctx.root, ctx.settings)
    files = _scan_for_job(ctx, patterns)
    sealed_rows = ctx.db.get_sealed_files(ctx.project.id)
    diff = diff_against_sealed(files, _as_filestats(sealed_rows), None)
    if diff.modified or diff.deleted:
        _to_review(ctx, review_reason_for(diff), _review_items_from_diff(diff))
        return
    if not diff.added:
        ctx.db.set_state(ctx.project.id, ProjectState.SEALED)
        ctx.finish(JobState.DONE)
        ctx.log("info", "nothing to append")
        return
    expected = _legacy_expectations(ctx)
    digests = _hash(ctx, diff.added, expected)
    present = {f.rel_path: f for f in files}
    problems = _legacy_problems(ctx, expected, present, digests)
    if problems:
        reason = f"{len(problems)} files do not match their legacy MHL 1.x: " + _list_paths(
            i.rel_path for i in problems
        )
        _to_review(ctx, reason, problems)
        return
    manifest = write_project_generation(
        ctx.root,
        digests,
        patterns,
        __version__,
        inherited_formats=_inherited(expected),
        partial=True,
    )
    merged = dict(sealed_rows)
    for f in diff.added:
        merged[f.rel_path] = SealedFile(
            f.rel_path, f.size, f.mtime_ns, digests[f.rel_path][PRIMARY_HASH_FORMAT]
        )
    ctx.log("info", f"appended {len(diff.added)} files")
    _after_generation(ctx, manifest, merged.values(), files)
