"""Command line: gate, error paths and output of ``run-once``, ``settings``, ``projects``."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from mhl_sentinel import cli as cli_module
from mhl_sentinel import sealer
from mhl_sentinel.cli import WorkingHoursGate, _force_utc, main
from mhl_sentinel.config import Settings, WorkingHoursConfig, save_yaml
from mhl_sentinel.db import Database, NetworkFilesystemError
from mhl_sentinel.models import JobState, ProjectState
from mhl_sentinel.schedule import WorkingHours

REL = "2025/2025-01_CLIENTE-CAMPANA"
THURSDAY_NOON = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)  # inside the default working hours
THURSDAY_NIGHT = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith("MHLS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, create: bool = True) -> Path:
    archive = tmp_path / "archive"
    if create:
        clip = archive / REL / "01_MASTERS" / "master.mov"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"master" * 1000)
    monkeypatch.setenv("MHLS_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("MHLS_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path / "config"


def invoke(*args: str, code: int = 0) -> Result:
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == code, result.output
    return result


def project_state(config: Path, rel: str = REL) -> ProjectState:
    with Database(config / "state.db") as db:
        project = db.get_project(rel)
        assert project is not None
        return project.state


# -- WorkingHoursGate ----------------------------------------------------------------------------


def hours(days: list[str], start: str = "09:00", end: str = "19:00") -> WorkingHours:
    return WorkingHours.from_settings(
        Settings(working_hours=WorkingHoursConfig(days=days, start=start, end=end))
    )


def test_gate_without_hours_is_always_open() -> None:
    stop = threading.Event()
    gate = WorkingHoursGate(None, stop)
    assert gate.is_set() is True and gate.wait(1.0) is True
    assert not stop.is_set()


def test_gate_open_outside_working_hours() -> None:
    stop = threading.Event()
    gate = WorkingHoursGate(hours(["thu"]), stop, now_fn=lambda: THURSDAY_NIGHT)
    assert gate.is_set() is True
    assert not stop.is_set()


def test_gate_closed_inside_working_hours_sets_stop() -> None:
    stop = threading.Event()
    gate = WorkingHoursGate(hours(["thu"]), stop, now_fn=lambda: THURSDAY_NOON)
    assert gate.wait(None) is False
    assert stop.is_set()


# -- helpers -------------------------------------------------------------------------------------


def test_force_utc_sets_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/Madrid")
    time.tzset()
    _force_utc()
    assert os.environ["TZ"] == "UTC"
    assert time.strftime("%z", time.localtime(0)) == "+0000"


def test_force_utc_leaves_utc_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    _force_utc()
    assert os.environ["TZ"] == "UTC"


def test_open_db_reports_network_filesystem_as_click_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(tmp_path, monkeypatch)

    def refuse(self: Database) -> Database:
        raise NetworkFilesystemError("refusing to open the state database on nfs")

    monkeypatch.setattr(Database, "open", refuse)
    result = invoke("projects", "list", code=1)
    assert "Error: refusing to open the state database on nfs" in result.output


# -- run-once ------------------------------------------------------------------------------------


def test_run_once_reports_scan_and_seal_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = setup(tmp_path, monkeypatch)
    out = invoke("run-once").output
    assert "state database open (schema v" in out and "0 job(s) requeued" in out
    assert "scan: 1 projects (1 new, 0 missing), 1 scanned, 0 queued automatically" in out
    assert project_state(config) is ProjectState.UNSEALED

    out = invoke("run-once", "--seal-all", "--ignore-working-hours").output
    assert f"job 1 seal {REL}: done → sealed" in out
    assert "root_manifest . : done" not in out
    assert "root_manifest ." in out
    assert project_state(config) is ProjectState.SEALED
    assert list((tmp_path / "archive" / REL / "ascmhl").glob("*.mhl"))


def test_run_once_with_missing_archive_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(tmp_path, monkeypatch, create=False)
    result = invoke("run-once", code=1)
    assert result.output.startswith("state database open")
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_run_once_prints_recovery_and_scan_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(tmp_path, monkeypatch)
    real_scan = sealer.run_scan_cycle

    def fake_recovery(db: Database, settings: Settings) -> sealer.FsRecovery:
        return sealer.FsRecovery(
            temp_files=["a/.0001.tmp"], orphans=["b/0003.mhl"], errors=["c: unreadable"]
        )

    def fake_scan(db: Database, settings: Settings, *, now: datetime) -> sealer.ScanSummary:
        summary = real_scan(db, settings, now=now)
        summary.orphans.append("d/0002.mhl")
        summary.errors.append("e: cannot list")
        return summary

    monkeypatch.setattr(sealer, "recover_history_dirs", fake_recovery)
    monkeypatch.setattr(sealer, "run_scan_cycle", fake_scan)
    out = invoke("run-once").output
    assert "stale temp file removed: a/.0001.tmp" in out
    assert "orphan manifest set aside: b/0003.mhl" in out
    assert "error: c: unreadable" in out
    assert "orphan manifest set aside: d/0002.mhl" in out
    assert "error: e: cannot list" in out


def test_run_once_leaves_jobs_for_later_inside_working_hours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "utcnow", lambda: THURSDAY_NOON)
    # The gate binds ``utcnow`` as a default argument: give it the same frozen clock.
    monkeypatch.setattr(
        cli_module,
        "WorkingHoursGate",
        lambda hrs, stop: WorkingHoursGate(hrs, stop, now_fn=lambda: THURSDAY_NOON),
    )
    out = invoke("run-once", "--seal-all").output
    assert "working hours: queued jobs left for later" in out
    assert project_state(config) is ProjectState.QUEUED
    with Database(config / "state.db") as db:
        assert [j.state for j in db.list_jobs()] == [JobState.QUEUED]


def test_run_once_stops_when_a_job_goes_back_to_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job stopped by the gate returns to ``queued`` and ends the run."""
    config = setup(tmp_path, monkeypatch)
    real_run_job = sealer.run_job
    ran: list[int] = []

    def stopped_job(db: Database, settings: Settings, job: Any, *, gate: Any, stop: Any) -> None:
        ran.append(job.id)
        stop.set()  # as WorkingHoursGate does when working hours begin mid-job
        real_run_job(db, settings, job, gate=gate, stop=stop)

    monkeypatch.setattr(sealer, "run_job", stopped_job)
    out = invoke("run-once", "--seal-all", "--ignore-working-hours").output
    assert ran == [1]
    assert f"job 1 seal {REL}: queued" in out
    with Database(config / "state.db") as db:
        job = db.get_job(1)
        assert job is not None and job.state is JobState.QUEUED
    assert "root_manifest" not in out


# -- settings / projects -------------------------------------------------------------------------


def test_settings_show_prints_effective_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = setup(tmp_path, monkeypatch)
    config.mkdir()
    save_yaml(Settings(settle_hours=24, config_dir=config), config / "config.yaml")
    monkeypatch.setenv("MHLS_VERIFY_INTERVAL_DAYS", "30")
    out = invoke("settings", "show").output
    assert "settle_hours: 24" in out
    assert "verify_interval_days: 30" in out
    assert f"config_dir: {config}" in out


def test_projects_list_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup(tmp_path, monkeypatch)
    assert invoke("projects", "list").output.splitlines()[0].split() == [
        "STATE", "FILES", "BYTES", "LAST", "SEALED", "PATH",
    ]  # fmt: skip
    invoke("run-once")
    lines = invoke("projects", "list").output.splitlines()
    assert lines[1].split() == ["unsealed", "1", "6000", "-", REL]
    invoke("projects", "seal", REL)
    invoke("run-once", "--ignore-working-hours")
    row = invoke("projects", "list").output.splitlines()[1].split()
    assert row[0] == "sealed" and row[-1] == REL
    assert row[3].startswith("20") and "T" in row[3]  # last sealed, to the second


def test_projects_actions_drive_the_state_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = setup(tmp_path, monkeypatch)
    invoke("run-once")
    assert invoke("projects", "seal", REL).output == f"{REL}: queued\n"
    assert invoke("projects", "ignore", REL).output == f"{REL}: ignored\n"
    assert project_state(config) is ProjectState.IGNORED
    assert invoke("projects", "unignore", REL).output == f"{REL}: unsealed\n"


def test_projects_action_on_unknown_project_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(tmp_path, monkeypatch)
    result = invoke("projects", "seal", "2025/NOPE", code=1)
    assert "unknown project '2025/NOPE' (run run-once first)" in result.output


@pytest.mark.parametrize("action", ["accept", "postpone", "unignore"])
def test_projects_action_in_wrong_state_reports_sealer_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    setup(tmp_path, monkeypatch)
    invoke("run-once")
    result = invoke("projects", action, REL, code=1)
    assert result.output.startswith("Error: ") and REL in result.output
    assert "Traceback" not in result.output


# -- serve ---------------------------------------------------------------------------------------


def test_serve_builds_app_and_runs_server_with_supervisor_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mhl_sentinel import server

    setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MHLS_PORT", "8765")
    monkeypatch.setenv("MHLS_LOG_LEVEL", "warning")
    seen: dict[str, Any] = {}

    class FakeServer:
        def run(self) -> None:
            seen["ran"] = True

    def fake_make_server(app: Any, settings: Settings, **kw: Any) -> FakeServer:
        seen.update(app=app, port=settings.port, **kw)
        return FakeServer()

    monkeypatch.setattr(server, "make_server", fake_make_server)
    monkeypatch.setattr(server, "configure_logging", lambda level: seen.update(level=level))
    invoke("serve", "--host", "127.0.0.1")
    assert seen["ran"] is True
    assert (seen["host"], seen["port"], seen["level"]) == ("127.0.0.1", 8765, "warning")
    assert callable(seen["on_stop"]) and seen["on_stop"].__name__ == "request_stop"
    assert seen["app"].router.lifespan_context is not None


def test_run_once_queues_and_runs_due_verifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(tmp_path, monkeypatch)
    invoke("run-once", "--seal-all", "--ignore-working-hours")
    later = datetime.now(UTC) + timedelta(days=200)
    monkeypatch.setattr(cli_module, "utcnow", lambda: later)
    out = invoke("run-once", "--ignore-working-hours").output
    assert f"queued verify {REL}" in out
    assert f"verify {REL}: done → sealed" in out
