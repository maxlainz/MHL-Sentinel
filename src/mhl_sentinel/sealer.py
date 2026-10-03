"""Project state machine and jobs (docs/arquitectura.md, "Estados de un proyecto").

This is the only module that changes ``projects.state``. Three layers:

- :func:`classify` / :func:`should_auto_enqueue`: pure decisions from a scan diff (D9, D15, D31).
- :func:`run_scan_cycle`: discovery → scan → classify → enqueue (no media file is read).
- :func:`run_job`: ``seal``, ``append``, ``accept_new_version`` (D16, D17, D28, D39, D48),
  ``verify`` (D23, D8) and ``root_manifest`` (D29, :mod:`rootmanifest`). Only jobs read media
  files, through :mod:`hasher`, and only they write generations.
- :func:`schedule_maintenance`: staggered periodic verification (D23) and the root manifest job,
  called after a scan cycle.

Vanished folders (D58 to D62): a tracked folder that a scan does not find becomes ``missing`` (the
row, caches and history mirror are kept, D59); it goes back to its previous state when a scan
finds it again or when Retry finds it (D61), with an automatic verification if the app wrote
its history; a new folder whose ``ascmhl_chain.xml`` is byte-identical to the mirrored chain of
a vanished project is that project moved or renamed (D62); Retire (D60) forgets it. A root
that lists no project at all while the database has some is an unreachable archive, not an
archive where everything vanished: the cycle raises before touching any state.

Orphan manifests (issue #1): a ``NNNN_*.mhl`` in ``ascmhl/`` that the chain does not list is
the trace of a crash between the two renames of ``mhlwriter._commit``. ``ascmhl`` would load it
as a generation (and number the next one after it), so it is renamed to ``<name>.orphan`` before
any history is loaded; the next generation number then comes from the chain.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import stat
import threading
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ascmhl import chain_xml_parser
from ascmhl.history import MHLHistory

from mhl_sentinel import __version__, history_mirror, legacy_mhl, rootmanifest
from mhl_sentinel.clock import from_iso, to_iso, utcnow
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, JobRow, ProjectRow, ReviewItem, SealedFile, VerifyResult
from mhl_sentinel.discovery import discover_projects
from mhl_sentinel.hasher import FileChanged, Gate, StopFlag, Stopped, hash_project
from mhl_sentinel.mhlwriter import (
    DEFAULT_IGNORE_PATTERNS,
    PRIMARY_HASH_FORMAT,
    MHLReviewError,
    write_project_generation,
)
from mhl_sentinel.mhlwriter import (
    retire_history as retire_history,  # re-exported (D17)
)
from mhl_sentinel.models import (
    ChangeKind,
    FileStat,
    JobKind,
    JobState,
    ProjectCandidate,
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
ROOT_MANIFEST_STALE_KEY = rootmanifest.ROOT_MANIFEST_STALE_KEY  # set after every generation
VERIFY_DAY_KEY = "verify_day"  # local date (settings.timezone) of the counter below
VERIFY_DAY_COUNT_KEY = "verify_day_count"  # verify jobs enqueued automatically that day
VERIFICATION_PREFIX = "verification:"  # review_reason of a failed verify job
MTIME_TOLERANCE_NS = 1_000_000_000  # as scanner.diff_against_sealed
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
    missing: list[str] = field(default_factory=list)  # folders gone from disk → missing (D58)
    reappeared: list[str] = field(default_factory=list)  # missing folders found again (D58)
    moved: list[tuple[str, str]] = field(default_factory=list)  # (old, new) rel_paths (D62)
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
    stop: threading.Event | None = None,
) -> ScanSummary:
    """Discovery → moved folders (D62) → missing / reappeared folders (D58) → per-project scan
    → classify → persist → auto-enqueue. Reads no media file.

    ``stop`` (set by SIGTERM) ends the cycle between two projects, so a shutdown does not wait
    for a whole archive walk; every project already scanned is committed."""
    root = settings.archive_root
    if not root.is_dir():
        raise ArchiveUnavailableError(f"archive root {root} is not a directory")
    candidates = discover_projects(root, settings.project_depth, settings.ignore_prefixes)
    if not candidates and any(p.state is not ProjectState.IGNORED for p in db.list_projects()):
        # D58 firewall: an empty listing of a share that held projects is an unmounted or
        # unreadable share, never "every project vanished".
        raise ArchiveUnavailableError("archive root lists no projects")
    summary = ScanSummary()
    first = db.get_kv(first_discovery_marker) is None
    summary.discovered = len(candidates)
    on_disk = {c.rel_path for c in candidates}
    gone: list[ProjectRow] = []
    for p in db.list_projects():
        if p.rel_path in on_disk or p.state is ProjectState.IGNORED:
            continue
        if p.state is ProjectState.MISSING or _folder_gone(root / p.rel_path):
            gone.append(p)
        else:  # discovery did not list it, yet it is there (or unreadable): not "vanished"
            msg = "not listed by discovery but not confirmed gone; left as it is"
            log.warning("%s: %s", p.rel_path, msg)
            summary.errors.append(f"{p.rel_path}: {msg}")
    for cand in candidates:
        if db.get_project(cand.rel_path) is None:
            moved = _match_moved(db, settings, cand, gone, now) if cand.has_history else None
            if moved is not None:
                gone = [p for p in gone if p.id != moved.id]
                summary.moved.append((moved.rel_path, cand.rel_path))
                continue
            summary.new_projects.append(cand.rel_path)
        db.upsert_project(cand.rel_path, cand.name, preexisting=first, now=now)
    if first:
        db.set_kv(first_discovery_marker, to_iso(now))

    for project in gone:
        if project.state is not ProjectState.MISSING:
            _mark_missing(db, project, now)
        summary.missing.append(project.rel_path)
        summary.states[project.rel_path] = ProjectState.MISSING

    for project in db.list_projects(ProjectState.MISSING):
        if project.rel_path in on_disk:
            log.warning("project back on disk: %s", project.rel_path)
            if _restore_from_missing(db, project, now):
                summary.enqueued.append((project.rel_path, JobKind.VERIFY))
            summary.reappeared.append(project.rel_path)

    for project in db.list_projects():
        if stop is not None and stop.is_set():
            break
        if project.rel_path not in on_disk or project.state is ProjectState.IGNORED:
            if project.state is ProjectState.IGNORED:
                summary.states[project.rel_path] = project.state
            continue
        _scan_one(db, settings, project, now, summary)
    return summary


def _folder_gone(path: Path) -> bool:
    """D58: discovery swallows listing errors (an intermediate folder that fails to list on a
    flaky share yields no projects), so absence is confirmed by one ``lstat`` of the folder
    itself: only "no such file" (or something that is not a folder) counts as vanished; any
    other error, or a folder that is there, is not a decision to take this round."""
    try:
        st = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return not stat.S_ISDIR(st.st_mode)


# States that only make sense while the folder is there: a project that vanishes in them goes
# back to plain unsealed/sealed (the next scan classifies it again anyway).
_TRANSIENT = frozenset({ProjectState.QUEUED, ProjectState.HASHING, ProjectState.ERROR})


def _resting_state(project: ProjectRow) -> ProjectState:
    return ProjectState.SEALED if _is_sealed(project) else ProjectState.UNSEALED


def _mark_missing(db: Database, project: ProjectRow, now: datetime) -> None:
    """D58: ``missing`` from the first scan that misses the folder. The row keeps everything
    (review reason, sealed files, generation number); queued jobs are cancelled."""
    before = project.state
    if before in _TRANSIENT or before is ProjectState.MISSING:
        before = _resting_state(project)
    with db.transaction():
        db.cancel_queued_jobs(project.id, now)
        db.update_project_fields(
            project.id,
            state=ProjectState.MISSING,
            error=None,
            missing_since=now,
            state_before_missing=before,
        )
    log.warning("project folder missing: %s", project.rel_path)


def _state_to_restore(project: ProjectRow) -> ProjectState:
    before = project.state_before_missing
    if before is None or before in _TRANSIENT or before is ProjectState.MISSING:
        return _resting_state(project)
    return before


def _restore_from_missing(db: Database, project: ProjectRow, now: datetime) -> bool:
    """D58/D61: back to the state it had, and an automatic verification (normal priority) if
    the app wrote its history: the files were out of sight. Not for a project waiting in review:
    a verification there would be skipped (only Accept or Postpone apply). The scan that follows
    classifies the project as usual. Returns True if a verification was queued."""
    state = _state_to_restore(project)
    verify = _is_sealed(project) and state in (ProjectState.SEALED, ProjectState.CHANGED)
    with db.transaction():
        db.update_project_fields(
            project.id, state=state, missing_since=None, state_before_missing=None
        )
        if verify:
            enqueue(db, project.id, JobKind.VERIFY, Trigger.AUTO, now)
    return verify


def _match_moved(
    db: Database,
    settings: Settings,
    cand: ProjectCandidate,
    gone: list[ProjectRow],
    now: datetime,
) -> ProjectRow | None:
    """D62: a new folder whose ``ascmhl_chain.xml`` has the same bytes as the mirrored chain
    of a vanished project (already ``missing`` or missed by this very cycle) is that project,
    moved or renamed. The row follows the folder (no new project, nothing queued: the scan that
    follows classifies it). If several match, the most recently missed wins. Returns the
    matched row (as it was before the move), or ``None``."""
    chain = history_mirror.chain_bytes(settings.archive_root / cand.rel_path / HISTORY_DIR)
    if chain is None:
        return None
    matches = [
        p
        for p in gone
        if history_mirror.chain_bytes(history_mirror.mirror_dir(settings, p.rel_path)) == chain
    ]
    if not matches:
        return None
    stamp = to_iso(now)
    matches.sort(key=lambda p: p.missing_since or stamp, reverse=True)
    if len(matches) > 1:
        log.warning(
            "%s matches the history of %d vanished projects (%s); taking %s",
            cand.rel_path,
            len(matches),
            ", ".join(p.rel_path for p in matches),
            matches[0].rel_path,
        )
    project = matches[0]
    restore = (
        _state_to_restore(project)
        if project.state is ProjectState.MISSING
        else (project.state if project.state not in _TRANSIENT else _resting_state(project))
    )
    with db.transaction():
        db.cancel_queued_jobs(project.id, now)
        db.rename_project(project.id, cand.rel_path, cand.name)
        db.update_project_fields(
            project.id, state=restore, error=None, missing_since=None, state_before_missing=None
        )
    try:
        history_mirror.move_mirror(settings, project.rel_path, cand.rel_path)
    except OSError as exc:  # the next scan mirrors the history again at the new path
        log.warning("history mirror of %s not moved: %s", project.rel_path, exc)
    log.warning("project moved: %s → %s", project.rel_path, cand.rel_path)
    return project


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
    if project.state is not ProjectState.HASHING:  # a job may be writing the history right now
        _mirror_if_needed(settings, project, proj_root, summary)
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


def _mirror_if_needed(
    settings: Settings, project: ProjectRow, proj_root: Path, summary: ScanSummary
) -> None:
    """D59: mirror a history whose chain the mirror does not have yet (e.g. written by another
    tool, or before the mirror existed). A failure is reported, never fatal to the scan."""
    mirror = history_mirror.mirror_dir(settings, project.rel_path)
    try:
        if history_mirror.needs_sync(proj_root, mirror):
            history_mirror.sync_mirror(proj_root, mirror)
    except OSError as exc:
        msg = f"history mirror not updated: {exc}"
        log.warning("%s: %s", project.rel_path, msg)
        summary.errors.append(f"{project.rel_path}: {msg}")


# --- maintenance: periodic verification (D23) and the root manifest (D29) ----------------------


def _verify_reference_time(project: ProjectRow) -> datetime:
    """Last proof of integrity: last verification, else last seal, else first sight."""
    return from_iso(project.last_verified_at or project.last_sealed_at or project.first_seen)


def verify_daily_cap(sealed_count: int, interval_days: int) -> int:
    """D23 staggering: ``ceil(sealed / interval)`` verifications a day cover the archive once
    per interval without reading everything on the same night."""
    return math.ceil(sealed_count / interval_days) if sealed_count else 0


def schedule_verifications(
    db: Database, settings: Settings, *, now: datetime, working: bool
) -> list[str]:
    """Enqueue ``verify`` (priority 10) for sealed projects whose last verification (or seal)
    is older than ``verify_interval_days``, oldest first, at most :func:`verify_daily_cap` per
    local day (counter in ``settings_kv``). Never during working hours (D33). Returns the
    rel_paths enqueued."""
    if working:
        return []
    sealed = [p for p in db.list_projects(ProjectState.SEALED) if _is_sealed(p)]
    cap = verify_daily_cap(len(sealed), settings.verify_interval_days)
    day = now.astimezone(settings.tzinfo).date().isoformat()
    count = int(db.get_kv(VERIFY_DAY_COUNT_KEY) or 0) if db.get_kv(VERIFY_DAY_KEY) == day else 0
    limit = now - timedelta(days=settings.verify_interval_days)
    due = sorted(
        (
            p
            for p in sealed
            if _verify_reference_time(p) <= limit and not db.has_open_job(p.id, JobKind.VERIFY)
        ),
        key=lambda p: (_verify_reference_time(p), p.rel_path),
    )
    enqueued: list[str] = []
    for project in due[: max(0, cap - count)]:
        enqueue(db, project.id, JobKind.VERIFY, Trigger.AUTO, now)
        enqueued.append(project.rel_path)
        count += 1
    db.set_kv(VERIFY_DAY_KEY, day)
    db.set_kv(VERIFY_DAY_COUNT_KEY, str(count))
    if enqueued:
        log.info("verification scheduled for %d project(s) (cap %d/day)", len(enqueued), cap)
    return enqueued


def schedule_root_manifest(db: Database, settings: Settings, *, now: datetime) -> bool:
    """Enqueue the ``root_manifest`` job (priority 5, the lowest: it runs after the night's
    seals and verifications) when :func:`rootmanifest.root_manifest_needed`."""
    if db.has_open_job(None, JobKind.ROOT_MANIFEST):
        return False
    if not rootmanifest.root_manifest_needed(db, settings):
        return False
    db.enqueue_job(JobKind.ROOT_MANIFEST, None, Trigger.AUTO, PRIORITY[JobKind.ROOT_MANIFEST], now)
    return True


def schedule_maintenance(
    db: Database, settings: Settings, *, now: datetime, working: bool
) -> list[tuple[str, JobKind]]:
    """Called at the end of a scan cycle: verifications (outside working hours only) and the
    root manifest. Returns what was enqueued (``"."`` stands for the archive root)."""
    out = [
        (rel, JobKind.VERIFY)
        for rel in schedule_verifications(db, settings, now=now, working=working)
    ]
    if schedule_root_manifest(db, settings, now=now):
        out.append((".", JobKind.ROOT_MANIFEST))
    return out


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


_CANCELLABLE = frozenset({JobKind.SEAL, JobKind.ACCEPT_NEW_VERSION, JobKind.VERIFY})
_STATE_AFTER_CANCEL = {
    JobKind.SEAL: ProjectState.UNSEALED,
    JobKind.ACCEPT_NEW_VERSION: ProjectState.NEEDS_REVIEW,
    JobKind.VERIFY: ProjectState.SEALED,  # D63: a cancelled Verify now writes no generation
}


def is_cancellable(job: JobRow) -> bool:
    """Only the owner's own requests (Seal, Accept, Verify now): automatic jobs would be
    enqueued again next round, and an append or a scheduled verification is the app's own
    maintenance (D53, D57, D63)."""
    return job.trigger is Trigger.MANUAL and job.kind in _CANCELLABLE


def cancellable_job(db: Database, project: ProjectRow) -> JobRow | None:
    """The manual job a Cancel button can withdraw: a ``queued`` project whose ``seal``,
    ``accept_new_version`` or ``verify`` (Verify now, D63) waits (D53), or a ``hashing`` project
    whose manual job is running, also while paused by the working hours (D57; the supervisor
    aborts it)."""
    if project.state is ProjectState.QUEUED:
        job = db.queued_job(project.id)
    elif project.state is ProjectState.HASHING:
        job = db.running_job(project.id)
    else:
        return None
    if job is None or not is_cancellable(job):
        return None
    return job


def request_cancel(db: Database, project_id: int, now: datetime) -> int:
    """Cancel button (D53): withdraw a manual Seal/Accept that has not started (or was stopped by
    the working hours and sits in the queue again). The project goes back to the state it had
    before the request; hashes already checkpointed (D28) are kept for a later Seal. A running
    job is only the supervisor's to abort (``Supervisor.request_cancel``, D57): touching the DB
    here would leave the hasher thread writing over a cancelled job."""
    project = _require(db, project_id)
    job = cancellable_job(db, project)
    if job is None:
        raise SealerError(f"{project.rel_path}: nothing to cancel in state {project.state}")
    if job.state is not JobState.QUEUED:
        raise SealerError(f"{project.rel_path}: cancel a running job through the supervisor")
    db.set_job_state(job.id, JobState.CANCELLED, now)
    db.log(job.id, "info", "cancelled from the GUI before it ran", now)
    db.set_state(project_id, _STATE_AFTER_CANCEL[job.kind], review_reason=project.review_reason)
    log.info("%s cancelled for %s (job %d)", job.kind, project.rel_path, job.id)
    return job.id


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


def request_verify_now(db: Database, project_id: int, now: datetime) -> int:
    """Verify now (D63): a manual ``verify`` (priority manual +100) that also runs inside working
    hours (``bypass_hours``: the supervisor runs it with the gate closed and never pauses it).
    Only for ``sealed`` projects; the project shows ``queued`` until it starts, and Cancel
    withdraws it (back to ``sealed``). Returns the job id; a verification the scheduler had
    already queued is promoted instead of duplicated."""
    project = _require(db, project_id)
    if project.state is not ProjectState.SEALED or not _is_sealed(project):
        raise SealerError(f"{project.rel_path}: Verify now needs state sealed, not {project.state}")
    priority = PRIORITY[JobKind.VERIFY] + MANUAL_BONUS
    with db.transaction():
        job_id = db.enqueue_job(
            JobKind.VERIFY, project_id, Trigger.MANUAL, priority, now, bypass_hours=True
        )
        db.set_state(project_id, ProjectState.QUEUED, review_reason=project.review_reason)
    log.info("verify now requested for %s (job %d)", project.rel_path, job_id)
    return job_id


def request_retry(db: Database, settings: Settings, project_id: int, now: datetime) -> bool:
    """Retry on a ``missing`` project (D61): one ``is_dir()`` on its folder now (also in working
    hours: a stat costs nothing). Found → the reappearance of D58 (previous state, automatic
    verification at the next idle window if the app wrote its history) and True. Not found →
    False and nothing changes."""
    project = _require(db, project_id)
    if project.state is not ProjectState.MISSING:
        raise SealerError(f"{project.rel_path}: only a missing project can be retried")
    try:
        found = (settings.archive_root / project.rel_path).is_dir()
    except OSError:
        found = False
    if not found:
        log.info("retry: %s is still missing", project.rel_path)
        return False
    log.warning("project back on disk: %s", project.rel_path)
    _restore_from_missing(db, project, now)
    return True


RETIRE_LOG_TEMPLATE = "retired {rel_path} (history mirror deleted)"


def request_retire(db: Database, settings: Settings, project_id: int, now: datetime) -> str:
    """Retire a ``missing`` project (D60): delete its history mirror, then its row (cascade:
    files, sealed files, cached hashes, scans, review items, verify results, jobs). The only
    trace kept is a ``retire`` job (no project, ``done``, manual) with one log line; the root
    manifest is marked stale so the next one drops the reference. Returns the project's name."""
    project = _require(db, project_id)
    if project.state is not ProjectState.MISSING:
        raise SealerError("only a missing project can be retired")
    if db.running_job(project.id) is not None:  # the hasher still holds it; it stops shortly
        raise SealerError(f"{project.rel_path}: a job is still stopping; retire it in a moment")
    try:
        history_mirror.remove_mirror(settings, project.rel_path)
    except OSError as exc:
        raise SealerError(f"{project.rel_path}: history mirror not deleted: {exc}") from exc
    with db.transaction():
        job_id = db.record_finished_job(JobKind.RETIRE, None, Trigger.MANUAL, JobState.DONE, now)
        db.log(job_id, "info", RETIRE_LOG_TEMPLATE.format(rel_path=project.rel_path), now)
        db.delete_project(project.id)
        db.set_kv(ROOT_MANIFEST_STALE_KEY, "1")
    log.warning("project retired: %s (job %d)", project.rel_path, job_id)
    return project.name


def recover_after_restart(db: Database) -> int:
    """Jobs left ``running`` go back to ``queued``, and so do their ``hashing`` projects."""
    count = db.requeue_running_jobs()
    for project in db.list_projects(ProjectState.HASHING):
        db.set_state(project.id, ProjectState.QUEUED)
    return count


@dataclass(slots=True)
class FsRecovery:
    """What :func:`recover_history_dirs` cleaned up (archive-relative POSIX paths)."""

    temp_files: list[str] = field(default_factory=list)  # stale ``.<name>.tmp`` removed
    orphans: list[str] = field(default_factory=list)  # manifests renamed to ``*.orphan``
    errors: list[str] = field(default_factory=list)


def remove_stale_temp_files(folder: Path) -> list[Path]:
    """Delete the ``ascmhl/.<name>.tmp`` files that ``mhlwriter._commit`` leaves behind when
    the process is killed (SIGKILL, ``docker rm -f``) before its ``finally`` runs. Only call it
    while no job is writing (at startup, before the hasher runs). Returns the removed paths."""
    asc_dir = folder / HISTORY_DIR
    if not asc_dir.is_dir():
        return []
    removed: list[Path] = []
    for entry in sorted(asc_dir.iterdir()):
        if entry.name.startswith(".") and entry.name.endswith(".tmp") and entry.is_file():
            entry.unlink(missing_ok=True)
            log.warning("stale temp file %s removed (interrupted write)", entry.name)
            removed.append(entry)
    return removed


def recover_history_dirs(db: Database, settings: Settings) -> FsRecovery:
    """Startup recovery of the ``ascmhl/`` folders, before any job runs (abrupt recreation of
    the container, e.g. an auto-update): remove stale temp files and set orphan manifests
    aside (issue #1) in the archive root and in every tracked, non-ignored project that has an
    ``ascmhl/`` folder. A crash between the two renames of ``_commit`` therefore never reaches
    the next generation. Per-folder failures are collected, not raised."""
    root = settings.archive_root
    out = FsRecovery()
    folders: list[tuple[str, Path]] = [(".", root)]
    folders += [
        (p.rel_path, root / p.rel_path)
        for p in db.list_projects()
        if p.state not in (ProjectState.IGNORED, ProjectState.MISSING)
    ]
    for rel, folder in folders:
        prefix = "" if rel == "." else f"{rel}/"
        try:
            if not (folder / HISTORY_DIR).is_dir():
                continue
            for tmp in remove_stale_temp_files(folder):
                out.temp_files.append(f"{prefix}{HISTORY_DIR}/{tmp.name}")
            for orphan in quarantine_orphan_manifests(folder):
                out.orphans.append(f"{prefix}{HISTORY_DIR}/{orphan.name}")
        except Exception as exc:  # unreadable folder, broken chain: the scan will report it
            out.errors.append(f"{rel}: startup recovery failed: {type(exc).__name__}: {exc}")
    return out


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


class _StopOrCancel:
    """What the hasher sees as ``stop``: SIGTERM (requeue) or the Cancel button (D57)."""

    __slots__ = ("_cancel", "_stop")

    def __init__(self, stop: StopFlag, cancel: StopFlag | None) -> None:
        self._stop = stop
        self._cancel = cancel

    def is_set(self) -> bool:
        return self._stop.is_set() or (self._cancel is not None and self._cancel.is_set())


@dataclass(slots=True)
class _Ctx:
    db: Database
    settings: Settings
    job: JobRow
    project: ProjectRow
    root: Path
    gate: Gate
    stop: StopFlag
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
    stop: StopFlag,
    now_fn: Callable[[], datetime] = utcnow,
    on_progress: ProgressFn | None = None,
    cancel: StopFlag | None = None,
) -> None:
    """Run one queued job to completion, review, requeue (``Stopped``) or failure.

    ``cancel`` (the Cancel button, D57) aborts a manual ``seal``/``accept_new_version`` or a
    Verify now (D63) at the next file like ``stop`` does, but the job ends ``cancelled`` and the
    project goes back to the state it had before the request; it is ignored for any other job.
    If both are set, cancel wins. Either way nothing is written: the generation is only written
    after the last file.

    ``on_progress(files_done, files_total, bytes_done, bytes_total)`` is called after each
    progress write to the DB (the supervisor turns it into ``job.progress`` events).
    """
    if job.kind is JobKind.ROOT_MANIFEST:
        _run_root_manifest(db, settings, job, now_fn)
        return
    if job.project_id is None:
        db.set_job_state(job.id, JobState.FAILED, now_fn(), error=f"{job.kind}: no project")
        return
    project = db.get_project(job.project_id)
    if project is None:
        db.set_job_state(job.id, JobState.FAILED, now_fn(), error="project not found")
        return
    if project.state is ProjectState.MISSING:  # D58: its queued jobs were cancelled; a stray one
        db.log(job.id, "info", "skipped: project folder is missing", now_fn())
        db.set_job_state(job.id, JobState.CANCELLED, now_fn())
        return
    verify = job.kind is JobKind.VERIFY
    if verify and not (
        _is_sealed(project)
        and project.state in (ProjectState.SEALED, ProjectState.QUEUED, ProjectState.HASHING)
    ):
        # The project left ``sealed`` while the verification waited (new files, review...).
        db.log(job.id, "info", f"verify skipped: project is {project.state}", now_fn())
        db.set_job_state(job.id, JobState.CANCELLED, now_fn())
        return
    if not is_cancellable(job):
        cancel = None
    ctx = _Ctx(
        db,
        settings,
        job,
        project,
        settings.archive_root / project.rel_path,
        gate,
        _StopOrCancel(stop, cancel),
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
        elif verify:
            _run_verify(ctx)
        else:
            _run_seal(ctx, accept=job.kind is JobKind.ACCEPT_NEW_VERSION)
    except Stopped:
        if _went_missing(db, project.id):  # D58: the scan cycle took the project meanwhile
            ctx.finish(JobState.CANCELLED)
            ctx.log("info", "stopped; the project folder is missing")
            return
        if cancel is not None and cancel.is_set():  # D57: the owner took the request back
            ctx.finish(JobState.CANCELLED)
            db.set_state(
                project.id, _STATE_AFTER_CANCEL[job.kind], review_reason=project.review_reason
            )
            ctx.log("info", "cancelled while reading; hashes already done are kept")
            return
        db.set_job_state(job.id, JobState.QUEUED, now_fn())
        if verify and job.trigger is not Trigger.MANUAL:  # nothing decided: still sealed
            db.set_state(project.id, ProjectState.SEALED)
            ctx.log("info", "stopped; back in the queue (a verification re-reads everything)")
        else:  # a Verify now (D63) waits as queued, so Cancel still finds it
            db.set_state(project.id, ProjectState.QUEUED, review_reason=project.review_reason)
            ctx.log("info", "stopped; back in the queue (hashes already done are kept)")
    except FileChanged as exc:
        _fail(ctx, f"file kept changing while being read: {exc}")
    except Exception as exc:
        _fail(ctx, f"{type(exc).__name__}: {exc}")


def _went_missing(db: Database, project_id: int) -> bool:
    current = db.get_project(project_id)
    return current is not None and current.state is ProjectState.MISSING


def _fail(ctx: _Ctx, msg: str) -> None:
    """The job failed. The project goes to ``error`` unless the scan cycle already found its
    folder gone (D58): ``missing`` says more than the read error it caused."""
    ctx.finish(JobState.FAILED, msg)
    if not _went_missing(ctx.db, ctx.project.id):
        ctx.db.set_state(
            ctx.project.id, ProjectState.ERROR, error=msg, review_reason=ctx.project.review_reason
        )
    ctx.log("error", msg)


def _scan_for_job(ctx: _Ctx, patterns: list[str]) -> list[FileStat]:
    outcome = scan_project(ctx.root, patterns, ctx.settings.exclude_globs)
    if outcome.errors:
        raise OSError("scan errors: " + _list_paths(outcome.errors))
    return outcome.files


class _FreshReadCache(_DbHashCache):
    """Never hits: a verification must read every byte (D8, D23). Still records the digests,
    so an Accept right after a failed verification does not read the project again."""

    def get(self, rel_path: str, size: int, mtime_ns: int) -> dict[str, str]:
        del rel_path, size, mtime_ns
        return {}


def _hash(
    ctx: _Ctx,
    files: list[FileStat],
    expected: Mapping[str, Mapping[str, str]],
    *,
    fresh: bool = False,
) -> dict[str, dict[str, str]]:
    def formats_for(rel_path: str) -> Collection[str]:
        legacy = set(expected.get(rel_path, {})) & _SUPPORTED_LEGACY_FORMATS
        return {PRIMARY_HASH_FORMAT, *legacy}

    def progress(files_done: int, files_total: int, bytes_done: int, bytes_total: int) -> None:
        ctx.db.update_job_progress(ctx.job.id, files_done, files_total, bytes_done, bytes_total)
        if ctx.on_progress is not None:
            ctx.on_progress(files_done, files_total, bytes_done, bytes_total)
        if ctx.stop.is_set():
            # At every file boundary, also when the next files come from the cache (D28) and the
            # hasher would not read a byte: Cancel (D57) and SIGTERM stop before anything is
            # written, at the latest at the next file.
            raise Stopped

    progress(0, len(files), 0, sum(f.size for f in files))
    cache_cls = _FreshReadCache if fresh else _DbHashCache
    digests = hash_project(
        ctx.root,
        files,
        formats_for,
        cache_cls(ctx.db, ctx.project.id, ctx.now_fn),
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


# A job that sends its project to review still ends ``done``; this log line is how the GUI's
# activity log tells it apart from a real seal (``web.views``). Keep the two in sync.
REVIEW_LOG_PREFIX = "needs review, nothing written: "


def _to_review(ctx: _Ctx, reason: str, items: list[ReviewItem]) -> None:
    ctx.db.replace_review_items(ctx.project.id, items)
    ctx.db.set_state(ctx.project.id, ProjectState.NEEDS_REVIEW, review_reason=reason)
    ctx.finish(JobState.DONE)
    ctx.log("warning", f"{REVIEW_LOG_PREFIX}{reason}")


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
    ctx: _Ctx,
    manifest: Path,
    sealed: Iterable[SealedFile],
    files: list[FileStat],
    *,
    verified: bool = False,
) -> None:
    """``verified``: a verify generation stamps ``last_verified_at``, not ``last_sealed_at``."""
    now = ctx.now_fn()
    db, pid = ctx.db, ctx.project.id
    db.replace_sealed_files(pid, sealed)
    db.update_project_fields(
        pid,
        last_generation_no=_generation_no(manifest),
        file_count=len(files),
        total_bytes=sum(f.size for f in files),
        **{"last_verified_at" if verified else "last_sealed_at": now},
    )
    db.clear_review_items(pid)
    db.set_state(pid, ProjectState.SEALED)
    db.prune_hashes(pid, files)
    db.set_kv(ROOT_MANIFEST_STALE_KEY, "1")
    ctx.finish(JobState.DONE)
    ctx.log("info", f"wrote {manifest.name}")
    _mirror_after_write(ctx)


def _mirror_after_write(ctx: _Ctx) -> None:
    """D59: copy the new generation into the history mirror. The generation is already on the
    archive, so a failure here is only a warning; the next scan retries (the chains differ)."""
    try:
        history_mirror.sync_mirror(
            ctx.root, history_mirror.mirror_dir(ctx.settings, ctx.project.rel_path)
        )
    except OSError as exc:
        ctx.log("warning", f"history mirror not updated: {exc}")


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
    if problems and accept:
        # D65: Accept takes the folder as it is now. A file the legacy MHL 1.x lists and is gone
        # is only a warning; a file that no longer matches keeps our xxh128 but does not
        # inherit an origin hash that would be false.
        digests = _accept_legacy_problems(ctx, problems, digests)
    elif problems:
        reason = f"{len(problems)} files do not match their legacy MHL 1.x: " + _list_paths(
            i.rel_path for i in problems
        )
        _to_review(ctx, reason, problems)
        return
    if accept:
        retired = retire_history(ctx.root, ctx.now_fn())
        if retired is not None:
            ctx.log("info", f"previous history moved to {retired.relative_to(ctx.root).as_posix()}")
            try:  # D59: the mirror keeps the same layout as the project folder
                history_mirror.supersede_mirror(ctx.settings, ctx.project.rel_path, retired.name)
            except OSError as exc:
                ctx.log("warning", f"history mirror not set aside: {exc}")
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


def _accept_legacy_problems(
    ctx: _Ctx, problems: list[ReviewItem], digests: Mapping[str, Mapping[str, str]]
) -> dict[str, dict[str, str]]:
    """D65: log what Accept overrides and drop the legacy formats of files that no longer
    match, so the manifest never records an origin hash as ``verified`` when it failed."""
    out = {rel: dict(hashes) for rel, hashes in digests.items()}
    deleted = [i.rel_path for i in problems if i.change is ChangeKind.DELETED]
    modified = [i.rel_path for i in problems if i.change is ChangeKind.MODIFIED]
    if deleted:
        ctx.log(
            "warning",
            f"accepted: {len(deleted)} files listed in a legacy MHL 1.x are gone: "
            + _list_paths(deleted),
        )
    if modified:
        ctx.log(
            "warning",
            f"accepted: {len(modified)} files no longer match their legacy MHL 1.x, sealed "
            "without the origin hash: " + _list_paths(modified),
        )
        for rel in modified:
            out[rel] = {
                fmt: d for fmt, d in out[rel].items() if fmt not in _SUPPORTED_LEGACY_FORMATS
            }
    return out


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
    # D65: only the new files answer to the legacy MHL 1.x; what was sealed already did (or
    # was accepted without it), and files gone since the seal are caught by the diff above.
    added = {f.rel_path for f in diff.added}
    expected = {rel: h for rel, h in expected.items() if rel in added}
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


# --- verify (D23, D8) and root manifest (D29) jobs -------------------------------------------


def compare_for_verify(
    sealed: Mapping[str, SealedFile],
    present: Mapping[str, FileStat],
    digests: Mapping[str, Mapping[str, str]],
) -> list[VerifyResult]:
    """Fresh read vs ``sealed_files`` (vault note `Distinguir corrupción de modificación por
    mtime`): same hash → ``ok``; different hash with the same size and mtime (±1 s) →
    ``corrupt`` (nobody wrote the file: the bytes changed underneath); different hash and a
    different size or mtime → ``modified``; recorded but gone → ``missing``; not recorded →
    ``added`` (fine, D9)."""
    out: list[VerifyResult] = []
    for rel in sorted(set(sealed) | set(present)):
        old, cur = sealed.get(rel), present.get(rel)
        if cur is None:
            assert old is not None
            out.append(VerifyResult(rel, old.xxh128, None, "missing"))
            continue
        actual = digests.get(rel, {}).get(PRIMARY_HASH_FORMAT)
        if old is None:
            out.append(VerifyResult(rel, None, actual, "added"))
        elif old.xxh128 is None or old.xxh128 == actual:
            out.append(VerifyResult(rel, old.xxh128, actual, "ok"))
        elif old.size == cur.size and abs(old.mtime_ns - cur.mtime_ns) <= MTIME_TOLERANCE_NS:
            out.append(VerifyResult(rel, old.xxh128, actual, "corrupt"))
        else:
            out.append(VerifyResult(rel, old.xxh128, actual, "modified"))
    return out


VERIFY_PROBLEMS = ("corrupt", "modified", "missing")


def _verify_reason(problems: list[VerifyResult]) -> str:
    counts = [
        f"{sum(r.status == s for r in problems)} {s}"
        for s in VERIFY_PROBLEMS
        if any(r.status == s for r in problems)
    ]
    paths = _list_paths(r.rel_path for r in problems)
    return f"{VERIFICATION_PREFIX} {', '.join(counts)}; files: {paths}"


def _run_verify(ctx: _Ctx) -> None:
    """Re-read every file, compare with ``sealed_files``; all fine → a full generation where
    the library marks every known file ``verified`` and new ones ``original`` (spec §5.6: a
    verification appends a generation); any problem → review, nothing written."""
    patterns = scan_ignore_patterns(ctx.root, ctx.settings)
    files = _scan_for_job(ctx, patterns)
    sealed_rows = ctx.db.get_sealed_files(ctx.project.id)
    present = {f.rel_path: f for f in files}
    digests = _hash(ctx, files, {}, fresh=True)
    results = compare_for_verify(sealed_rows, present, digests)
    ctx.db.replace_verify_results(ctx.job.id, ctx.project.id, results)
    problems = [r for r in results if r.status in VERIFY_PROBLEMS]
    if problems:
        items = []
        for r in problems:
            old, cur = sealed_rows[r.rel_path], present.get(r.rel_path)
            change = ChangeKind.DELETED if cur is None else ChangeKind.MODIFIED
            items.append(
                ReviewItem(
                    r.rel_path,
                    change,
                    old.size,
                    None if cur is None else cur.size,
                    old.mtime_ns,
                    None if cur is None else cur.mtime_ns,
                )
            )
        _to_review(ctx, _verify_reason(problems), items)
        return
    try:
        manifest = write_project_generation(ctx.root, digests, patterns, __version__)
    except MHLReviewError as exc:  # sealed_files and the history disagree
        _to_review(ctx, f"{VERIFICATION_PREFIX} {exc}", [])
        return
    sealed = [
        SealedFile(f.rel_path, f.size, f.mtime_ns, digests[f.rel_path][PRIMARY_HASH_FORMAT])
        for f in files
    ]
    added = sum(r.status == "added" for r in results)
    ctx.log(
        "info",
        f"verified {len(files) - added} files" + (f", {added} new" if added else ""),
    )
    _after_generation(ctx, manifest, sealed, files, verified=True)


def _run_root_manifest(
    db: Database, settings: Settings, job: JobRow, now_fn: Callable[[], datetime]
) -> None:
    db.set_job_state(job.id, JobState.RUNNING, now_fn())
    try:
        if not settings.archive_root.is_dir():
            raise ArchiveUnavailableError(f"archive root {settings.archive_root} is missing")
        for orphan in quarantine_orphan_manifests(settings.archive_root):
            db.log(job.id, "warning", f"orphan root manifest renamed to {orphan.name}", now_fn())
        manifest = rootmanifest.refresh_root_manifest(db, settings, now=now_fn())
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        db.log(job.id, "error", msg, now_fn())
        db.set_job_state(job.id, JobState.FAILED, now_fn(), error=msg)
        log.error("root manifest job %d failed: %s", job.id, msg)
        return
    db.log(
        job.id,
        "info",
        f"wrote {manifest.name}" if manifest is not None else "root manifest up to date",
        now_fn(),
    )
    db.set_job_state(job.id, JobState.DONE, now_fn())
