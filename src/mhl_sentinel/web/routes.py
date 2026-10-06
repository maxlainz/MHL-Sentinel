"""Routes of the web GUI (docs/arquitectura.md, "Hito 3"; D10, D11, D25, D26, D45, D46, D49).

HTML for people (one screen + project detail + settings), HTMX fragments refreshed by SSE, and
small JSON endpoints for scripts. Buttons go through ``sealer.request_*`` only: the web layer
never changes a project state by itself.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from mhl_sentinel import __version__, history_mirror, rootmanifest, sealer
from mhl_sentinel.clock import utcnow
from mhl_sentinel.config import ENV_ONLY_FIELDS, Settings, load_settings, save_yaml
from mhl_sentinel.db import JobRow, ProjectRow
from mhl_sentinel.discovery import find_stray_entries
from mhl_sentinel.models import JobKind, ProjectState
from mhl_sentinel.schedule import WorkingHours
from mhl_sentinel.web import settings_form, views
from mhl_sentinel.web.deps import BusLike, EventLike, StatusLike, WebContext

TEMPLATES_DIR = Path(__file__).parent / "templates"
SSE_PING_SECONDS = 15
STRAY_TTL_SECONDS = 300.0
MAX_STRAYS_SHOWN = 50

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["size"] = views.human_size

router = APIRouter()


# --- helpers ---------------------------------------------------------------------------------


def ctx_of(request: Request) -> WebContext:
    ctx: WebContext = request.app.state.ctx
    return ctx


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


class StrayCache:
    """Stray entries (D49) cost a few directory listings on the share: refreshed every 5 min,
    never while the archive is unreachable (a hung mount would block a worker)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = -STRAY_TTL_SECONDS
        self._key: tuple[str, int, str] | None = None
        self._value: list[str] = []

    def get(self, root: Path, depth: int, prefixes: str, reachable: bool) -> list[str]:
        key = (str(root), depth, prefixes)
        with self._lock:
            fresh = key == self._key and time.monotonic() - self._at < STRAY_TTL_SECONDS
            if fresh or not reachable:
                return list(self._value) if key == self._key else []
        try:
            value = find_stray_entries(root, depth, prefixes)
        except (OSError, ValueError):
            value = []
        with self._lock:
            self._key, self._at, self._value = key, time.monotonic(), value
        return list(value)


def _status_dict(status: StatusLike) -> dict[str, Any]:
    def iso(value: datetime | str | None) -> str | None:
        if value is None or isinstance(value, str):
            return value
        return value.isoformat()

    job = status.current_job
    return {
        "working_now": status.working_now,
        "next_change": iso(status.next_change),
        "gate_open": status.gate_open,
        "last_cycle_at": iso(status.last_cycle_at),
        "archive_reachable": status.archive_reachable,
        "queued_jobs": status.queued_jobs,
        "current_job": None if job is None else _job_dict(job),
    }


def _job_dict(job: JobRow) -> dict[str, Any]:
    data = dataclasses.asdict(job)
    data["kind"] = job.kind.value
    data["trigger"] = job.trigger.value
    data["state"] = job.state.value
    return data


def _project_dict(project: ProjectRow) -> dict[str, Any]:
    data = dataclasses.asdict(project)
    data["state"] = project.state.value
    before = project.state_before_missing
    data["state_before_missing"] = before.value if before is not None else None
    return data


def header_context(ctx: WebContext) -> dict[str, Any]:
    settings = ctx.settings
    tz = settings.tzinfo
    now = utcnow()
    status = ctx.supervisor.status()
    job = status.current_job
    job_text = ""
    job_pct: int | None = None
    if job is not None:
        fresh = ctx.db.get_job(job.id) or job
        project = ctx.db.get_project(fresh.project_id) if fresh.project_id is not None else None
        job_text = views.job_label(fresh)
        if project is not None:
            job_text += " " + project.name
        job_pct = views.percent(fresh)
    projects = ctx.db.list_projects()
    counters = views.counters(projects, status.queued_jobs)
    return {
        "headline": views.headline(projects, status.archive_reachable),
        "counters": counters,
        "archive_ok": status.archive_reachable,
        "working_now": status.working_now,
        "gate_open": status.gate_open,
        "next_change": views.fmt_when(status.next_change, tz, now),
        "last_cycle": views.fmt_when(status.last_cycle_at, tz, now),
        "job_text": job_text,
        "job_pct": job_pct,
        "queued_jobs": status.queued_jobs,
        "timezone": settings.timezone,
        "root_manifest": views.fmt_datetime(rootmanifest.last_root_manifest_at(ctx.db), tz),
    }


def projects_context(ctx: WebContext) -> dict[str, Any]:
    settings = ctx.settings
    status = ctx.supervisor.status()
    projects = ctx.db.list_projects()
    return {
        "inbox": views.inbox(ctx.db, projects, settings, status.current_job, utcnow()),
        "preview": views.INBOX_PREVIEW,
        "counters": views.counters(projects, status.queued_jobs),
        "archive_ok": status.archive_reachable,
    }


def activity_context(ctx: WebContext) -> dict[str, Any]:
    settings = ctx.settings
    tz = settings.tzinfo
    now = utcnow()
    status = ctx.supervisor.status()
    return {
        "activity": views.activity(ctx.db, tz, now, status.current_job),
        "working_now": status.working_now,
        "archive_ok": status.archive_reachable,
        "next_change": views.fmt_when(status.next_change, tz, now),
    }


def project_context(ctx: WebContext, project: ProjectRow, *, with_history: bool) -> dict[str, Any]:
    settings = ctx.settings
    tz = settings.tzinfo
    status = ctx.supervisor.status()
    job = views.running_job_for(ctx.db, project.id, status.current_job)
    history = views.History()
    if with_history and project.state is not ProjectState.IGNORED:
        if status.archive_reachable:
            history = views.load_history(settings.archive_root / project.rel_path, tz)
        else:
            history = views.History(error="the archive is not reachable right now")
    verification = views.is_verification_review(project)
    review = (
        views.review_rows(ctx.db, project, tz)
        if project.state is ProjectState.NEEDS_REVIEW and not verification
        else []
    )
    # D73, D74: Seal now / Accept now / Update now on a waiting job, only inside working hours
    # (outside them the job already runs). Update now on a changed project, at any hour (D74).
    now = utcnow()
    working = WorkingHours.from_settings(settings).is_working(now)
    start_now = views.start_now_label(ctx.db, project, settings, now)
    update_now = views.can_update_now(project)
    verifiable = project.state in (ProjectState.SEALED, ProjectState.CHANGED)
    generations = project.last_generation_no
    if history.generations:
        generations = len(history.generations)
    return {
        "p": project,
        "view": views.project_view(ctx.db, project, settings, job),
        "sentence": views.detail_sentence(ctx.db, project, settings),
        "sealed_on": views.fmt_date(project.last_sealed_at, tz),
        "verified_on": views.fmt_date(project.last_verified_at, tz),
        "last_scan": views.fmt_datetime(project.last_scan_at, tz),
        "generation_count": generations,
        "history": history,
        "archive": views.archive_panel(ctx.db, project, settings, now),
        "review": review,
        "verification": views.verify_view(ctx.db, project, tz) if verification else None,
        "job": job,
        "job_label": "" if job is None else views.job_label(job),
        "job_running": job is not None and job.state.value == "running",
        "job_pct": views.percent(job),
        "can_seal": project.state is ProjectState.UNSEALED,
        "can_cancel": sealer.cancellable_job(ctx.db, project) is not None,
        "can_start_now": bool(start_now),
        "start_now_label": start_now,
        "can_update_now": update_now,
        "read_size": views.read_size(ctx.db, project) if start_now or update_now else None,
        "working": working,
        "settle_hours": settings.settle_hours,
        "can_review": project.state is ProjectState.NEEDS_REVIEW,
        "can_ignore": project.state
        not in (ProjectState.IGNORED, ProjectState.HASHING, ProjectState.MISSING),
        "can_retry": project.state is ProjectState.MISSING,
        # D63, D74: Verify now on a sealed or changed project, with a warning in working hours.
        "can_verify": verifiable and project.last_generation_no is not None,
        "verify_warn": verifiable and working,
        "can_unignore": project.state is ProjectState.IGNORED,
    }


def _get_project(ctx: WebContext, project_id: int) -> ProjectRow:
    project = ctx.db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


# --- pages -----------------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> Response:
    ctx = ctx_of(request)
    settings = ctx.settings
    status = ctx.supervisor.status()
    cache: StrayCache = request.app.state.strays
    strays = cache.get(
        settings.archive_root,
        settings.project_depth,
        settings.ignore_prefixes,
        status.archive_reachable,
    )
    retired = request.query_params.get("retired", "")[:200]
    context = {
        **activity_context(ctx),
        **header_context(ctx),
        **projects_context(ctx),
        "notice": f"{retired} forgotten" if retired else "",
        "strays": strays[:MAX_STRAYS_SHOWN],
        "strays_more": max(0, len(strays) - MAX_STRAYS_SHOWN),
    }
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/fragments/header", response_class=HTMLResponse)
def fragment_header(request: Request) -> Response:
    return templates.TemplateResponse(request, "_header.html", header_context(ctx_of(request)))


@router.get("/fragments/projects", response_class=HTMLResponse)
def fragment_projects(request: Request) -> Response:
    return templates.TemplateResponse(request, "_projects.html", projects_context(ctx_of(request)))


@router.get("/fragments/activity", response_class=HTMLResponse)
def fragment_activity(request: Request) -> Response:
    return templates.TemplateResponse(request, "_activity.html", activity_context(ctx_of(request)))


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: int) -> Response:
    ctx = ctx_of(request)
    project = _get_project(ctx, project_id)
    context = project_context(ctx, project, with_history=True)
    return templates.TemplateResponse(request, "project.html", context)


@router.get("/fragments/projects/{project_id}", response_class=HTMLResponse)
def fragment_project(request: Request, project_id: int) -> Response:
    ctx = ctx_of(request)
    project = _get_project(ctx, project_id)
    context = project_context(ctx, project, with_history=False)
    return templates.TemplateResponse(request, "_project_card.html", context)


@router.get("/fragments/projects/{project_id}/progress", response_class=HTMLResponse)
def fragment_progress(request: Request, project_id: int) -> Response:
    ctx = ctx_of(request)
    project = _get_project(ctx, project_id)
    context = project_context(ctx, project, with_history=False)
    return templates.TemplateResponse(request, "_progress.html", context)


# --- buttons ---------------------------------------------------------------------------------

_ACTIONS: dict[str, tuple[Callable[..., object], str]] = {
    "seal": (sealer.request_seal, "Seal requested: it runs after working hours."),
    "ignore": (sealer.request_ignore, "Ignored: the app will leave this folder alone."),
    "unignore": (sealer.request_unignore, "Watched again: the next round checks it."),
    "accept": (
        sealer.request_accept_new_version,
        "Accepted: the project is sealed again as it is today, after working hours.",
    ),
    "postpone": (sealer.request_postpone, "Postponed: nothing changed."),
    "cancel": (sealer.request_cancel, "Cancelled: the project is as it was before the request."),
}


def _action_response(
    request: Request,
    ctx: WebContext,
    project_id: int,
    *,
    notice: str,
    error: str = "",
    status_code: int = 200,
    retired: str | None = None,
) -> Response:
    """The answer of a button: back to the inbox or the detail page, or (Retire) gone."""
    # Buttons in the inbox (main screen) come back to the inbox; the detail page to the card.
    from_inbox = request.query_params.get("from") == "inbox"
    if retired is not None:
        # The project no longer exists: nothing to render but the inbox, with the notice.
        home = "/?" + urlencode({"retired": retired})
        if not is_htmx(request):
            return RedirectResponse(home, status_code=303)
        if not from_inbox:
            return Response(headers={"HX-Redirect": home})
        context = projects_context(ctx)
        context.update(notice=notice, error="")
        response = templates.TemplateResponse(request, "_projects.html", context)
        response.headers["HX-Trigger"] = "inbox-changed"
        return response
    if not is_htmx(request):
        return RedirectResponse("/" if from_inbox else f"/projects/{project_id}", status_code=303)
    if from_inbox:
        context = projects_context(ctx)
        name = _get_project(ctx, project_id).name
        context.update(notice=f"{name}: {notice}" if notice else "", error=error)
        response = templates.TemplateResponse(
            request, "_projects.html", context, status_code=status_code
        )
    else:
        project = _get_project(ctx, project_id)
        context = project_context(ctx, project, with_history=False)
        context.update(notice=notice, error=error)
        response = templates.TemplateResponse(
            request, "_project_card.html", context, status_code=status_code
        )
    # The buttons do not publish bus events: tell the headline and the activity log to refresh.
    response.headers["HX-Trigger"] = "inbox-changed"
    return response


@router.post("/projects/{project_id}/retire", response_class=HTMLResponse)
def project_retire(request: Request, project_id: int) -> Response:
    """Retire (D60): deletes the record and the history mirror of a ``missing`` project."""
    ctx = ctx_of(request)
    _get_project(ctx, project_id)
    try:
        name = sealer.request_retire(ctx.db, ctx.settings, project_id, utcnow())
    except sealer.SealerError as exc:
        return _action_response(
            request, ctx, project_id, notice="", error=str(exc), status_code=409
        )
    return _action_response(request, ctx, project_id, notice=f"{name} forgotten", retired=name)


@router.post("/projects/{project_id}/retry", response_class=HTMLResponse)
def project_retry(request: Request, project_id: int) -> Response:
    """Retry (D61): one look at the folder now."""
    ctx = ctx_of(request)
    _get_project(ctx, project_id)
    try:
        back = sealer.request_retry(ctx.db, ctx.settings, project_id, utcnow())
    except sealer.SealerError as exc:
        return _action_response(
            request, ctx, project_id, notice="", error=str(exc), status_code=409
        )
    if back and ctx.db.has_open_job(project_id, JobKind.VERIFY):
        ctx.supervisor.notify_job_queued()
        working = WorkingHours.from_settings(ctx.settings).is_working(utcnow())
        notice = "Back on disk: verification queued" + (
            ", it runs after working hours." if working else "."
        )
    elif back:
        notice = "Back on disk."
    else:
        notice = "Still not on disk."
    return _action_response(request, ctx, project_id, notice=notice)


@router.post("/projects/{project_id}/verify", response_class=HTMLResponse)
def project_verify(request: Request, project_id: int) -> Response:
    """Verify now (D63): a manual verification that also runs inside working hours."""
    ctx = ctx_of(request)
    _get_project(ctx, project_id)
    now = utcnow()
    try:
        sealer.request_verify_now(ctx.db, project_id, now)
    except sealer.SealerError as exc:
        return _action_response(
            request, ctx, project_id, notice="", error=str(exc), status_code=409
        )
    ctx.supervisor.notify_job_queued()
    working = WorkingHours.from_settings(ctx.settings).is_working(now)
    # D63: inside working hours it starts at once (a paused job yields to it); outside them it
    # goes ahead of everything waiting, after the job that may be running.
    notice = "Verification started." if working else "Verification queued ahead of the rest."
    return _action_response(request, ctx, project_id, notice=notice)


_STARTED: dict[JobKind, str] = {
    JobKind.SEAL: "Sealing started.",
    JobKind.ACCEPT_NEW_VERSION: "Accept started.",
    JobKind.APPEND: "Update started.",
}


@router.post("/projects/{project_id}/start-now", response_class=HTMLResponse)
def project_start_now(request: Request, project_id: int) -> Response:
    """Seal now / Accept now / Update now (D73, D74): a Seal, Accept or append (manual or
    automatic) waiting for the end of the working hours (queued, or running but paused by them)
    starts at once."""
    ctx = ctx_of(request)
    project = _get_project(ctx, project_id)
    try:
        if project.state is ProjectState.HASHING:
            # D57, D73: a running job is the hasher thread's; only the supervisor touches it.
            job = ctx.db.running_job(project_id)
            if job is None or not ctx.supervisor.request_start_now(project_id):
                raise sealer.SealerError("this job cannot be started now")
        else:
            job_id = sealer.request_start_now(ctx.db, project_id, utcnow())
            job = ctx.db.get_job(job_id)
    except sealer.SealerError as exc:
        return _action_response(
            request, ctx, project_id, notice="", error=str(exc), status_code=409
        )
    ctx.supervisor.notify_job_queued()
    notice = _STARTED[job.kind] if job is not None else _STARTED[JobKind.SEAL]
    return _action_response(request, ctx, project_id, notice=notice)


@router.post("/projects/{project_id}/update-now", response_class=HTMLResponse)
def project_update_now(request: Request, project_id: int) -> Response:
    """Update now (D74): the new files of a ``changed`` project join the manifest without
    waiting for the settle time, also inside working hours."""
    ctx = ctx_of(request)
    _get_project(ctx, project_id)
    now = utcnow()
    try:
        sealer.request_update_now(ctx.db, project_id, now)
    except sealer.SealerError as exc:
        return _action_response(
            request, ctx, project_id, notice="", error=str(exc), status_code=409
        )
    ctx.supervisor.notify_job_queued()
    working = WorkingHours.from_settings(ctx.settings).is_working(now)
    notice = "Update started." if working else "Update queued ahead of the rest."
    return _action_response(request, ctx, project_id, notice=notice)


@router.get("/projects/{project_id}/history.zip")
def project_history_zip(request: Request, project_id: int) -> Response:
    """The saved MHL history of a project as a zip (D60: download before Retire)."""
    ctx = ctx_of(request)
    project = _get_project(ctx, project_id)
    data = history_mirror.zip_history(ctx.settings, project)
    if data is None:
        raise HTTPException(status_code=404, detail="no saved MHL history for this project")
    filename = f"{project.name}-ascmhl.zip"
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/projects/{project_id}/{action}", response_class=HTMLResponse)
def project_action(request: Request, project_id: int, action: str) -> Response:
    if action not in _ACTIONS:
        raise HTTPException(status_code=404, detail="unknown action")
    ctx = ctx_of(request)
    _get_project(ctx, project_id)
    func, notice = _ACTIONS[action]
    status_code = 200
    error = ""
    try:
        if action == "cancel" and _get_project(ctx, project_id).state is ProjectState.HASHING:
            # D57: a running Seal/Accept is the hasher thread's; only the supervisor aborts it.
            if not ctx.supervisor.request_cancel(project_id):
                raise sealer.SealerError("this job cannot be cancelled now")
            notice = "Cancelling: the app stops at the next file."
        else:
            func(ctx.db, project_id, utcnow())
        if action in ("seal", "accept"):
            ctx.supervisor.notify_job_queued()  # wake the hasher thread instead of waiting a tick
    except sealer.SealerError as exc:
        status_code, error, notice = 409, str(exc), ""
    return _action_response(
        request, ctx, project_id, notice=notice, error=error, status_code=status_code
    )


@router.post("/scan-now", response_class=HTMLResponse)
async def scan_now(request: Request) -> Response:
    ctx = ctx_of(request)
    result = ctx.supervisor.request_scan_now()
    if inspect.isawaitable(result):
        await result
    if not is_htmx(request):
        return RedirectResponse("/", status_code=303)
    context = header_context(ctx)
    context["notice"] = "Scan requested: the list refreshes in a moment."
    return templates.TemplateResponse(request, "_header.html", context)


# --- settings (D25, D45) ---------------------------------------------------------------------


def _settings_page(request: Request, state: settings_form.FormState, code: int = 200) -> Response:
    settings = ctx_of(request).settings
    context = {
        "form": state,
        "s": settings,
        "days": settings_form.DAY_LABELS,
        "log_levels": settings_form.LOG_LEVELS,
        "themes": settings_form.THEMES,
        "env_overrides": settings_form.env_overrides(),
    }
    return templates.TemplateResponse(request, "settings.html", context, status_code=code)


@router.get("/settings", response_class=HTMLResponse)
def settings_get(request: Request) -> Response:
    values = settings_form.values_from_settings(ctx_of(request).settings)
    return _settings_page(request, settings_form.FormState(values=values))


@router.post("/settings", response_class=HTMLResponse)
async def settings_post(request: Request) -> Response:
    ctx = ctx_of(request)
    form = await request.form()
    raw = {key: [str(v) for v in form.getlist(key)] for key in form}
    values = settings_form.values_from_form(raw)
    current = ctx.settings
    new, errors = settings_form.build_settings(values, current)
    if new is None:
        return _settings_page(request, settings_form.FormState(values, errors), 422)
    any_sealed = any(p.last_generation_no is not None for p in ctx.db.list_projects())
    warnings = settings_form.glob_warnings(current, new, any_sealed)
    try:
        await asyncio.to_thread(_save, new)
    except OSError as exc:
        state = settings_form.FormState(values, {"form": f"could not save: {exc.strerror}"})
        return _settings_page(request, state, 500)
    # Re-read with the real precedence (defaults < yaml < env) so the running state never
    # shows a value an MHLS_* variable overrides (D36).
    effective = load_settings(new.config_dir).model_copy(
        update={f: getattr(current, f) for f in ENV_ONLY_FIELDS}
    )
    ctx.settings_ref.replace(effective)
    state = settings_form.FormState(
        settings_form.values_from_settings(effective), warnings=warnings, saved=True
    )
    return _settings_page(request, state)


def _save(new: Settings) -> None:
    save_yaml(new, new.config_file)


# --- machines --------------------------------------------------------------------------------


def _db_writable(ctx: WebContext) -> bool:
    try:
        with ctx.db.transaction():
            pass
    except (sqlite3.Error, RuntimeError):
        return False
    return True


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    archive_ok = ctx.supervisor.status().archive_reachable
    db_ok = _db_writable(ctx)
    ok = archive_ok and db_ok
    body = {
        "status": "ok" if ok else "error",
        "archive": "ok" if archive_ok else "unreachable",
        "db": "ok" if db_ok else "error",
        "version": __version__,
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/api/status")
def api_status(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    status = ctx.supervisor.status()
    projects = ctx.db.list_projects()
    body = _status_dict(status)
    body["counters"] = dataclasses.asdict(views.counters(projects, status.queued_jobs))
    body["version"] = __version__
    body["timezone"] = ctx.settings.timezone
    return JSONResponse(body)


@router.get("/api/projects")
def api_projects(request: Request) -> JSONResponse:
    projects = ctx_of(request).db.list_projects()
    return JSONResponse({"projects": [_project_dict(p) for p in projects]})


# --- server-sent events ----------------------------------------------------------------------


def _event_data(event: EventLike) -> str:
    ts = event.ts if isinstance(event.ts, str) else event.ts.isoformat()
    return json.dumps({**dict(event.payload), "ts": ts}, default=str)


async def event_stream(
    bus: BusLike, is_disconnected: Callable[[], Awaitable[bool]], poll: float = 1.0
) -> AsyncIterator[dict[str, str]]:
    """One SSE message per bus event (``event: <kind>``, ``data: json``); unsubscribes on exit."""
    queue = bus.subscribe()
    try:
        while True:
            if await is_disconnected():
                break
            try:
                event: EventLike = await asyncio.wait_for(queue.get(), timeout=poll)
            except TimeoutError:
                continue
            yield {"event": event.kind, "data": _event_data(event)}
    finally:
        bus.unsubscribe(queue)


@router.get("/events")
async def events(request: Request) -> EventSourceResponse:
    bus = ctx_of(request).bus
    return EventSourceResponse(event_stream(bus, request.is_disconnected), ping=SSE_PING_SECONDS)
