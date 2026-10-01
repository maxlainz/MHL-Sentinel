"""Supervisor: scheduler loop + hasher thread over a synthetic archive (hito 2)."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import sys
import time
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from mhl_sentinel import sealer
from mhl_sentinel.clock import utcnow
from mhl_sentinel.config import Settings, WorkingHoursConfig
from mhl_sentinel.db import Database, SealedFile
from mhl_sentinel.events import Event, EventBus
from mhl_sentinel.models import JobKind, JobState, ProjectState, Trigger
from mhl_sentinel.settings_ref import SettingsRef
from mhl_sentinel.supervisor import Supervisor

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_fixtures.py"
ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
NOON = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)  # a Thursday


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_fixtures_supervisor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_settings(tmp_path: Path, archive: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "archive_root": archive,
        "config_dir": tmp_path / "config",
        "working_hours": WorkingHoursConfig(days=[]),  # never working: gate open
        "settle_hours": 0,
    }
    base.update(kw)
    return Settings(**base)


def run(body: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(body())


async def until(pred: Callable[[], bool], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


def drain(q: asyncio.Queue[Event]) -> list[Event]:
    out: list[Event] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_cycle_then_queued_seals_run_on_the_hasher_thread(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    generator().build(archive, 1, "small")
    settings = make_settings(tmp_path, archive)

    async def body() -> None:
        with Database(settings.db_path) as db:
            bus = EventBus(db)
            q = bus.subscribe()
            sup = Supervisor(db, SettingsRef(settings), bus, tick_seconds=0.1, idle_seconds=0.05)
            await sup.start()
            try:
                await until(lambda: sup.status().last_cycle_at is not None)
                status = sup.status()
                assert status.gate_open and not status.working_now and status.archive_reachable
                projects = db.list_projects(ProjectState.UNSEALED)
                assert projects and all(p.preexisting for p in projects)
                assert db.list_jobs() == []  # D15: preexisting projects wait for Seal
                for p in projects:  # like run-once --seal-all
                    sealer.request_seal(db, p.id, utcnow())
                sup.notify_job_queued()
                await until(lambda: {p.state for p in db.list_projects()} == {ProjectState.SEALED})
                await until(lambda: sup.status().current_job is None)
            finally:
                await sup.stop()
            assert not sup.running
            events = drain(q)
            kinds = {e.kind for e in events}
            assert {"cycle.started", "cycle.finished", "archive.ok"} <= kinds
            assert {"job.started", "job.progress", "job.finished"} <= kinds
            sealed = {
                e.payload["rel_path"]
                for e in events
                if e.kind == "project.state" and e.payload["state"] == "sealed"
            }
            assert sealed == {p.rel_path for p in projects}
            unsealed = {
                e.payload["rel_path"]
                for e in events
                if e.kind == "project.state" and e.payload["state"] == "unsealed"
            }
            assert unsealed == {p.rel_path for p in projects}  # first discovery
            assert all((archive / p.rel_path / "ascmhl").is_dir() for p in db.list_projects())

    run(body)


def test_stop_mid_job_requeues_without_a_generation(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    project = archive / "2024" / "2024-01_CLIENTE-Z_MUCHOS"
    for d in range(40):
        folder = project / "02_OCF" / f"A{d:03d}"
        folder.mkdir(parents=True)
        for i in range(100):
            (folder / f"clip_{i:04d}.bin").write_bytes(bytes([d, i % 256]) * 2048)
    settings = make_settings(tmp_path, archive)

    async def body() -> None:
        with Database(settings.db_path) as db:
            bus = EventBus(db)
            sup = Supervisor(db, SettingsRef(settings), bus, tick_seconds=0.1, idle_seconds=0.05)
            await sup.start()
            stopped_in = 0.0
            try:
                await until(lambda: db.get_project("2024/2024-01_CLIENTE-Z_MUCHOS") is not None)
                row = db.get_project("2024/2024-01_CLIENTE-Z_MUCHOS")
                assert row is not None
                job_id = sealer.request_seal(db, row.id, utcnow())
                sup.notify_job_queued()

                def hashing() -> bool:
                    job = db.get_job(job_id)
                    return job is not None and job.files_done > 0

                await until(hashing, timeout=60)
            finally:
                t0 = time.monotonic()
                await sup.stop()
                stopped_in = time.monotonic() - t0
            assert stopped_in < 5.0
            job = db.get_job(job_id)
            assert job is not None and job.state is JobState.QUEUED
            assert 0 < job.files_done < job.files_total == 4000
            after = db.get_project(row.id)
            assert after is not None and after.state is ProjectState.QUEUED
            assert not (project / "ascmhl").exists()  # no partial generation

    run(body)


def test_working_hours_close_the_gate_but_allow_a_manual_scan(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    generator().build(archive, 1, "small")
    settings = make_settings(
        tmp_path,
        archive,
        working_hours=WorkingHoursConfig(days=ALL_DAYS, start="09:00", end="19:00"),
    )

    async def body() -> None:
        with Database(settings.db_path) as db:
            bus = EventBus(db)
            q = bus.subscribe()
            sup = Supervisor(
                db,
                SettingsRef(settings),
                bus,
                tick_seconds=0.05,
                idle_seconds=0.05,
                now_fn=lambda: NOON,
            )
            await sup.start()
            try:
                await asyncio.sleep(0.5)
                status = sup.status()
                assert status.working_now and not status.gate_open
                assert status.last_cycle_at is None and db.list_projects() == []
                cycles = [e for e in drain(q) if e.kind == "cycle.finished"]
                assert cycles and not any(e.payload["scan"] for e in cycles)

                sup.request_scan_now()  # D26: allowed inside working hours, scan only
                await until(lambda: sup.status().last_cycle_at is not None)
                assert not sup.status().scan_requested
                target = db.list_projects(ProjectState.UNSEALED)[0]
                job_id = sealer.request_seal(db, target.id, utcnow())
                sup.notify_job_queued()
                await asyncio.sleep(0.5)
                job = db.get_job(job_id)
                assert job is not None and job.state is JobState.QUEUED and job.files_done == 0
                assert sup.status().queued_jobs == 1
                assert not (archive / target.rel_path / "ascmhl").exists()
            finally:
                await sup.stop()

    run(body)


def test_settings_replaced_apply_on_the_next_tick(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    working = WorkingHoursConfig(days=ALL_DAYS, start="09:00", end="19:00")
    settings = make_settings(tmp_path, archive, working_hours=working)

    async def body() -> None:
        with Database(settings.db_path) as db:
            ref = SettingsRef(settings)
            sup = Supervisor(
                db, ref, EventBus(db), tick_seconds=0.05, idle_seconds=0.05, now_fn=lambda: NOON
            )
            await sup.start()
            try:
                await asyncio.sleep(0.2)
                assert not sup.gate.is_set()
                ref.replace(
                    settings.model_copy(update={"working_hours": WorkingHoursConfig(days=[])})
                )
                await until(sup.gate.is_set, timeout=5)
            finally:
                await sup.stop()

    run(body)


def test_tick_errors_are_published_and_do_not_kill_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    settings = make_settings(tmp_path, archive)
    real = sealer.run_scan_cycle
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> sealer.ScanSummary:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(sealer, "run_scan_cycle", flaky)

    async def body() -> None:
        with Database(settings.db_path) as db:
            bus = EventBus(db)
            q = bus.subscribe()
            sup = Supervisor(db, SettingsRef(settings), bus, tick_seconds=0.05, idle_seconds=0.05)
            await sup.start()
            try:
                await until(lambda: sup.status().last_cycle_at is not None, timeout=10)
            finally:
                await sup.stop()
            logs = [e for e in drain(q) if e.kind == "log"]
            assert any(e.payload["level"] == "error" and "boom" in e.payload["msg"] for e in logs)

    run(body)


def test_unreachable_archive_publishes_and_blocks_jobs(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, tmp_path / "not-mounted")

    async def body() -> None:
        with Database(settings.db_path) as db:
            job_id = db.enqueue_job(JobKind.SEAL, None, Trigger.MANUAL, 130, utcnow())
            db.set_job_state(job_id, JobState.RUNNING, utcnow())  # left running by a crash
            bus = EventBus(db)
            q = bus.subscribe()
            sup = Supervisor(db, SettingsRef(settings), bus, tick_seconds=0.05, idle_seconds=0.05)
            await sup.start()
            try:
                await asyncio.sleep(0.4)
                assert not sup.status().archive_reachable
                job = db.get_job(job_id)
                assert job is not None and job.state is JobState.QUEUED  # recovered, not run
            finally:
                await sup.stop()
            kinds = [e.kind for e in drain(q)]
            assert "archive.unreachable" in kinds and "job.started" not in kinds

    run(body)


def test_cancel_aborts_the_running_manual_seal(tmp_path: Path) -> None:
    """D57: Cancel on a running Seal ends the job ``cancelled`` and the project ``unsealed``;
    other projects, verifications and an idle hasher are refused."""
    archive = tmp_path / "archive"
    project = archive / "2024" / "2024-01_CLIENTE-Z_MUCHOS"
    for d in range(40):
        folder = project / "02_OCF" / f"A{d:03d}"
        folder.mkdir(parents=True)
        for i in range(100):
            (folder / f"clip_{i:04d}.bin").write_bytes(bytes([d, i % 256]) * 2048)
    (archive / "2024" / "2024-02_CLIENTE-OTRO").mkdir(parents=True)
    (archive / "2024" / "2024-02_CLIENTE-OTRO" / "a.mov").write_bytes(b"x" * 10)
    settings = make_settings(tmp_path, archive)

    async def body() -> None:
        with Database(settings.db_path) as db:
            bus = EventBus(db)
            q = bus.subscribe()
            sup = Supervisor(db, SettingsRef(settings), bus, tick_seconds=0.1, idle_seconds=0.05)
            await sup.start()
            try:
                await until(lambda: len(db.list_projects()) == 2)
                row = db.get_project("2024/2024-01_CLIENTE-Z_MUCHOS")
                other = db.get_project("2024/2024-02_CLIENTE-OTRO")
                assert row is not None and other is not None
                assert not sup.request_cancel(row.id)  # nothing running
                job_id = sealer.request_seal(db, row.id, utcnow())
                sup.notify_job_queued()

                def hashing() -> bool:
                    job = db.get_job(job_id)
                    return job is not None and job.files_done > 0

                await until(hashing, timeout=60)
                assert not sup.request_cancel(other.id)
                assert sup.request_cancel(row.id)
                await until(lambda: sup.status().current_job is None, timeout=10)
                job = db.get_job(job_id)
                assert job is not None and job.state is JobState.CANCELLED
                assert 0 < job.files_done < job.files_total == 4000
                after = db.get_project(row.id)
                assert after is not None and after.state is ProjectState.UNSEALED
                assert not (project / "ascmhl").exists()

                # A verification or an automatic append, if it were running, is never cancelled.
                for kind, trigger in (
                    (JobKind.VERIFY, Trigger.MANUAL),
                    (JobKind.SEAL, Trigger.AUTO),
                ):
                    with sup._lock:
                        sup._current_job = dataclasses.replace(job, kind=kind, trigger=trigger)
                    try:
                        assert not sup.request_cancel(row.id)
                    finally:
                        with sup._lock:
                            sup._current_job = None
            finally:
                await sup.stop()
            events = drain(q)
            finished = [e for e in events if e.kind == "job.finished"]
            assert [(e.payload["id"], e.payload["state"]) for e in finished] == [
                (job_id, "cancelled")
            ]
            states = [
                e.payload["state"]
                for e in events
                if e.kind == "project.state" and e.payload["id"] == row.id
            ]
            assert states[-1] == "unsealed"

    run(body)


def test_a_failing_job_is_published_as_job_failed(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    settings = make_settings(tmp_path, archive)

    async def body() -> None:
        with Database(settings.db_path) as db:
            job_id = db.enqueue_job(JobKind.VERIFY, None, Trigger.AUTO, 10, utcnow())
            bus = EventBus(db)
            q = bus.subscribe()
            sup = Supervisor(db, SettingsRef(settings), bus, tick_seconds=0.05, idle_seconds=0.05)
            await sup.start()
            try:
                await until(lambda: db.count_jobs(JobState.QUEUED) == 0, timeout=10)
            finally:
                await sup.stop()
            failed = [e for e in drain(q) if e.kind == "job.failed"]
            assert [e.payload["id"] for e in failed] == [job_id]

    run(body)


def test_scan_cycle_schedules_verifications_and_the_root_manifest(tmp_path: Path) -> None:
    """Hito 4: after a scan outside working hours, due projects get ``verify`` and the root gets
    its references-only generation; ``root.updated`` and the verify counts are published."""
    archive = tmp_path / "archive"
    generator().build(archive, 1, "small")
    settings = make_settings(tmp_path, archive, verify_interval_days=1)

    async def body() -> None:
        with Database(settings.db_path) as db:
            bus = EventBus(db)
            q = bus.subscribe()
            sup = Supervisor(db, SettingsRef(settings), bus, tick_seconds=0.1, idle_seconds=0.05)
            await sup.start()
            try:
                await until(lambda: sup.status().last_cycle_at is not None)
                for p in db.list_projects(ProjectState.UNSEALED):
                    sealer.request_seal(db, p.id, utcnow())
                sup.notify_job_queued()
                await until(lambda: {p.state for p in db.list_projects()} == {ProjectState.SEALED})
                await until(lambda: db.count_jobs(JobState.QUEUED) == 0)
                old = utcnow() - timedelta(days=2)
                for p in db.list_projects():
                    db.update_project_fields(p.id, last_sealed_at=old)
                sup.request_scan_now()
                await until(lambda: (archive / "ascmhl" / "ascmhl_chain.xml").is_file(), 60)
                await until(
                    lambda: (
                        all(p.last_verified_at for p in db.list_projects())
                        and db.count_jobs(JobState.QUEUED) == 0
                        and sup.status().current_job is None
                    ),
                    60,
                )
            finally:
                await sup.stop()
            events = drain(q)
            verified = [
                e.payload
                for e in events
                if e.kind == "job.finished" and e.payload["kind"] == "verify"
            ]
            assert len(verified) == len(db.list_projects())
            assert all(set(p["verify"]) == {"ok"} for p in verified)
            assert any(e.kind == "root.updated" for e in events)
            finished = [
                e.payload for e in events if e.kind == "cycle.finished" and e.payload["scan"]
            ]
            assert any(p.get("root_enqueued") for p in finished)
            assert db.get_kv("last_root_manifest_at") is not None

    run(body)


def test_manual_scan_in_working_hours_schedules_no_verification(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    generator().build(archive, 1, "small")
    working = WorkingHoursConfig(days=ALL_DAYS, start="09:00", end="19:00")
    settings = make_settings(tmp_path, archive, working_hours=working, verify_interval_days=1)

    async def body() -> None:
        with Database(settings.db_path) as db:
            sup = Supervisor(
                db,
                SettingsRef(settings),
                EventBus(db),
                tick_seconds=0.05,
                idle_seconds=0.05,
                now_fn=lambda: NOON,
            )
            sealer.run_scan_cycle(db, settings, now=NOON)
            for p in db.list_projects():  # pretend they were sealed long ago
                db.update_project_fields(
                    p.id, last_generation_no=1, last_sealed_at=NOON - timedelta(days=5)
                )
                db.set_state(p.id, ProjectState.SEALED)
                db.replace_sealed_files(
                    p.id,
                    [
                        SealedFile(f.rel_path, f.size, f.mtime_ns, None)
                        for f in db.get_files(p.id).values()
                    ],
                )
            await sup.start()
            try:
                sup.request_scan_now()
                await until(lambda: sup.status().last_cycle_at is not None)
                await asyncio.sleep(0.2)
            finally:
                await sup.stop()
            assert {p.state for p in db.list_projects()} == {ProjectState.SEALED}
            assert [j for j in db.list_jobs() if j.kind is JobKind.VERIFY] == []
            # Outside working hours the same projects are due (the gate is the only difference).
            evening = NOON.replace(hour=22)
            assert sealer.schedule_verifications(db, settings, now=evening, working=False)

    run(body)
