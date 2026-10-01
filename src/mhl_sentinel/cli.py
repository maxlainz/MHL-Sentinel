"""Command line: ``mhl-sentinel`` (docs/arquitectura.md, ``cli.py``).

``run-once`` is the hito 1 way of running the whole pipeline once: scan cycle, scheduling of the
periodic verifications and of the root manifest (hito 4), then the queued jobs one after another,
only outside working hours (D33) unless ``--ignore-working-hours``. When the queue is empty and a
generation made the root manifest stale, the ``root_manifest`` job is queued and run too.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime

import click
import yaml

from mhl_sentinel import __version__, rootmanifest, sealer
from mhl_sentinel.clock import utcnow
from mhl_sentinel.config import Settings, load_settings
from mhl_sentinel.db import Database, NetworkFilesystemError
from mhl_sentinel.events import EventBus
from mhl_sentinel.models import JobState, ProjectState
from mhl_sentinel.schedule import WorkingHours
from mhl_sentinel.settings_ref import SettingsRef
from mhl_sentinel.supervisor import Supervisor, SupervisorStatus


def _force_utc() -> None:
    # ascmhl writes dates with the current offset; the app always runs with TZ=UTC (contract).
    if os.environ.get("TZ") != "UTC":
        os.environ["TZ"] = "UTC"
        time.tzset()


def _open_db(settings: Settings) -> Database:
    try:
        return Database(settings.db_path).open()
    except NetworkFilesystemError as exc:
        raise click.ClickException(str(exc)) from exc


class WorkingHoursGate:
    """Open outside working hours. When it finds itself closed it sets ``stop``: a CLI run
    does not wait for the evening, the job goes back to the queue with its checkpoints."""

    def __init__(
        self,
        hours: WorkingHours | None,
        stop: threading.Event,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        self._hours = hours
        self._stop = stop
        self._now = now_fn

    def is_set(self) -> bool:
        if self._hours is None or not self._hours.is_working(self._now()):
            return True
        self._stop.set()
        return False

    def wait(self, timeout: float | None = None) -> bool:
        return self.is_set()


@click.group()
@click.version_option(__version__, prog_name="mhl-sentinel")
def main() -> None:
    """MHL Sentinel: ASC MHL histories for an archive of finished projects."""


@main.command("run-once")
@click.option("--ignore-working-hours", is_flag=True, help="Hash even inside working hours.")
@click.option("--seal-all", is_flag=True, help="Queue Seal for every unsealed project first.")
def run_once(ignore_working_hours: bool, seal_all: bool) -> None:
    """One scan cycle, then run the queued jobs sequentially."""
    _force_utc()
    settings = load_settings()
    with _open_db(settings) as db:
        sealer.recover_after_restart(db)
        try:
            summary = sealer.run_scan_cycle(db, settings, now=utcnow())
        except sealer.ArchiveUnavailableError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            f"scan: {summary.discovered} projects ({len(summary.new_projects)} new,"
            f" {len(summary.missing)} missing), {summary.scanned} scanned,"
            f" {len(summary.enqueued)} queued automatically"
        )
        for orphan in summary.orphans:
            click.echo(f"orphan manifest set aside: {orphan}")
        for error in summary.errors:
            click.echo(f"error: {error}")
        if seal_all:
            for project in db.list_projects(ProjectState.UNSEALED):
                sealer.request_seal(db, project.id, utcnow())

        stop = threading.Event()
        hours = None if ignore_working_hours else WorkingHours.from_settings(settings)
        gate = WorkingHoursGate(hours, stop)
        working = hours is not None and hours.is_working(utcnow())
        for rel, kind in sealer.schedule_maintenance(db, settings, now=utcnow(), working=working):
            click.echo(f"queued {kind} {rel}")
        root_checked = False
        while True:
            job = db.next_job(utcnow())
            if job is None:
                # Seals and verifications of this run made the root manifest stale (once).
                if root_checked or not sealer.schedule_root_manifest(db, settings, now=utcnow()):
                    break
                root_checked = True
                continue
            if not gate.is_set():
                click.echo("working hours: queued jobs left for later")
                break
            target = db.get_project(job.project_id) if job.project_id is not None else None
            label = target.rel_path if target else "."
            sealer.run_job(db, settings, job, gate=gate, stop=stop)
            done = db.get_job(job.id)
            assert done is not None
            after = db.get_project(job.project_id) if job.project_id is not None else None
            state = after.state if after else rootmanifest.last_root_manifest_at(db) or "-"
            extra = f" ({done.error})" if done.error else ""
            click.echo(f"job {job.id} {job.kind} {label}: {done.state} → {state}{extra}")
            if done.state is JobState.QUEUED:  # stopped by the working-hours gate
                break


@main.group("settings")
def settings_group() -> None:
    """Effective settings."""


@settings_group.command("show")
def settings_show() -> None:
    """Print the effective settings (defaults < config.yaml < env MHLS_*) as YAML."""
    data = load_settings().model_dump(mode="json")
    click.echo(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), nl=False)


@main.group("projects")
def projects_group() -> None:
    """Projects known to the state database."""


@projects_group.command("list")
def projects_list() -> None:
    """Table: state, path, files, bytes, last sealed."""
    settings = load_settings()
    with _open_db(settings) as db:
        rows = db.list_projects()
        click.echo(f"{'STATE':<13} {'FILES':>6} {'BYTES':>12} {'LAST SEALED':<20} PATH")
        for p in rows:
            sealed = (p.last_sealed_at or "-")[:19]
            click.echo(
                f"{p.state.value:<13} {p.file_count or 0:>6} {p.total_bytes or 0:>12}"
                f" {sealed:<20} {p.rel_path}"
            )


def _project_action(action: str, rel_path: str) -> None:
    settings = load_settings()
    with _open_db(settings) as db:
        project = db.get_project(rel_path)
        if project is None:
            raise click.ClickException(f"unknown project {rel_path!r} (run run-once first)")
        func: Callable[[Database, int, datetime], object] = {
            "seal": sealer.request_seal,
            "accept": sealer.request_accept_new_version,
            "postpone": sealer.request_postpone,
            "ignore": sealer.request_ignore,
            "unignore": sealer.request_unignore,
        }[action]
        try:
            func(db, project.id, utcnow())
        except sealer.SealerError as exc:
            raise click.ClickException(str(exc)) from exc
        after = db.get_project(project.id)
        assert after is not None
        click.echo(f"{rel_path}: {after.state}")


for _action, _help in (
    ("seal", "Seal button (D16): queue a seal for an unsealed project."),
    ("accept", "Accept as new version (D17) for a project in review."),
    ("postpone", "Postpone (D17): leave the project in review."),
    ("ignore", "Ignore button (D19)."),
    ("unignore", "Undo Ignore."),
):

    def _make(action: str) -> Callable[[str], None]:
        def command(rel_path: str) -> None:
            _project_action(action, rel_path)

        return command

    projects_group.command(_action, help=_help)(click.argument("rel_path")(_make(_action)))


@main.command("serve")
@click.option("--host", default="0.0.0.0", show_default=True, help="Address to listen on.")
@click.option("--once-tick", is_flag=True, hidden=True, help="Run one supervisor tick and exit.")
def serve(host: str, once_tick: bool) -> None:
    """Web GUI and supervisor in one process (hito 2). SIGTERM/SIGINT stop it cleanly."""
    from mhl_sentinel.server import (  # lazy: uvicorn/FastAPI only for serve
        attach_supervisor,
        build_app,
        configure_logging,
        make_server,
    )

    _force_utc()
    settings = load_settings()
    configure_logging(os.environ.get("MHLS_LOG_LEVEL") or settings.log_level)
    with _open_db(settings) as db:
        settings_ref = SettingsRef(settings)
        bus = EventBus(db)
        supervisor = Supervisor(db, settings_ref, bus)
        if once_tick:
            status = asyncio.run(_one_tick(supervisor))
            click.echo(
                f"tick: working_now={status.working_now} gate_open={status.gate_open}"
                f" archive_reachable={status.archive_reachable}"
                f" queued_jobs={status.queued_jobs}"
            )
            return
        app = attach_supervisor(build_app(db, settings_ref, supervisor, bus), supervisor)
        make_server(app, settings, host=host).run()


async def _one_tick(supervisor: Supervisor) -> SupervisorStatus:
    supervisor.bus.bind(asyncio.get_running_loop())
    sealer.recover_after_restart(supervisor.db)
    await supervisor.tick()
    return supervisor.status()


if __name__ == "__main__":  # pragma: no cover
    main()
