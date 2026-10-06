"""Presentation helpers: traffic lights, production wording (D10), local dates, sizes.

Pure functions over DB rows; no request, no template. Dates are stored in UTC and shown in
``settings.timezone`` (docs/arquitectura.md, "Hito 3").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mhl_sentinel import sealer
from mhl_sentinel.clock import from_iso, utcnow
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, JobRow, ProjectRow
from mhl_sentinel.models import ChangeKind, JobKind, JobState, ProjectState, Trigger
from mhl_sentinel.schedule import WorkingHours

GREEN, AMBER, RED, GREY = "green", "amber", "red", "grey"

LIGHT: dict[ProjectState, str] = {
    ProjectState.SEALED: GREEN,
    ProjectState.UNSEALED: AMBER,
    ProjectState.CHANGED: AMBER,
    ProjectState.QUEUED: AMBER,
    ProjectState.HASHING: AMBER,
    ProjectState.NEEDS_REVIEW: RED,
    ProjectState.ERROR: RED,
    ProjectState.MISSING: RED,
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
    ProjectState.MISSING: "missing",
    ProjectState.IGNORED: "ignored",
}

JOB_LABEL: dict[JobKind, str] = {
    JobKind.SEAL: "Sealing",
    JobKind.APPEND: "Adding new files to",
    JobKind.ACCEPT_NEW_VERSION: "Sealing a new version of",
    JobKind.VERIFY: "Verifying",
    JobKind.ROOT_MANIFEST: "Updating the archive manifest",
    JobKind.RETIRE: "Forgetting",
}
VERIFY_NOW_LABEL = "Verify now"  # D63: a manual verification, also inside working hours
# D73, D74: a Seal, Accept or append (manual or automatic) started at once, inside working hours.
START_NOW_LABEL: dict[JobKind, str] = {
    JobKind.SEAL: "Seal now",
    JobKind.ACCEPT_NEW_VERSION: "Accept now",
    JobKind.APPEND: "Update now",
}
UPDATE_NOW_LABEL = START_NOW_LABEL[JobKind.APPEND]  # D74: also a changed project, any hour

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


def job_label(job: JobRow) -> str:
    """What a job is called in the header and the project card."""
    if job.kind is JobKind.VERIFY and job.trigger is Trigger.MANUAL:
        return VERIFY_NOW_LABEL
    return JOB_LABEL.get(job.kind, str(job.kind))


def start_now_label(db: Database, project: ProjectRow, settings: Settings, now: datetime) -> str:
    """``Seal now`` / ``Accept now`` / ``Update now`` (D73, D74) for a Seal, Accept or append
    still bound to the working hours, only while they are on; empty when there is no such
    button."""
    job = sealer.startable_now_job(db, project)
    if job is None or not WorkingHours.from_settings(settings).is_working(now):
        return ""
    return START_NOW_LABEL[job.kind]


def can_update_now(project: ProjectRow) -> bool:
    """Update now (D74): a ``changed`` project, inside or outside working hours."""
    return project.state is ProjectState.CHANGED and project.last_generation_no is not None


def unsealed_bytes(db: Database, project: ProjectRow) -> int:
    """What an append reads (D74): the files the last scan saw that the manifest lacks. From the
    DB, so the size in the dialog costs no access to the share."""
    return sum(f.size for f in sealer.unsealed_files(db, project.id))


def read_size(db: Database, project: ProjectRow) -> int | None:
    """Bytes a start-now or Update now reads: the new files for an append (or a ``changed``
    project), the whole project for a Seal or Accept."""
    job = sealer.startable_now_job(db, project)
    if can_update_now(project) or (job is not None and job.kind is JobKind.APPEND):
        return unsealed_bytes(db, project)
    return project.total_bytes


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


def _missing_since(project: ProjectRow, tz: ZoneInfo) -> str:
    return fmt_when(project.missing_since, tz, utcnow()) or "the last round"


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
        return "queued · runs after working hours"
    if state is ProjectState.HASHING:
        pct = percent(job)
        return "reading files" + (f" · {pct}%" if pct is not None else "")
    if state is ProjectState.IGNORED:
        return "ignored"
    if state is ProjectState.MISSING:
        return "missing: not on disk since " + _missing_since(project, tz)
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
    missing: int = 0


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
        elif p.state is ProjectState.MISSING:
            c.missing += 1
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


# --- inbox model (issue #5, proposal D) ------------------------------------------------------
# The main screen is an inbox: what needs a decision, what is not sealed yet, what is moving, and
# everything else folded into one calm "all quiet" line.


@dataclass(slots=True)
class InboxRow:
    view: ProjectView
    meta: str = ""  # "142 files · 28.4 GB"
    when: str = ""  # sealed date, for the quiet list
    cancellable: bool = False
    start_now: str = ""  # D73, D74: "Seal now" / "Accept now" / "Update now", or no button
    update_now: bool = False  # D74: Update now on a changed project, any hour
    size: int | None = None  # bytes, for the start-now / Update now confirmation

    @property
    def acts(self) -> bool:
        return self.cancellable or bool(self.start_now) or self.update_now


@dataclass(slots=True)
class Inbox:
    decide: list[InboxRow] = field(default_factory=list)  # needs review, problems, missing (red)
    unsealed: list[InboxRow] = field(default_factory=list)  # waiting for the owner's Seal
    moving: list[InboxRow] = field(default_factory=list)  # queued, reading, new files
    quiet: list[InboxRow] = field(default_factory=list)  # sealed
    ignored: list[InboxRow] = field(default_factory=list)
    next_verification: str = ""
    working: bool = False  # inside working hours now: the dialogs warn about the NAS load


INBOX_PREVIEW = 6  # unsealed rows shown before "show N more"


def _meta(project: ProjectRow, tz: ZoneInfo | None = None) -> str:
    parts = []
    if project.state is ProjectState.MISSING and tz is not None and project.last_sealed_at:
        parts.append(f"sealed {fmt_date(project.last_sealed_at, tz)}")
    if project.file_count is not None:
        parts.append(f"{project.file_count} file{'' if project.file_count == 1 else 's'}")
    if project.total_bytes is not None:
        parts.append(human_size(project.total_bytes))
    if project.state is ProjectState.MISSING and tz is not None and project.last_verified_at:
        parts.append(f"verified OK {fmt_date(project.last_verified_at, tz)}")
    return " · ".join(parts)


OVERDUE = "overdue"


def next_verification(
    projects: list[ProjectRow], settings: Settings, now: datetime | None = None
) -> str:
    """Earliest date a sealed project is due for its periodic re-read (D23): the oldest
    ``last_verified_at`` (else ``last_sealed_at``) plus the interval. An estimate: the scheduler
    staggers verifications over the nights, so one may run a little later."""
    dates = [
        d
        for p in projects
        if p.state is ProjectState.SEALED
        and (d := _as_utc(p.last_verified_at or p.last_sealed_at)) is not None
    ]
    if not dates:
        return ""
    due = min(dates) + timedelta(days=settings.verify_interval_days)
    if (
        now is not None
        and due.astimezone(settings.tzinfo).date() < now.astimezone(settings.tzinfo).date()
    ):
        # D23 staggers a few projects per night: a backlog can leave the oldest one overdue.
        return OVERDUE
    return fmt_date(due, settings.tzinfo)


def inbox(
    db: Database,
    projects: list[ProjectRow],
    settings: Settings,
    current: JobRow | None,
    now: datetime,
) -> Inbox:
    box = Inbox(next_verification=next_verification(projects, settings, now))
    box.working = WorkingHours.from_settings(settings).is_working(now)
    tz = settings.tzinfo
    for p in sorted_projects(projects):
        row = InboxRow(project_view(db, p, settings, current), _meta(p, tz))
        state = p.state
        if state in (ProjectState.NEEDS_REVIEW, ProjectState.ERROR, ProjectState.MISSING):
            box.decide.append(row)
        elif state is ProjectState.UNSEALED:
            box.unsealed.append(row)
        elif state is ProjectState.SEALED:
            row.when = fmt_date(p.last_sealed_at, tz)
            box.quiet.append(row)
        elif state is ProjectState.IGNORED:
            box.ignored.append(row)
        else:
            row.cancellable = sealer.cancellable_job(db, p) is not None
            # D73, D74: start-now also on an automatic job, which has no Cancel.
            row.start_now = start_now_label(db, p, settings, now)
            row.update_now = can_update_now(p)
            if row.start_now or row.update_now:
                row.size = read_size(db, p)
            box.moving.append(row)
    # What moves now goes on top: reading files, then queued, then new files settling.
    order = {ProjectState.HASHING.value: 0, ProjectState.QUEUED.value: 1}
    box.moving.sort(key=lambda r: (order.get(r.view.state, 2), r.view.name))
    return box


def detail_sentence(db: Database, project: ProjectRow, settings: Settings) -> str:
    """One plain sentence on top of the project page: where it stands and what is expected."""
    tz = settings.tzinfo
    state = project.state
    if state is ProjectState.SEALED:
        text = f"Sealed on {fmt_date(project.last_sealed_at, tz)}. Nothing has changed since."
        if project.last_verified_at:
            text += f" Last verified on {fmt_date(project.last_verified_at, tz)}."
        return text
    if state is ProjectState.NEEDS_REVIEW:
        if is_verification_review(project):
            found = review_summary(db, project).removeprefix("verification: ")
            return f"The periodic verification found problems: {found}. Decide what to do."
        found = review_summary(db, project).removesuffix(" since the last seal")
        return f"Files changed since the last seal ({found}). Is this the new final version?"
    if state is ProjectState.UNSEALED:
        text = "No manifest yet. Press Seal when the archiving of this project is finished."
        if not project.preexisting:
            hours = settings.settle_hours
            text += f" If nobody does, it seals itself after {hours} h without changes."
        return text
    if state is ProjectState.CHANGED:
        return (
            "New files were added since the last seal, which is fine. They join the manifest "
            f"after {settings.settle_hours} h without changes."
        )
    if state is ProjectState.QUEUED:
        return "Queued. The app works on it after working hours."
    if state is ProjectState.HASHING:
        text = "Reading every file to write the manifest."
        if sealer.cancellable_job(db, project) is not None:
            text += " Cancel stops it at the next file; files already read are kept."
        return text
    if state is ProjectState.IGNORED:
        return "Ignored. The app leaves this folder alone."
    if state is ProjectState.MISSING:
        text = f"This project's files have not been on disk since {_missing_since(project, tz)}."
        return text + (
            " Retry looks for them again. Forget permanently deletes the app's record and the "
            "saved MHL history of this project; the archive itself is not touched."
        )
    return f"Something went wrong: {project.error or 'unknown error'}. The app retries next round."


@dataclass(slots=True)
class Headline:
    tone: str  # "alert" (red), "wait" (amber) or "calm"
    text: str


def _n(count: int, one: str, many: str) -> str:
    return f"1 {one}" if count == 1 else f"{count} {many}"


def headline(projects: list[ProjectRow], archive_ok: bool) -> Headline:
    """The one sentence at the top of the main screen. "Every project is sealed" only when
    every watched project really is ``sealed``; anything moving keeps the amber tone."""
    if not archive_ok:
        return Headline("alert", "The archive is not reachable.")
    states = Counter(p.state for p in projects)
    missing = states[ProjectState.MISSING]
    decide = states[ProjectState.NEEDS_REVIEW] + states[ProjectState.ERROR] + missing
    if decide and decide == missing:
        text = _n(missing, "project is", "projects are") + " missing from the disk."
        return Headline("alert", text)
    if decide:
        return Headline("alert", _n(decide, "project needs", "projects need") + " your decision.")
    if states[ProjectState.UNSEALED]:
        text = _n(states[ProjectState.UNSEALED], "project is", "projects are") + " not sealed yet."
        return Headline("wait", text)
    sealing = states[ProjectState.QUEUED] + states[ProjectState.HASHING]
    if sealing:
        return Headline("wait", _n(sealing, "project is", "projects are") + " being sealed.")
    if states[ProjectState.CHANGED]:
        text = _n(states[ProjectState.CHANGED], "project has", "projects have")
        return Headline("wait", text + " new files waiting to be added.")
    if states[ProjectState.SEALED]:
        return Headline("calm", "All quiet. Every project is sealed.")
    if states[ProjectState.IGNORED]:
        return Headline("calm", "Every project is ignored.")
    return Headline("calm", "No projects found yet.")


@dataclass(slots=True)
class ActivityRow:
    when: str
    text: str
    tone: str  # running, queued, done, review, failed, cancelled
    project_id: int | None = None
    detail: str = ""
    pct: int | None = None


@dataclass(slots=True)
class Activity:
    upcoming: list[ActivityRow] = field(default_factory=list)
    past: list[ActivityRow] = field(default_factory=list)


_DONE_LABEL: dict[JobKind, str] = {
    JobKind.SEAL: "Sealed",
    JobKind.APPEND: "Added new files to",
    JobKind.ACCEPT_NEW_VERSION: "Sealed a new version of",
    JobKind.VERIFY: "Verified",
    JobKind.ROOT_MANIFEST: "Updated the archive manifest",
    JobKind.RETIRE: "Forgot",
}
_FAILED_LABEL: dict[JobKind, str] = {
    JobKind.SEAL: "Could not seal",
    JobKind.APPEND: "Could not add new files to",
    JobKind.ACCEPT_NEW_VERSION: "Could not seal a new version of",
    JobKind.VERIFY: "Could not verify",
    JobKind.ROOT_MANIFEST: "Could not update the archive manifest",
    JobKind.RETIRE: "Could not forget",
}
_QUEUED_LABEL: dict[JobKind, str] = {
    JobKind.SEAL: "Seal",
    JobKind.APPEND: "Add new files to",
    JobKind.ACCEPT_NEW_VERSION: "Seal a new version of",
    JobKind.VERIFY: "Verify",
    JobKind.ROOT_MANIFEST: "Update the archive manifest",
    JobKind.RETIRE: "Forget",
}


_REVIEW_LOG_PREFIX = sealer.REVIEW_LOG_PREFIX
_RETIRE_PREFIX, _RETIRE_SUFFIX = sealer.RETIRE_LOG_TEMPLATE.split("{rel_path}")


def _went_to_review(db: Database, job: JobRow) -> str:
    """A job that sent its project to review still ends ``done`` (``sealer._to_review``): the
    reason is in its log, and a verification also leaves its problem rows. Empty if it went
    well."""
    for entry in db.get_job_log(job.id):
        if entry.msg.startswith(_REVIEW_LOG_PREFIX):
            return entry.msg.removeprefix(_REVIEW_LOG_PREFIX)
    if job.kind is JobKind.VERIFY and job.project_id is not None:
        problems = Counter(
            r.status
            for r in db.get_verify_results(job.project_id, job.id)
            if r.status in sealer.VERIFY_PROBLEMS
        )
        if problems:
            return ", ".join(f"{n} {status}" for status, n in sorted(problems.items()))
    return ""


def _retire_message(db: Database, job: JobRow) -> str:
    for entry in db.get_job_log(job.id):
        if entry.msg.startswith(_RETIRE_PREFIX):
            return entry.msg
    return ""


def _retired_name(message: str) -> str:
    """The project's folder name out of ``retired <rel_path> (history mirror deleted)``."""
    rel = message.removeprefix(_RETIRE_PREFIX).removesuffix(_RETIRE_SUFFIX)
    return rel.rsplit("/", 1)[-1]


def activity(
    db: Database, tz: ZoneInfo, now: datetime, current: JobRow | None, limit: int = 8
) -> Activity:
    """The app's own work as a calm log, like a transfer log: what runs now, what waits in the
    queue, and the last finished jobs. Read from ``jobs`` only; no access to the share."""
    names = {p.id: p.name for p in db.list_projects()}
    out = Activity()

    def label(table: dict[JobKind, str], job: JobRow) -> str:
        text = table.get(job.kind, str(job.kind))
        manual_verify = job.kind is JobKind.VERIFY and job.trigger is Trigger.MANUAL
        if manual_verify and (table is JOB_LABEL or table is _QUEUED_LABEL):  # not when finished
            text = VERIFY_NOW_LABEL
        if job.project_id is not None and job.project_id in names:
            text += " " + names[job.project_id]
        return text

    jobs = db.list_jobs(limit=200)
    if current is not None:
        fresh = db.get_job(current.id) or current
        jobs = [fresh, *(j for j in jobs if j.id != fresh.id)]
    queued: list[JobRow] = []
    finished: list[JobRow] = []
    for job in jobs:
        if job.state is JobState.RUNNING:
            if any(r.tone == "running" for r in out.upcoming):
                continue
            detail = (
                f"{job.files_done} of {job.files_total} files · "
                f"{human_size(job.bytes_done)} of {human_size(job.bytes_total)}"
            )
            out.upcoming.append(
                ActivityRow(
                    "now", label(JOB_LABEL, job), "running", job.project_id, detail, percent(job)
                )
            )
        elif job.state is JobState.QUEUED:
            queued.append(job)
        elif job.finished_at:
            finished.append(job)
    # Jobs run by priority, not by id: "Earlier" is ordered by when they finished.
    finished.sort(
        key=lambda j: _as_utc(j.finished_at) or datetime.min.replace(tzinfo=UTC), reverse=True
    )
    for job in finished[:limit]:
        when = fmt_when(job.finished_at, tz, now)
        if job.state is JobState.DONE and job.kind is JobKind.RETIRE:
            message = _retire_message(db, job)
            text = "Forgot " + _retired_name(message)
            out.past.append(ActivityRow(when, text.strip(), "done", None, message))
        elif job.state is JobState.DONE:
            review = _went_to_review(db, job)
            if review and job.kind is JobKind.VERIFY:
                text = "Verification found problems in " + names.get(job.project_id or -1, "")
                found = review.removeprefix(VERIFICATION_PREFIX).strip()
                out.past.append(ActivityRow(when, text.strip(), "review", job.project_id, found))
            elif review:
                name = names.get(job.project_id or -1, "A project")
                text, detail = f"{name} needs your decision", "nothing was written: " + review
                out.past.append(ActivityRow(when, text, "review", job.project_id, detail))
            else:
                detail = ""
                if job.files_total:
                    detail = f"{job.files_total} files · {human_size(job.bytes_total)}"
                text = label(_DONE_LABEL, job)
                out.past.append(ActivityRow(when, text, "done", job.project_id, detail))
        elif job.state is JobState.FAILED:
            text = label(_FAILED_LABEL, job)
            out.past.append(ActivityRow(when, text, "failed", job.project_id, job.error or ""))
        else:
            text = "Cancelled: " + label(_QUEUED_LABEL, job)
            out.past.append(ActivityRow(when, text, "cancelled", job.project_id))
    queued.sort(key=lambda j: (-j.priority, j.id))
    for job in queued[:limit]:
        text = label(_QUEUED_LABEL, job)
        out.upcoming.append(ActivityRow("next", text, "queued", job.project_id))
    if len(queued) > limit:
        out.upcoming.append(ActivityRow("next", f"and {len(queued) - limit} more", "queued"))
    return out
