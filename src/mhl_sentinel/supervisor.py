"""Supervisor: the scheduler loop and the hasher thread (hito 2, docs/arquitectura.md).

- An asyncio task ticks every ``tick_seconds``: it opens the ``gate`` outside working hours and
  closes it inside them (D33), checks that the archive answers (a hung SMB mount must not hang
  the app), and runs a scan cycle (``sealer.run_scan_cycle`` in a worker thread) when the gate is
  open and the scan interval has passed, or when ``request_scan_now()`` asked for one (D26: a
  manual scan is allowed during working hours; it is only a scan).
- One hasher ``threading.Thread`` consumes the job queue by priority, one job and one file at a
  time (D34), only while the gate is open. Closing the gate pauses the read; ``stop()`` makes the
  current job raise ``Stopped`` at the next block, so it goes back to ``queued`` with its
  checkpoints and no generation is written.

Every exception inside a tick or a job is logged and published as a ``log`` event; nothing kills
the loop or the thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mhl_sentinel import rootmanifest, sealer
from mhl_sentinel.clock import to_iso, utcnow
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, JobRow
from mhl_sentinel.events import EventBus
from mhl_sentinel.models import JobKind, JobState, ProjectState
from mhl_sentinel.schedule import WorkingHours
from mhl_sentinel.settings_ref import SettingsRef

log = logging.getLogger(__name__)

ARCHIVE_TIMEOUT_SECONDS = 10.0
# Upper bound for the hasher thread to reach the next 8 MiB block boundary after ``stop``. A
# container runtime gives 10 s between SIGTERM and SIGKILL by default (Docker, Watchtower); the
# HTTP shutdown runs in parallel (``server.GRACEFUL_HTTP_SECONDS``), so 8 s leaves margin. If the
# thread is still reading (hung mount) it is a daemon: the process exits anyway, the job stays
# ``running`` in the DB and the next start puts it back in the queue.
STOP_TIMEOUT_SECONDS = 8.0
IDLE_SECONDS = 5.0
PROGRESS_MIN_INTERVAL = 0.5  # seconds between two job.progress events of the same job


@dataclass(frozen=True, slots=True)
class SupervisorStatus:
    working_now: bool
    next_change: datetime | None  # aware, in settings.timezone; None if it never changes
    gate_open: bool
    current_job: JobRow | None
    last_cycle_at: datetime | None
    archive_reachable: bool
    queued_jobs: int
    scan_requested: bool = False
    running: bool = False


def _check_listdir(path: os.PathLike[str] | str, timeout: float) -> bool:
    """``os.listdir`` in a daemon thread; False if it raises or does not return in time."""
    result: dict[str, bool] = {}

    def target() -> None:
        try:
            os.listdir(path)
            result["ok"] = True
        except OSError:
            result["ok"] = False

    thread = threading.Thread(target=target, name="mhls-archive-check", daemon=True)
    thread.start()
    thread.join(timeout)
    return result.get("ok", False)


class Supervisor:
    def __init__(
        self,
        db: Database,
        settings_ref: SettingsRef,
        bus: EventBus,
        *,
        tick_seconds: float = 60.0,
        idle_seconds: float = IDLE_SECONDS,
        stop_timeout: float = STOP_TIMEOUT_SECONDS,
        archive_timeout: float = ARCHIVE_TIMEOUT_SECONDS,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db = db
        self.settings_ref = settings_ref
        self.bus = bus
        self.tick_seconds = tick_seconds
        self.idle_seconds = idle_seconds
        self.stop_timeout = stop_timeout
        self.archive_timeout = archive_timeout
        self.now_fn = now_fn

        self.gate = threading.Event()  # open ⇔ outside working hours
        self.stop_event = threading.Event()
        self._wake = threading.Event()  # wakes the idle hasher (new job, stop)
        self._scan_requested = threading.Event()
        self._task: asyncio.Task[None] | None = None
        self._hasher: threading.Thread | None = None
        self._loop_wakeup: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._current_job_id: int | None = None
        self._last_cycle_at: datetime | None = None
        self._archive_reachable: bool | None = None
        self._archive_check: threading.Thread | None = None
        self._working_now = False
        # Startup recovery of ascmhl/ folders (stale temp files, orphan manifests) must run
        # before the first job; it needs the archive, so it runs at the first tick that reaches
        # it, and the hasher waits for it.
        self._fs_recovered = threading.Event()

    # -- public API --------------------------------------------------------------------------

    async def start(self) -> None:
        """Recover interrupted jobs, run the first tick and start the loop and the hasher."""
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self.bus.bind(self._loop)
        self.stop_event.clear()
        recovered = sealer.recover_after_restart(self.db)
        log.info(
            "state database open (schema v%d); %d interrupted job(s) back in the queue",
            self.db.user_version,
            recovered,
        )
        if recovered:
            self._publish_log("info", f"{recovered} interrupted job(s) back in the queue")
        self._loop_wakeup = asyncio.Event()
        self._update_gate()
        self._hasher = threading.Thread(target=self._hasher_main, name="mhls-hasher", daemon=True)
        self._hasher.start()
        self._task = asyncio.create_task(self._loop_main(), name="mhls-supervisor")

    def request_stop(self) -> None:
        """Begin stopping now (called from the SIGTERM handler, before the HTTP shutdown): the
        hasher aborts at its next block while uvicorn closes connections in parallel. Only sets
        ``stop_event`` directly (signal-safe); the rest is scheduled on the loop. ``stop()`` must
        still be awaited to join the thread."""
        self.stop_event.set()
        loop = self._loop
        if loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._begin_stop)

    def _begin_stop(self) -> None:
        self.gate.clear()
        self._wake.set()
        if self._loop_wakeup is not None:
            self._loop_wakeup.set()

    async def stop(self) -> None:
        """Close the gate, stop the hasher at the next block (job → queued), end the loop."""
        self.gate.clear()
        self.stop_event.set()
        self._wake.set()
        if self._loop_wakeup is not None:
            self._loop_wakeup.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        hasher, self._hasher = self._hasher, None
        if hasher is not None:
            await asyncio.to_thread(hasher.join, self.stop_timeout)
            if hasher.is_alive():
                log.error("hasher thread did not stop within %.0f s", self.stop_timeout)

    def request_scan_now(self) -> None:
        """``Run scan now`` (D26): a scan at the next tick, also inside working hours."""
        self._scan_requested.set()
        self._poke_loop()

    def notify_job_queued(self) -> None:
        """Wake the idle hasher now instead of after ``idle_seconds`` (e.g. after Seal)."""
        self._wake.set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> SupervisorStatus:
        settings = self.settings_ref.get()
        hours = WorkingHours.from_settings(settings)
        now = self.now_fn()
        working = hours.is_working(now)
        try:
            next_change: datetime | None = hours.next_change(now)
        except ValueError:
            next_change = None
        with self._lock:
            job_id = self._current_job_id
        current = self.db.get_job(job_id) if job_id is not None else None
        return SupervisorStatus(
            working_now=working,
            next_change=next_change,
            gate_open=self.gate.is_set(),
            current_job=current,
            last_cycle_at=self._last_cycle_at,
            archive_reachable=bool(self._archive_reachable),
            queued_jobs=self.db.count_jobs(JobState.QUEUED),
            scan_requested=self._scan_requested.is_set(),
            running=self.running,
        )

    async def tick(self) -> None:
        """One iteration of the loop (public for ``serve --once-tick`` and tests)."""
        try:
            await self._tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("supervisor tick failed")
            self._publish_log("error", f"tick failed: {type(exc).__name__}: {exc}")

    # -- loop --------------------------------------------------------------------------------

    def _poke_loop(self) -> None:
        loop, wakeup = self._loop, self._loop_wakeup
        if loop is not None and wakeup is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(wakeup.set)

    async def _loop_main(self) -> None:
        assert self._loop_wakeup is not None
        while not self.stop_event.is_set():
            await self.tick()
            self._loop_wakeup.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._loop_wakeup.wait(), self.tick_seconds)

    def _update_gate(self) -> bool:
        """Open the gate outside working hours (D33). Returns ``working_now``."""
        settings = self.settings_ref.get()
        working = WorkingHours.from_settings(settings).is_working(self.now_fn())
        if self.stop_event.is_set():
            self.gate.clear()
        elif working:
            if self.gate.is_set():
                log.info("working hours: pausing")
            self.gate.clear()
        else:
            if not self.gate.is_set():
                log.info("outside working hours: gate open")
                self._wake.set()
            self.gate.set()
        self._working_now = working
        return working

    async def _check_archive(self) -> bool:
        settings = self.settings_ref.get()
        previous = self._archive_check
        if previous is not None and previous.is_alive():
            reachable = False  # the last listdir is still hanging: the mount is stuck
        else:
            box: dict[str, bool] = {}

            def run() -> None:
                box["ok"] = _check_listdir(settings.archive_root, self.archive_timeout)

            checker = threading.Thread(target=run, name="mhls-archive-probe", daemon=True)
            self._archive_check = checker
            checker.start()
            await asyncio.to_thread(checker.join, self.archive_timeout + 1.0)
            reachable = box.get("ok", False)
        if reachable != self._archive_reachable:
            kind = "archive.ok" if reachable else "archive.unreachable"
            if not reachable:
                log.warning("archive root %s is not reachable", settings.archive_root)
            self.bus.publish(kind, {"root": str(settings.archive_root)})
        self._archive_reachable = reachable
        return reachable

    async def _tick(self) -> None:
        working = self._update_gate()
        settings = self.settings_ref.get()
        reachable = await self._check_archive()
        if reachable and not self._fs_recovered.is_set():
            await asyncio.to_thread(self._recover_history_dirs, settings)
        now = self.now_fn()
        requested = self._scan_requested.is_set()
        due = self._last_cycle_at is None or now - self._last_cycle_at >= timedelta(
            minutes=settings.scan_interval_minutes
        )
        scan = reachable and (requested or (not working and due))
        self.bus.publish(
            "cycle.started", {"scan": scan, "working_now": working, "manual": requested}
        )
        payload: dict[str, Any] = {"scan": scan, "working_now": working, "archive": reachable}
        if scan:
            self._scan_requested.clear()
            before = self._project_states()
            try:
                summary, maintenance = await asyncio.to_thread(
                    self._scan_and_schedule, settings, now, working
                )
            except sealer.ArchiveUnavailableError as exc:
                self._archive_reachable = False
                self.bus.publish("archive.unreachable", {"root": str(settings.archive_root)})
                self._publish_log("warning", str(exc))
                payload["error"] = str(exc)
            else:
                self._last_cycle_at = now
                payload.update(
                    discovered=summary.discovered,
                    scanned=summary.scanned,
                    new=len(summary.new_projects),
                    missing=len(summary.missing),
                    enqueued=len(summary.enqueued),
                    errors=len(summary.errors),
                    verify_enqueued=sum(k is JobKind.VERIFY for _, k in maintenance),
                    root_enqueued=any(k is JobKind.ROOT_MANIFEST for _, k in maintenance),
                )
                for error in summary.errors:
                    self._publish_log("warning", error)
                for orphan in summary.orphans:
                    self._publish_log("warning", f"orphan manifest set aside: {orphan}")
                if summary.enqueued or maintenance:
                    self._wake.set()
            self._publish_state_diff(before, self._project_states())
        payload["last_cycle_at"] = to_iso(self._last_cycle_at) if self._last_cycle_at else None
        self.bus.publish("cycle.finished", payload)

    def _recover_history_dirs(self, settings: Settings) -> None:
        """Runs in a worker thread, once, before the hasher may start a job."""
        result = sealer.recover_history_dirs(self.db, settings)
        for tmp in result.temp_files:
            self._publish_log("warning", f"stale temp file removed: {tmp}")
        for orphan in result.orphans:
            self._publish_log("warning", f"orphan manifest set aside: {orphan}")
        for error in result.errors:
            self._publish_log("warning", error)
        log.info(
            "startup recovery: %d temp file(s) removed, %d orphan manifest(s) set aside",
            len(result.temp_files),
            len(result.orphans),
        )
        self._fs_recovered.set()
        self._wake.set()  # jobs may run now

    def _scan_and_schedule(
        self, settings: Settings, now: datetime, working: bool
    ) -> tuple[sealer.ScanSummary, list[tuple[str, JobKind]]]:
        """Scan cycle, then verifications (never during working hours, D33) and the root
        manifest job (hito 4). Runs in a worker thread."""
        summary = sealer.run_scan_cycle(self.db, settings, now=now, stop=self.stop_event)
        if self.stop_event.is_set():
            return summary, []
        maintenance = sealer.schedule_maintenance(self.db, settings, now=now, working=working)
        return summary, maintenance

    # -- hasher thread -----------------------------------------------------------------------

    def _hasher_main(self) -> None:
        while not self.stop_event.is_set():
            try:
                ran = self._hasher_step()
            except Exception as exc:  # never let the thread die
                log.exception("hasher step failed")
                self._publish_log("error", f"hasher: {type(exc).__name__}: {exc}")
                ran = False
            if not ran:
                self._wake.wait(self.idle_seconds)
                self._wake.clear()

    def _hasher_step(self) -> bool:
        """Run the next job if the gate is open and the archive answers. True if one ran."""
        if (
            not self.gate.is_set()
            or self._archive_reachable is not True
            or not self._fs_recovered.is_set()
        ):
            return False
        job = self.db.next_job(self.now_fn())
        if job is None:
            return False
        settings = self.settings_ref.get()
        project = self.db.get_project(job.project_id) if job.project_id is not None else None
        rel_path = project.rel_path if project is not None else None
        with self._lock:
            self._current_job_id = job.id
        before = self._project_states()
        self.bus.publish(
            "job.started",
            {
                "id": job.id,
                "kind": str(job.kind),
                "project_id": job.project_id,
                "rel_path": rel_path,
            },
        )
        last_sent = 0.0

        def on_progress(
            files_done: int, files_total: int, bytes_done: int, bytes_total: int
        ) -> None:
            nonlocal last_sent
            now = time.monotonic()
            if files_done not in (0, files_total) and now - last_sent < PROGRESS_MIN_INTERVAL:
                return
            last_sent = now
            self.bus.publish(
                "job.progress",
                {
                    "id": job.id,
                    "project_id": job.project_id,
                    "files_done": files_done,
                    "files_total": files_total,
                    "bytes_done": bytes_done,
                    "bytes_total": bytes_total,
                },
            )

        try:
            sealer.run_job(
                self.db,
                settings,
                job,
                gate=self.gate,
                stop=self.stop_event,
                now_fn=self.now_fn,
                on_progress=on_progress,
            )
        finally:
            with self._lock:
                self._current_job_id = None
            done = self.db.get_job(job.id)
            state = done.state if done is not None else JobState.FAILED
            error = done.error if done is not None else "job vanished"
            kind = "job.failed" if state is JobState.FAILED else "job.finished"
            payload: dict[str, Any] = {
                "id": job.id,
                "kind": str(job.kind),
                "project_id": job.project_id,
                "rel_path": rel_path,
                "state": str(state),
                "error": error,
            }
            if job.kind is JobKind.VERIFY and job.project_id is not None:
                results = self.db.get_verify_results(job.project_id, job.id)
                payload["verify"] = dict(Counter(r.status for r in results))
            self.bus.publish(kind, payload)
            if job.kind is JobKind.ROOT_MANIFEST and state is JobState.DONE:
                at = rootmanifest.last_root_manifest_at(self.db)
                self.bus.publish(rootmanifest.ROOT_UPDATED_EVENT, {"at": at})
            self._publish_state_diff(before, self._project_states())
        return True

    # -- helpers -----------------------------------------------------------------------------

    def _project_states(self) -> dict[int, tuple[str, ProjectState]]:
        return {p.id: (p.rel_path, p.state) for p in self.db.list_projects()}

    def _publish_state_diff(
        self,
        before: dict[int, tuple[str, ProjectState]],
        after: dict[int, tuple[str, ProjectState]],
    ) -> None:
        for pid, (rel_path, state) in after.items():
            old = before.get(pid)
            if old is None or old[1] is not state:
                self.bus.publish(
                    "project.state", {"id": pid, "rel_path": rel_path, "state": str(state)}
                )

    def _publish_log(self, level: str, msg: str) -> None:
        self.bus.publish("log", {"level": level, "msg": msg})
