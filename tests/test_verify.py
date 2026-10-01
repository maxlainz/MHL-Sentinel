"""Periodic verification (hito 4, D23, D8): the ``verify`` job and its staggered scheduling.

Every generation written here is checked by the reference as a real CLI process (norm
``conformidad-mhl.md``). The mtime rule is the one of the vault note
`Distinguir corrupción de modificación por mtime`.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lxml import etree

from helpers_ascmhl import assert_xsd_valid, run_cli
from mhl_sentinel import sealer
from mhl_sentinel.clock import utcnow
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, ProjectRow
from mhl_sentinel.models import ChangeKind, JobKind, JobState, ProjectState, Trigger

NS = {"m": "urn:ASC:MHL:v2.0"}
REL = "2025/2025-01_CLIENTE-CAMPANA"
MASTER = "01_MASTERS/master.mov"


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def open_gate() -> threading.Event:
    gate = threading.Event()
    gate.set()
    return gate


def run_all_jobs(db: Database, settings: Settings) -> None:
    while (job := db.next_job(utcnow())) is not None:
        sealer.run_job(db, settings, job, gate=open_gate(), stop=threading.Event())


@pytest.fixture
def sealed(tmp_path: Path) -> Iterator[tuple[Settings, Database, ProjectRow]]:
    root = tmp_path / "archive"
    project = root / REL
    (project / "01_MASTERS").mkdir(parents=True)
    (project / "03_GRADE").mkdir()
    (project / MASTER).write_bytes(b"master" * 5000)
    (project / "03_GRADE" / "grade.drx").write_bytes(b"grade" * 300)
    settings = Settings(archive_root=root, config_dir=tmp_path / "config")
    with Database(settings.db_path) as db:
        sealer.run_scan_cycle(db, settings, now=utcnow())
        row = db.get_project(REL)
        assert row is not None
        sealer.request_seal(db, row.id, utcnow())
        run_all_jobs(db, settings)
        db.set_kv(sealer.ROOT_MANIFEST_STALE_KEY, "0")
        after = db.get_project(row.id)
        assert after is not None and after.state is ProjectState.SEALED
        yield settings, db, after


def manifests(project: Path) -> list[Path]:
    return sorted((project / "ascmhl").glob("*.mhl"))


def verify_job(db: Database, settings: Settings, project_id: int) -> int:
    time.sleep(1.1)  # distinct generation file names (seconds in the name)
    job_id = sealer.enqueue(db, project_id, JobKind.VERIFY, Trigger.AUTO, utcnow())
    job = db.get_job(job_id)
    assert job is not None
    sealer.run_job(db, settings, job, gate=open_gate(), stop=threading.Event())
    return job_id


def actions(manifest: Path) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for h in etree.parse(str(manifest)).findall("m:hashes/m:hash", NS):
        xxh = h.find("m:xxh128", NS)
        assert xxh is not None
        out[str(h.findtext("m:path", namespaces=NS))] = xxh.get("action")
    return out


def flip_last_byte_keep_mtime(path: Path) -> None:
    st = path.stat()
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01
    path.write_bytes(bytes(data))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_untouched_project_gets_a_verified_generation(
    sealed: tuple[Settings, Database, ProjectRow],
) -> None:
    settings, db, project = sealed
    root = settings.archive_root / REL
    job_id = verify_job(db, settings, project.id)

    job = db.get_job(job_id)
    assert job is not None and job.state is JobState.DONE, job
    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.SEALED
    assert after.last_verified_at is not None and after.last_generation_no == 2
    assert after.last_sealed_at == project.last_sealed_at  # a verification is not a seal
    assert {r.status for r in db.get_verify_results(project.id)} == {"ok"}
    assert db.get_kv(sealer.ROOT_MANIFEST_STALE_KEY) == "1"

    gen1, gen2 = manifests(root)
    assert set(actions(gen1).values()) == {"original"}
    assert actions(gen2) == {MASTER: "verified", "03_GRADE/grade.drx": "verified"}
    assert_xsd_valid(gen2)
    result = run_cli("ascmhl-debug", "verify", root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_added_file_is_fine_and_original(sealed: tuple[Settings, Database, ProjectRow]) -> None:
    settings, db, project = sealed
    root = settings.archive_root / REL
    (root / "05_DELIVERABLES").mkdir()
    (root / "05_DELIVERABLES" / "spot.mp4").write_bytes(b"spot" * 100)
    verify_job(db, settings, project.id)

    statuses = {r.rel_path: r.status for r in db.get_verify_results(project.id)}
    assert statuses["05_DELIVERABLES/spot.mp4"] == "added"
    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.SEALED
    gen2 = manifests(root)[-1]
    assert actions(gen2)["05_DELIVERABLES/spot.mp4"] == "original"
    assert actions(gen2)[MASTER] == "verified"
    assert "05_DELIVERABLES/spot.mp4" in db.get_sealed_files(project.id)
    result = run_cli("ascmhl-debug", "verify", root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_bit_flip_with_the_same_mtime_is_corrupt(
    sealed: tuple[Settings, Database, ProjectRow],
) -> None:
    settings, db, project = sealed
    root = settings.archive_root / REL
    flip_last_byte_keep_mtime(root / MASTER)  # the hash cache from the seal would still match
    verify_job(db, settings, project.id)

    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.NEEDS_REVIEW
    assert after.review_reason is not None
    assert after.review_reason.startswith("verification: 1 corrupt"), after.review_reason
    assert after.last_verified_at is None
    results = {r.rel_path: r for r in db.get_verify_results(project.id)}
    assert results[MASTER].status == "corrupt"
    assert results[MASTER].expected != results[MASTER].actual
    assert results["03_GRADE/grade.drx"].status == "ok"
    assert [(i.rel_path, i.change) for i in db.get_review_items(project.id)] == [
        (MASTER, ChangeKind.MODIFIED)
    ]
    assert len(manifests(root)) == 1  # nothing written

    # The next scan keeps the verification's review (same size and mtime: no scan diff).
    sealer.run_scan_cycle(db, settings, now=utcnow())
    kept = db.get_project(project.id)
    assert kept is not None and kept.review_reason == after.review_reason
    assert [i.rel_path for i in db.get_review_items(project.id)] == [MASTER]

    # Accept as new version: the fresh read is reused, the new history verifies.
    sealer.request_accept_new_version(db, project.id, utcnow())
    run_all_jobs(db, settings)
    accepted = db.get_project(project.id)
    assert accepted is not None and accepted.state is ProjectState.SEALED
    result = run_cli("ascmhl-debug", "verify", root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_modified_with_a_new_mtime_and_deleted(
    sealed: tuple[Settings, Database, ProjectRow],
) -> None:
    settings, db, project = sealed
    root = settings.archive_root / REL
    master = root / MASTER
    st = master.stat()
    master.write_bytes(b"other" * 6000)
    os.utime(master, ns=(st.st_atime_ns, st.st_mtime_ns + 60 * 1_000_000_000))
    (root / "03_GRADE" / "grade.drx").unlink()
    verify_job(db, settings, project.id)

    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.NEEDS_REVIEW
    assert after.review_reason is not None
    assert after.review_reason.startswith("verification: 1 modified, 1 missing")
    statuses = {r.rel_path: r.status for r in db.get_verify_results(project.id)}
    assert statuses == {MASTER: "modified", "03_GRADE/grade.drx": "missing"}
    items = {i.rel_path: i.change for i in db.get_review_items(project.id)}
    assert items == {MASTER: ChangeKind.MODIFIED, "03_GRADE/grade.drx": ChangeKind.DELETED}
    assert len(manifests(root)) == 1


def test_verify_is_skipped_when_the_project_left_sealed(
    sealed: tuple[Settings, Database, ProjectRow],
) -> None:
    settings, db, project = sealed
    db.set_state(project.id, ProjectState.CHANGED)
    job_id = sealer.enqueue(db, project.id, JobKind.VERIFY, Trigger.AUTO, utcnow())
    job = db.get_job(job_id)
    assert job is not None
    sealer.run_job(db, settings, job, gate=open_gate(), stop=threading.Event())
    done = db.get_job(job_id)
    assert done is not None and done.state is JobState.CANCELLED
    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.CHANGED


def test_stopped_verify_leaves_the_project_sealed(
    sealed: tuple[Settings, Database, ProjectRow],
) -> None:
    settings, db, project = sealed
    job_id = sealer.enqueue(db, project.id, JobKind.VERIFY, Trigger.AUTO, utcnow())
    job = db.get_job(job_id)
    assert job is not None
    stop = threading.Event()
    stop.set()
    sealer.run_job(db, settings, job, gate=open_gate(), stop=stop)
    assert db.get_job(job_id).state is JobState.QUEUED  # type: ignore[union-attr]
    assert db.get_project(project.id).state is ProjectState.SEALED  # type: ignore[union-attr]
    assert len(manifests(settings.archive_root / REL)) == 1


# --- scheduling (D23: staggered, never in working hours) ----------------------------------------

DAY = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


def make_sealed_rows(db: Database, n: int, sealed_days_ago: list[int]) -> list[int]:
    ids = []
    for i in range(n):
        pid = db.upsert_project(f"2024/2024-{i + 1:02d}_CLIENTE", f"P{i}", True, DAY)
        db.update_project_fields(
            pid,
            last_generation_no=1,
            last_sealed_at=DAY - timedelta(days=sealed_days_ago[i]),
        )
        db.set_state(pid, ProjectState.SEALED)
        ids.append(pid)
    return ids


def queued_verify(db: Database) -> list[int | None]:
    return [
        j.project_id
        for j in db.list_jobs()
        if j.kind is JobKind.VERIFY and j.state is JobState.QUEUED
    ]


def test_scheduler_staggers_and_respects_working_hours(tmp_path: Path) -> None:
    settings = Settings(
        archive_root=tmp_path / "archive", config_dir=tmp_path / "c", verify_interval_days=2
    )
    with Database(settings.db_path) as db:
        # 5 sealed projects, interval 2 days → at most ceil(5 / 2) = 3 a day.
        ids = make_sealed_rows(db, 5, [10, 3, 9, 1, 30])
        assert sealer.verify_daily_cap(5, 2) == 3

        assert sealer.schedule_verifications(db, settings, now=DAY, working=True) == []
        assert queued_verify(db) == []

        got = sealer.schedule_verifications(db, settings, now=DAY, working=False)
        # Due: sealed >= 2 days ago (10, 3, 9, 30), oldest first, capped at 3.
        assert got == [
            "2024/2024-05_CLIENTE",
            "2024/2024-01_CLIENTE",
            "2024/2024-03_CLIENTE",
        ]
        assert db.get_job(db.list_jobs()[0].id).priority == 10  # type: ignore[union-attr]
        # Same day: the cap is spent even if the jobs already ran.
        for job in db.list_jobs():
            db.set_job_state(job.id, JobState.DONE, DAY)
        assert sealer.schedule_verifications(db, settings, now=DAY, working=False) == []

        # Next day the counter resets; verified projects are not due any more.
        for pid in (ids[4], ids[0], ids[2]):
            db.update_project_fields(pid, last_verified_at=DAY)
        nxt = DAY + timedelta(days=1)
        assert sealer.schedule_verifications(db, settings, now=nxt, working=False) == [
            "2024/2024-02_CLIENTE",  # sealed 4 days before
            "2024/2024-04_CLIENTE",  # sealed 2 days before: just due
        ]
        # A project already waiting for its verify is not enqueued or counted twice.
        assert sealer.schedule_verifications(db, settings, now=nxt, working=False) == []
        assert sorted(queued_verify(db)) == [ids[1], ids[3]]  # type: ignore[type-var]


def test_schedule_maintenance_never_verifies_in_working_hours(tmp_path: Path) -> None:
    settings = Settings(archive_root=tmp_path / "archive", config_dir=tmp_path / "c")
    with Database(settings.db_path) as db:
        make_sealed_rows(db, 2, [200, 200])
        # No history on disk: no root manifest job either.
        assert sealer.schedule_maintenance(db, settings, now=DAY, working=True) == []
        out = sealer.schedule_maintenance(db, settings, now=DAY, working=False)
        assert [k for _, k in out] == [JobKind.VERIFY]  # ceil(2 / 90) = 1 a day
