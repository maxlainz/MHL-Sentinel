"""Presentation helpers: traffic lights, production wording (D10), local dates, sizes.

Pure functions over DB rows; no request, no template. Dates are stored in UTC and shown in
``settings.timezone`` (docs/arquitectura.md, "Hito 3").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mhl_sentinel.clock import from_iso
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, JobRow, ProjectRow
from mhl_sentinel.models import ChangeKind, JobKind, JobState, ProjectState

GREEN, AMBER, RED, GREY = "green", "amber", "red", "grey"

LIGHT: dict[ProjectState, str] = {
    ProjectState.SEALED: GREEN,
    ProjectState.UNSEALED: AMBER,
    ProjectState.CHANGED: AMBER,
    ProjectState.QUEUED: AMBER,
    ProjectState.HASHING: AMBER,
    ProjectState.NEEDS_REVIEW: RED,
    ProjectState.ERROR: RED,
    ProjectState.IGNORED: GREY,
}
_SEVERITY = {RED: 0, AMBER: 1, GREEN: 2, GREY: 3}

STATE_LABEL: dict[ProjectState, str] = {
    ProjectState.SEALED: "sealed",
    ProjectState.UNSEALED: "no manifest",
    ProjectState.CHANGED: "new files",
    ProjectState.QUEUED: "queued",
    ProjectState.HASHING: "reading files",
    ProjectState.NEEDS_REVIEW: "needs review",
    ProjectState.ERROR: "problem",
    ProjectState.IGNORED: "ignored",
}

JOB_LABEL: dict[JobKind, str] = {
    JobKind.SEAL: "Sealing",
    JobKind.APPEND: "Adding new files to",
    JobKind.ACCEPT_NEW_VERSION: "Sealing a new version of",
    JobKind.VERIFY: "Verifying",
    JobKind.ROOT_MANIFEST: "Updating the archive manifest",
}

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(n: int | None) -> str:
    """Decimal units, as file browsers and NAS panels show them (``28.4 GB``)."""
    if n is None:
        return "-"
    value = float(n)
    for unit in _UNITS:
        if abs(value) < 1000 or unit == _UNITS[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{n} B"  # pragma: no cover


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return from_iso(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def local(value: datetime | str | None, tz: ZoneInfo) -> datetime | None:
    dt = _as_utc(value)
    return None if dt is None else dt.astimezone(tz)


def fmt_date(value: datetime | str | None, tz: ZoneInfo) -> str:
    dt = local(value, tz)
    return "" if dt is None else dt.strftime("%Y-%m-%d")


def fmt_datetime(value: datetime | str | None, tz: ZoneInfo) -> str:
    dt = local(value, tz)
    return "" if dt is None else dt.strftime("%Y-%m-%d %H:%M")


def fmt_when(value: datetime | str | None, tz: ZoneInfo, now: datetime) -> str:
    """``14:02`` today, ``Mon 09:00`` within a week, else ``2026-10-01 09:00``."""
    dt = local(value, tz)
    if dt is None:
        return ""
    today = now.astimezone(tz).date()
    delta = (dt.date() - today).days
    if delta == 0:
        return dt.strftime("%H:%M")
    if -6 <= delta <= 6:
        return dt.strftime("%a %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_mtime_ns(value: int | None, tz: ZoneInfo) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value / 1e9, UTC).astimezone(tz).strftime("%Y-%m-%d")


def percent(job: JobRow | None) -> int | None:
    if job is None:
        return None
    if job.bytes_total > 0:
        return min(100, int(job.bytes_done * 100 / job.bytes_total))
    if job.files_total > 0:
        return min(100, int(job.files_done * 100 / job.files_total))
    return 0


def running_job_for(db: Database, project_id: int, current: JobRow | None) -> JobRow | None:
    """The running (or else queued) job of a project; the supervisor's view first."""
    if current is not None and current.project_id == project_id:
        fresh = db.get_job(current.id)
        return fresh or current
    queued: JobRow | None = None
    for job in db.list_jobs(limit=200):
        if job.project_id != project_id:
            continue
        if job.state is JobState.RUNNING:
            return job
        if job.state is JobState.QUEUED and queued is None:
            queued = job
    return queued


@dataclass(slots=True)
class ReviewRow:
    change: str
    rel_path: str
    detail: str


def review_rows(db: Database, project: ProjectRow, tz: ZoneInfo) -> list[ReviewRow]:
    """Sketch D46: modified and deleted files (review items) plus added ones (fine)."""
    rows: list[ReviewRow] = []
    for item in db.get_review_items(project.id):
        detail = ""
        if item.change is ChangeKind.MODIFIED:
            parts = []
            if item.old_size != item.new_size:
                parts.append(f"size {human_size(item.old_size)} → {human_size(item.new_size)}")
            when = fmt_mtime_ns(item.new_mtime_ns, tz)
            if when:
                parts.append(when)
            detail = ", ".join(parts)
        rows.append(ReviewRow(item.change.value, item.rel_path, detail))
    sealed = db.get_sealed_files(project.id)
    if sealed:
        listed = {r.rel_path for r in rows}
        for rel_path in sorted(set(db.get_files(project.id)) - set(sealed) - listed):
            rows.append(ReviewRow(ChangeKind.ADDED.value, rel_path, "added files are fine"))
    order = {ChangeKind.MODIFIED.value: 0, ChangeKind.DELETED.value: 1, ChangeKind.ADDED.value: 2}
    rows.sort(key=lambda r: (order.get(r.change, 3), r.rel_path))
    return rows


VERIFICATION_PREFIX = "verification:"  # sealer.VERIFICATION_PREFIX


def is_verification_review(project: ProjectRow) -> bool:
    return project.state is ProjectState.NEEDS_REVIEW and (project.review_reason or "").startswith(
        VERIFICATION_PREFIX
    )


def review_summary(db: Database, project: ProjectRow) -> str:
    """``2 modified, 1 deleted`` from the review items; else the stored reason's head.

    A failed periodic verification shows its own counts (``verification: 1 corrupt``)."""
    if is_verification_review(project):
        return (project.review_reason or "").split(";", 1)[0]
    counts = Counter(i.change for i in db.get_review_items(project.id))
    parts = [
        f"{counts[kind]} {kind.value}"
        for kind in (ChangeKind.MODIFIED, ChangeKind.DELETED)
        if counts[kind]
    ]
    if parts:
        return ", ".join(parts) + " since the last seal"
    if project.review_reason:
        return project.review_reason.split(":", 1)[0]
    return "something changed since the last seal"


def status_text(
    db: Database,
    project: ProjectRow,
    settings: Settings,
    job: JobRow | None,
) -> str:
    """One line for production (D10, D11): no hashes, no absolute paths."""
    tz = settings.tzinfo
    state = project.state
    if state is ProjectState.SEALED:
        text = f"sealed {fmt_date(project.last_sealed_at, tz)}".strip()
        if project.last_verified_at:
            text += f" · verified {fmt_date(project.last_verified_at, tz)}"
        return text
    if state is ProjectState.NEEDS_REVIEW:
        return "review: " + review_summary(db, project)
    if state is ProjectState.UNSEALED:
        if project.preexisting:
            return "no manifest yet · press Seal to create it"
        return f"no manifest yet · seals itself after {settings.settle_hours} h without changes"
    if state is ProjectState.CHANGED:
        return (
            "new files since the last seal · added to the manifest after "
            f"{settings.settle_hours} h without changes"
        )
    if state is ProjectState.QUEUED:
        return "waiting for the next idle window"
    if state is ProjectState.HASHING:
        pct = percent(job)
        return "reading files" + (f" · {pct}%" if pct is not None else "")
    if state is ProjectState.IGNORED:
        return "ignored"
    return "problem: " + (project.error or "unknown error") + " · retried on the next round"


_VERIFY_ORDER = {"corrupt": 0, "modified": 1, "missing": 2, "added": 3, "ok": 4}
VERIFY_LABEL = {
    "corrupt": "corrupt (changed without being saved)",
    "modified": "modified",
    "missing": "missing",
    "added": "new file (fine)",
    "ok": "ok",
}


@dataclass(slots=True)
class VerifyRow:
    status: str
    label: str
    rel_path: str


@dataclass(slots=True)
class VerifyView:
    rows: list[VerifyRow] = field(default_factory=list)  # everything except ``ok``
    ok: int = 0
    when: str = ""


def verify_view(db: Database, project: ProjectRow, tz: ZoneInfo) -> VerifyView:
    """Latest verification of a project: problem rows first; ``ok`` files only counted."""
    results = db.get_verify_results(project.id)
    last = next(
        (
            j
            for j in db.list_jobs(limit=200)
            if j.project_id == project.id and j.kind is JobKind.VERIFY and j.finished_at
        ),
        None,
    )
    view = VerifyView(when=fmt_datetime(last.finished_at if last else None, tz))
    for r in sorted(results, key=lambda r: (_VERIFY_ORDER.get(r.status, 5), r.rel_path)):
        if r.status == "ok":
            view.ok += 1
        else:
            view.rows.append(VerifyRow(r.status, VERIFY_LABEL.get(r.status, r.status), r.rel_path))
    return view


@dataclass(slots=True)
class ProjectView:
    id: int
    name: str
    folder: str  # parent folders relative to the archive root, may be empty
    state: str
    label: str
    light: str
    text: str


def project_view(
    db: Database, project: ProjectRow, settings: Settings, current: JobRow | None
) -> ProjectView:
    job = current if current is not None and current.project_id == project.id else None
    parent = project.rel_path.rsplit("/", 1)[0] if "/" in project.rel_path else ""
    return ProjectView(
        id=project.id,
        name=project.name,
        folder=parent,
        state=project.state.value,
        label=STATE_LABEL[project.state],
        light=LIGHT[project.state],
        text=status_text(db, project, settings, job),
    )


def sorted_projects(projects: list[ProjectRow]) -> list[ProjectRow]:
    """Red first, then amber, green, grey; by path within each colour."""
    return sorted(projects, key=lambda p: (_SEVERITY[LIGHT[p.state]], p.rel_path))


@dataclass(slots=True)
class Counters:
    projects: int = 0
    needs_review: int = 0
    unsealed: int = 0
    queued: int = 0
    errors: int = 0


def counters(projects: list[ProjectRow], queued_jobs: int) -> Counters:
    c = Counters(queued=queued_jobs)
    for p in projects:
        if p.state is ProjectState.IGNORED:
            continue
        c.projects += 1
        if p.state is ProjectState.NEEDS_REVIEW:
            c.needs_review += 1
        elif p.state is ProjectState.UNSEALED:
            c.unsealed += 1
        elif p.state is ProjectState.ERROR:
            c.errors += 1
    return c


@dataclass(slots=True)
class GenerationView:
    number: int
    created: str
    files: int
    tool: str


@dataclass(slots=True)
class History:
    generations: list[GenerationView] = field(default_factory=list)
    error: str | None = None


def load_history(project_root: Path, tz: ZoneInfo) -> History:
    """Generations of ``<project>/ascmhl/`` through the reference (``MHLHistory``).

    Any exception (broken chain, unreadable share) becomes a readable message.
    """
    if not (project_root / "ascmhl").is_dir():
        return History()
    try:
        from ascmhl.history import MHLHistory

        history = MHLHistory.load_from_path(str(project_root))
        out: list[GenerationView] = []
        for hash_list in history.hash_lists:
            info = hash_list.creator_info
            created = fmt_datetime(getattr(info, "creation_date", None), tz)
            tool_info = getattr(info, "tool", None)
            tool = getattr(tool_info, "name", "") or ""
            files = sum(1 for mh in hash_list.media_hashes if not mh.is_directory)
            out.append(GenerationView(int(hash_list.generation_number), created, files, tool))
        return History(generations=out)
    except Exception as exc:
        return History(error=f"the manifest history could not be read ({type(exc).__name__})")
