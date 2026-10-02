"""Vanished project folders (D58 to D63): ``missing``, the history mirror, Retire, Retry, moved
folders and Verify now, at the ``sealer`` level. Generations are checked by the reference."""

from __future__ import annotations

import io
import shutil
import threading
import time
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import xxhash

from helpers_ascmhl import run_cli
from mhl_sentinel import discovery, history_mirror, rootmanifest, sealer
from mhl_sentinel.clock import to_iso, utcnow
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, VerifyResult
from mhl_sentinel.mhlwriter import write_project_generation
from mhl_sentinel.models import JobKind, JobState, ProjectState, Trigger

A = "2025/2025-01_CLIENTE-CAMPANA"
B = "2025/2025-02_CLIENTE-OTRA"
CHAIN = "ascmhl_chain.xml"


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


def run_all_jobs(db: Database, settings: Settings, cancel: threading.Event | None = None) -> None:
    while (job := db.next_job(utcnow())) is not None:
        sealer.run_job(db, settings, job, gate=open_gate(), stop=threading.Event(), cancel=cancel)


def make_archive(tmp_path: Path) -> tuple[Settings, Database]:
    root = tmp_path / "archive"
    for rel in (A, B):
        (root / rel / "01_MASTERS").mkdir(parents=True)
        (root / rel / "01_MASTERS" / "m.mov").write_bytes(rel.encode() * 100)
    settings = Settings(archive_root=root, config_dir=tmp_path / "config", settle_hours=0)
    return settings, Database(settings.db_path).open()


def seal(db: Database, settings: Settings, rel: str) -> int:
    project = db.get_project(rel)
    assert project is not None
    sealer.request_seal(db, project.id, utcnow())
    run_all_jobs(db, settings)
    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.SEALED
    return project.id


@pytest.fixture
def archive(tmp_path: Path) -> Iterator[tuple[Settings, Database]]:
    """Two preexisting projects; A sealed (and mirrored), B unsealed."""
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    seal(db, settings, A)
    yield settings, db
    db.close()


def mirror_of(settings: Settings, rel: str) -> Path:
    return history_mirror.mirror_dir(settings, rel)


def reference_verifies(folder: Path) -> None:
    result = run_cli("ascmhl-debug", "verify", folder)
    assert result.returncode == 0, result.stdout + result.stderr


# --- D58: missing ------------------------------------------------------------------------------


def test_folder_gone_is_missing_from_the_first_scan(
    archive: tuple[Settings, Database], tmp_path: Path
) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    db.update_project_fields(a.id, review_reason="kept as it was")
    verify_id = sealer.enqueue(db, a.id, JobKind.VERIFY, Trigger.AUTO, utcnow())
    shutil.move(settings.archive_root / A, tmp_path / "away")

    t1 = datetime(2026, 10, 2, 22, 0, tzinfo=UTC)
    summary = sealer.run_scan_cycle(db, settings, now=t1)
    assert summary.missing == [A] and summary.states[A] is ProjectState.MISSING
    gone = db.get_project(A)
    assert gone is not None and gone.state is ProjectState.MISSING and gone.error is None
    assert gone.missing_since == to_iso(t1)
    assert gone.state_before_missing is ProjectState.SEALED
    assert gone.review_reason == "kept as it was" and gone.last_generation_no == 1
    assert db.get_sealed_files(gone.id)  # nothing of what the manifest says is lost
    verify = db.get_job(verify_id)
    assert verify is not None and verify.state is JobState.CANCELLED

    # Later scans do not move the date; the project leaves verification and the root manifest.
    sealer.run_scan_cycle(db, settings, now=t1 + timedelta(days=3))
    again = db.get_project(A)
    assert again is not None and again.missing_since == to_iso(t1)
    db.update_project_fields(a.id, last_sealed_at=t1 - timedelta(days=400))
    assert sealer.schedule_verifications(db, settings, now=t1, working=False) == []
    assert rootmanifest.referenced_projects(db, settings) == []
    # The check is the state, not only the folder: with the folder back but not rescanned yet.
    shutil.move(tmp_path / "away", settings.archive_root / A)
    assert rootmanifest.referenced_projects(db, settings) == []
    assert sealer.schedule_verifications(db, settings, now=t1, working=False) == []


def test_queued_project_that_vanishes_remembers_its_resting_state(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    b = db.get_project(B)
    assert b is not None
    job_id = sealer.request_seal(db, b.id, utcnow())
    shutil.rmtree(settings.archive_root / B)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    gone = db.get_project(B)
    assert gone is not None and gone.state is ProjectState.MISSING
    assert gone.state_before_missing is ProjectState.UNSEALED  # not "queued"
    job = db.get_job(job_id)
    assert job is not None and job.state is JobState.CANCELLED


@pytest.mark.parametrize("leave_year_folder", [False, True])
def test_root_listing_no_projects_is_an_unreachable_archive(
    archive: tuple[Settings, Database], leave_year_folder: bool
) -> None:
    """D58 firewall: zero candidates while the database has projects changes nothing."""
    settings, db = archive
    before = {p.rel_path: (p.state, p.missing_since) for p in db.list_projects()}
    for rel in (A, B):
        shutil.rmtree(settings.archive_root / rel)
    if not leave_year_folder:
        shutil.rmtree(settings.archive_root / "2025")
    with pytest.raises(sealer.ArchiveUnavailableError, match="lists no projects"):
        sealer.run_scan_cycle(db, settings, now=utcnow())
    assert {p.rel_path: (p.state, p.missing_since) for p in db.list_projects()} == before


def test_empty_root_with_only_ignored_projects_is_a_normal_round(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    for project in db.list_projects():
        sealer.request_ignore(db, project.id, utcnow())
        shutil.rmtree(settings.archive_root / project.rel_path)
    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    assert summary.discovered == 0 and summary.missing == []


def test_folder_back_restores_the_state_and_queues_a_verification(
    archive: tuple[Settings, Database], tmp_path: Path
) -> None:
    settings, db = archive
    shutil.move(settings.archive_root / A, tmp_path / "away")
    sealer.run_scan_cycle(db, settings, now=utcnow())
    shutil.move(tmp_path / "away", settings.archive_root / A)

    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    assert summary.reappeared == [A] and summary.missing == []
    assert (A, JobKind.VERIFY) in summary.enqueued
    back = db.get_project(A)
    assert back is not None and back.state is ProjectState.SEALED
    assert back.missing_since is None and back.state_before_missing is None
    job = db.queued_job(back.id)
    assert job is not None and job.kind is JobKind.VERIFY and job.trigger is Trigger.AUTO
    assert job.priority == sealer.PRIORITY[JobKind.VERIFY] and not job.bypass_hours

    time.sleep(1.1)  # distinct generation file names
    run_all_jobs(db, settings)
    verified = db.get_project(A)
    assert verified is not None and verified.state is ProjectState.SEALED
    assert verified.last_generation_no == 2 and verified.last_verified_at is not None
    reference_verifies(settings.archive_root / A)
    assert db.get_project(B).state is ProjectState.UNSEALED  # type: ignore[union-attr]


def test_project_in_review_comes_back_in_review_without_verification(
    archive: tuple[Settings, Database], tmp_path: Path
) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    db.set_state(a.id, ProjectState.NEEDS_REVIEW, review_reason="1 modified since the last seal")
    shutil.move(settings.archive_root / A, tmp_path / "away")
    sealer.run_scan_cycle(db, settings, now=utcnow())
    shutil.move(tmp_path / "away", settings.archive_root / A)
    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    back = db.get_project(A)
    assert back is not None and back.state is ProjectState.NEEDS_REVIEW
    assert back.review_reason == "1 modified since the last seal"
    assert summary.enqueued == [] and db.queued_job(a.id) is None


def test_files_changed_while_away_go_to_review_after_coming_back(
    archive: tuple[Settings, Database], tmp_path: Path
) -> None:
    settings, db = archive
    shutil.move(settings.archive_root / A, tmp_path / "away")
    sealer.run_scan_cycle(db, settings, now=utcnow())
    (tmp_path / "away" / "01_MASTERS" / "m.mov").write_bytes(b"changed")
    shutil.move(tmp_path / "away", settings.archive_root / A)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    back = db.get_project(A)
    assert back is not None and back.state is ProjectState.NEEDS_REVIEW
    run_all_jobs(db, settings)  # the queued verification is skipped: the project is in review
    jobs = [j for j in db.list_jobs() if j.kind is JobKind.VERIFY]
    assert jobs[0].state is JobState.CANCELLED


# --- D61: Retry --------------------------------------------------------------------------------


def test_retry_checks_the_folder_now(archive: tuple[Settings, Database], tmp_path: Path) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    with pytest.raises(sealer.SealerError, match="only a missing project"):
        sealer.request_retry(db, settings, a.id, utcnow())
    shutil.move(settings.archive_root / A, tmp_path / "away")
    sealer.run_scan_cycle(db, settings, now=utcnow())
    missing = db.get_project(a.id)

    assert sealer.request_retry(db, settings, a.id, utcnow()) is False
    assert db.get_project(a.id) == missing  # nothing changed

    shutil.move(tmp_path / "away", settings.archive_root / A)
    assert sealer.request_retry(db, settings, a.id, utcnow()) is True
    back = db.get_project(a.id)
    assert back is not None and back.state is ProjectState.SEALED and back.missing_since is None
    job = db.queued_job(a.id)
    assert job is not None and job.kind is JobKind.VERIFY and job.trigger is Trigger.AUTO


# --- D60: Retire -------------------------------------------------------------------------------


def test_retire_forgets_everything_but_one_job_and_one_log_line(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    jobs_before = {j.id for j in db.list_jobs()}
    db.replace_verify_results(next(iter(jobs_before)), a.id, [VerifyResult("x", "1", "1", "ok")])
    assert mirror_of(settings, A).is_dir() and mirror_of(settings, B).parent.exists() is False
    with pytest.raises(sealer.SealerError, match="only a missing project can be retired"):
        sealer.request_retire(db, settings, a.id, utcnow())

    shutil.rmtree(settings.archive_root / A)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    db.set_kv(sealer.ROOT_MANIFEST_STALE_KEY, "0")
    now = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)
    assert sealer.request_retire(db, settings, a.id, now) == "2025-01_CLIENTE-CAMPANA"

    assert db.get_project(a.id) is None and db.get_project(A) is None
    for table in ("files", "sealed_files", "file_hashes", "scans", "review_items"):
        count = db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (a.id,))
        assert count.fetchone()[0] == 0, table
    assert db.conn.execute("SELECT COUNT(*) FROM verify_results").fetchone()[0] == 0
    assert not (settings.config_dir / "history" / "2025").exists()  # mirror and empty parents
    (job,) = db.list_jobs()  # the project's own jobs went with it
    assert job.kind is JobKind.RETIRE and job.project_id is None
    assert job.state is JobState.DONE and job.trigger is Trigger.MANUAL
    assert job.started_at == job.finished_at == to_iso(now)
    assert [e.msg for e in db.get_job_log(job.id)] == [f"retired {A} (history mirror deleted)"]
    assert db.get_kv(sealer.ROOT_MANIFEST_STALE_KEY) == "1"
    assert db.get_project(B) is not None


def test_retire_keeps_the_mirror_of_a_project_nested_below(tmp_path: Path) -> None:
    settings = Settings(archive_root=tmp_path / "archive", config_dir=tmp_path / "config")
    for rel in ("2025", "2025/2025-01_X"):
        (history_mirror.mirror_dir(settings, rel) / CHAIN).parent.mkdir(parents=True)
        (history_mirror.mirror_dir(settings, rel) / CHAIN).write_text(rel)
    assert history_mirror.remove_mirror(settings, "2025")
    assert not history_mirror.mirror_dir(settings, "2025").exists()
    assert (history_mirror.mirror_dir(settings, "2025/2025-01_X") / CHAIN).read_text() == (
        "2025/2025-01_X"
    )


# --- D59: the history mirror -------------------------------------------------------------------


def assert_mirrored(settings: Settings, rel: str) -> None:
    disk = settings.archive_root / rel / "ascmhl"
    mirror = mirror_of(settings, rel)
    names = sorted(p.name for p in disk.iterdir() if not p.name.startswith("."))
    assert sorted(p.name for p in mirror.iterdir()) == names
    for name in names:
        assert (mirror / name).read_bytes() == (disk / name).read_bytes(), name


def test_mirror_follows_seal_append_verify_and_accept(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    assert_mirrored(settings, A)  # after the seal (fixture)
    a = db.get_project(A)
    assert a is not None
    folder = settings.archive_root / A

    time.sleep(1.1)
    (folder / "02_NEW").mkdir()
    (folder / "02_NEW" / "n.mov").write_bytes(b"new" * 50)
    sealer.run_scan_cycle(db, settings, now=utcnow())  # changed
    sealer.run_scan_cycle(db, settings, now=utcnow())  # settled: append queued
    run_all_jobs(db, settings)
    assert db.get_project(a.id).last_generation_no == 2  # type: ignore[union-attr]
    assert_mirrored(settings, A)

    time.sleep(1.1)
    sealer.enqueue(db, a.id, JobKind.VERIFY, Trigger.AUTO, utcnow())
    run_all_jobs(db, settings)
    assert db.get_project(a.id).last_generation_no == 3  # type: ignore[union-attr]
    assert_mirrored(settings, A)
    old_chain = (mirror_of(settings, A) / CHAIN).read_bytes()

    db.set_state(a.id, ProjectState.NEEDS_REVIEW, review_reason="test")
    sealer.request_accept_new_version(db, a.id, utcnow())
    run_all_jobs(db, settings)
    after = db.get_project(a.id)
    assert after is not None and after.state is ProjectState.SEALED
    assert after.last_generation_no == 1
    assert_mirrored(settings, A)
    (superseded,) = (folder / "ascmhl_superseded").iterdir()
    mirrored = mirror_of(settings, A).parent / "ascmhl_superseded" / superseded.name  # same stamp
    assert (mirrored / CHAIN).read_bytes() == old_chain == (superseded / CHAIN).read_bytes()
    assert sorted(p.name for p in mirrored.iterdir()) == sorted(
        p.name for p in superseded.iterdir()
    )
    reference_verifies(folder)


def test_scan_mirrors_a_history_written_by_another_tool(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / B
    write_project_generation(folder, {"01_MASTERS/m.mov": xxh(folder / "01_MASTERS/m.mov")})
    assert not mirror_of(settings, B).exists()
    sealer.run_scan_cycle(db, settings, now=utcnow())
    assert_mirrored(settings, B)
    assert db.get_project(B).last_generation_no is None  # type: ignore[union-attr]

    # Unchanged chain: nothing copied again; a changed chain (another tool's new generation)
    # is copied, and the old manifests stay in the mirror.
    disk_chain = folder / "ascmhl" / CHAIN
    assert not history_mirror.needs_sync(folder, mirror_of(settings, B))
    time.sleep(1.1)
    (folder / "x.mov").write_bytes(b"x")
    write_project_generation(folder, {"x.mov": xxh(folder / "x.mov")}, partial=True)
    assert history_mirror.needs_sync(folder, mirror_of(settings, B))
    sealer.run_scan_cycle(db, settings, now=utcnow())
    assert (mirror_of(settings, B) / CHAIN).read_bytes() == disk_chain.read_bytes()
    assert len(list(mirror_of(settings, B).glob("*.mhl"))) == 2


def xxh(path: Path) -> dict[str, str]:
    return {"xxh128": xxhash.xxh128(path.read_bytes()).hexdigest()}


def test_sync_mirror_skips_temp_files_and_never_deletes(tmp_path: Path) -> None:
    project, mirror = tmp_path / "p", tmp_path / "m"
    asc = project / "ascmhl"
    asc.mkdir(parents=True)
    (asc / "0001_p.mhl").write_text("one")
    (asc / ".0002_p.mhl.tmp").write_text("partial")
    (asc / CHAIN).write_text("chain-1")
    mirror.mkdir()
    (mirror / f".{CHAIN}{history_mirror.TEMP_SUFFIX}").write_text("killed mid-copy")
    assert history_mirror.sync_mirror(project, mirror)
    assert sorted(p.name for p in mirror.iterdir()) == ["0001_p.mhl", CHAIN]
    assert not history_mirror.sync_mirror(project, mirror)  # nothing new
    (asc / "0002_p.mhl").write_text("two")
    (asc / CHAIN).write_text("chain-2")
    assert history_mirror.sync_mirror(project, mirror)  # a new generation: added, nothing moved
    assert sorted(p.name for p in mirror.iterdir()) == ["0001_p.mhl", "0002_p.mhl", CHAIN]
    assert not (tmp_path / history_mirror.SUPERSEDED_DIR).exists()
    # A history that lost a manifest is another history: the mirror is set aside, never mixed
    # with it (``ascmhl`` would load the stray manifest as a generation) and never deleted.
    (asc / "0001_p.mhl").unlink()
    (asc / "0002_p.mhl").unlink()
    (asc / "0001_q.mhl").write_text("other")
    (asc / CHAIN).write_text("chain-3")
    assert history_mirror.sync_mirror(project, mirror)
    assert sorted(p.name for p in mirror.iterdir()) == ["0001_q.mhl", CHAIN]
    (aside,) = (tmp_path / history_mirror.SUPERSEDED_DIR).iterdir()
    assert (aside / "0001_p.mhl").read_text() == "one" and (aside / CHAIN).read_text() == "chain-2"
    assert not history_mirror.sync_mirror(tmp_path / "no-history", tmp_path / "m2")
    with pytest.raises(ValueError):
        history_mirror.mirror_dir(Settings(config_dir=tmp_path), "../escape")


def test_zip_history_from_the_mirror_or_from_the_disk(
    archive: tuple[Settings, Database], tmp_path: Path
) -> None:
    settings, db = archive
    a, b = db.get_project(A), db.get_project(B)
    assert a is not None and b is not None
    assert history_mirror.zip_history(settings, b) is None  # no history anywhere

    data = history_mirror.zip_history(settings, a)
    assert data is not None
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    manifest = next((settings.archive_root / A / "ascmhl").glob("*.mhl")).name
    assert sorted(names) == sorted([f"{a.name}/ascmhl/{CHAIN}", f"{a.name}/ascmhl/{manifest}"])
    # The download works once the folder is gone: it comes from the mirror.
    shutil.rmtree(settings.archive_root / A)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    missing = db.get_project(A)
    assert missing is not None and missing.state is ProjectState.MISSING
    again = history_mirror.zip_history(settings, missing)
    assert again is not None
    extracted = tmp_path / "unzipped"
    zipfile.ZipFile(io.BytesIO(again)).extractall(extracted)
    assert (extracted / a.name / "ascmhl" / CHAIN).read_bytes() == (
        mirror_of(settings, A) / CHAIN
    ).read_bytes()

    # No mirror (e.g. a history written before the mirror existed): from ascmhl/ on disk.
    folder = settings.archive_root / B
    write_project_generation(folder, {"01_MASTERS/m.mov": xxh(folder / "01_MASTERS/m.mov")})
    from_disk = history_mirror.zip_history(settings, b)
    assert from_disk is not None
    assert f"{b.name}/ascmhl/{CHAIN}" in zipfile.ZipFile(io.BytesIO(from_disk)).namelist()


# --- D62: moved or renamed folders --------------------------------------------------------------


def test_renamed_between_two_scans_is_the_same_project(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    new_rel = "2025/2025-01_CLIENTE-CAMPANA_RENAMED"
    (settings.archive_root / A).rename(settings.archive_root / new_rel)
    old_mirror = (mirror_of(settings, A) / CHAIN).read_bytes()

    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    assert summary.moved == [(A, new_rel)]
    assert summary.new_projects == [] and summary.missing == [] and summary.enqueued == []
    moved = db.get_project(new_rel)
    assert moved is not None and moved.id == a.id and moved.name == new_rel.split("/")[1]
    assert moved.state is ProjectState.SEALED and moved.last_generation_no == 1
    assert db.get_project(A) is None and len(db.list_projects()) == 2
    assert db.get_review_items(a.id) == []
    assert (mirror_of(settings, new_rel) / CHAIN).read_bytes() == old_mirror
    assert not mirror_of(settings, A).exists()
    reference_verifies(settings.archive_root / new_rel)


def test_missing_project_found_under_another_name(
    archive: tuple[Settings, Database], tmp_path: Path
) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    shutil.move(settings.archive_root / A, tmp_path / "away")
    sealer.run_scan_cycle(db, settings, now=utcnow())
    assert db.get_project(a.id).state is ProjectState.MISSING  # type: ignore[union-attr]

    new_rel = "2026/2026-01_CLIENTE-CAMPANA"
    (settings.archive_root / "2026").mkdir()
    shutil.move(tmp_path / "away", settings.archive_root / new_rel)
    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    assert summary.moved == [(A, new_rel)] and summary.enqueued == []
    moved = db.get_project(a.id)
    assert moved is not None and moved.rel_path == new_rel
    assert moved.state is ProjectState.SEALED and moved.missing_since is None
    assert db.queued_job(a.id) is None
    assert_mirrored(settings, new_rel)


def test_a_copy_without_a_vanished_twin_is_a_new_project(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    copy = "2025/2025-01_CLIENTE-CAMPANA_COPIA"
    shutil.copytree(settings.archive_root / A, settings.archive_root / copy)
    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    assert summary.moved == [] and summary.new_projects == [copy]


def test_two_vanished_twins_the_most_recently_missed_wins(
    archive: tuple[Settings, Database], tmp_path: Path
) -> None:
    settings, db = archive
    a, b = db.get_project(A), db.get_project(B)
    assert a is not None and b is not None
    shutil.move(settings.archive_root / A, tmp_path / "away")
    shutil.rmtree(settings.archive_root / B)
    (settings.archive_root / "2025" / "keep").mkdir()  # the root still lists a project
    sealer.run_scan_cycle(db, settings, now=utcnow())
    # B's mirror holds the same chain (it should not happen; the newest wins).
    shutil.copytree(mirror_of(settings, A), mirror_of(settings, B))
    db.update_project_fields(a.id, missing_since="2026-10-01T10:00:00.000000Z")
    db.update_project_fields(b.id, missing_since="2026-10-02T10:00:00.000000Z")
    shutil.move(tmp_path / "away", settings.archive_root / "2025" / "renamed")
    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    assert summary.moved == [(B, "2025/renamed")]
    assert db.get_project(a.id).state is ProjectState.MISSING  # type: ignore[union-attr]


# --- D63: Verify now ---------------------------------------------------------------------------


def test_verify_now_is_manual_bypasses_hours_and_promotes_a_scheduled_one(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a, b = db.get_project(A), db.get_project(B)
    assert a is not None and b is not None
    with pytest.raises(sealer.SealerError, match="Verify now needs state sealed"):
        sealer.request_verify_now(db, b.id, utcnow())
    scheduled = sealer.enqueue(db, a.id, JobKind.VERIFY, Trigger.AUTO, utcnow())
    job_id = sealer.request_verify_now(db, a.id, utcnow())
    assert job_id == scheduled
    job = db.get_job(job_id)
    assert job is not None and job.trigger is Trigger.MANUAL and job.bypass_hours
    assert job.priority == sealer.PRIORITY[JobKind.VERIFY] + sealer.MANUAL_BONUS
    project = db.get_project(a.id)
    assert project is not None and project.state is ProjectState.QUEUED
    assert db.next_job(utcnow(), bypass_only=True) == job
    assert sealer.cancellable_job(db, project) == job

    time.sleep(1.1)
    run_all_jobs(db, settings)
    after = db.get_project(a.id)
    assert after is not None and after.state is ProjectState.SEALED
    assert after.last_generation_no == 2 and after.last_verified_at is not None
    assert_mirrored(settings, A)
    reference_verifies(settings.archive_root / A)


def test_verify_now_cancelled_in_the_queue_or_while_reading(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    job_id = sealer.request_verify_now(db, a.id, utcnow())
    assert sealer.request_cancel(db, a.id, utcnow()) == job_id
    project = db.get_project(a.id)
    assert project is not None and project.state is ProjectState.SEALED
    assert db.get_job(job_id).state is JobState.CANCELLED  # type: ignore[union-attr]

    job_id = sealer.request_verify_now(db, a.id, utcnow())
    cancel = threading.Event()
    cancel.set()  # the Cancel button pressed while the verification reads (D57)
    run_all_jobs(db, settings, cancel=cancel)
    job = db.get_job(job_id)
    assert job is not None and job.state is JobState.CANCELLED
    after = db.get_project(a.id)
    assert after is not None and after.state is ProjectState.SEALED
    assert after.last_generation_no == 1 and after.last_verified_at is None
    assert len(list((settings.archive_root / A / "ascmhl").glob("*.mhl"))) == 1


def test_stopped_verify_now_waits_as_queued(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    job_id = sealer.request_verify_now(db, a.id, utcnow())
    job = db.get_job(job_id)
    assert job is not None
    stop = threading.Event()
    stop.set()  # SIGTERM
    sealer.run_job(db, settings, job, gate=open_gate(), stop=stop)
    assert db.get_job(job_id).state is JobState.QUEUED  # type: ignore[union-attr]
    project = db.get_project(a.id)
    assert project is not None and project.state is ProjectState.QUEUED
    assert sealer.cancellable_job(db, project) is not None


def test_a_stray_job_of_a_missing_project_is_cancelled(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    shutil.rmtree(settings.archive_root / A)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    job_id = db.enqueue_job(JobKind.VERIFY, a.id, Trigger.AUTO, 10, utcnow())
    run_all_jobs(db, settings)
    assert db.get_job(job_id).state is JobState.CANCELLED  # type: ignore[union-attr]
    assert db.get_project(a.id).state is ProjectState.MISSING  # type: ignore[union-attr]


# --- review fixes ------------------------------------------------------------------------------


def test_a_folder_discovery_did_not_list_but_still_there_is_not_missing(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery swallows listing errors (a year folder that fails to list on SMB): absence is
    confirmed by one lstat of the folder, so a hiccup never turns projects ``missing``."""
    settings, db = archive
    real = discovery.discover_projects

    def without_a(*args: object, **kwargs: object) -> list[object]:
        return [c for c in real(*args, **kwargs) if c.rel_path != A]  # type: ignore[arg-type]

    monkeypatch.setattr(sealer, "discover_projects", without_a)
    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    a = db.get_project(A)
    assert a is not None and a.state is ProjectState.SEALED and a.missing_since is None
    assert summary.missing == [] and any(e.startswith(A) for e in summary.errors)

    shutil.rmtree(settings.archive_root / A)  # really gone now
    summary = sealer.run_scan_cycle(db, settings, now=utcnow())
    a = db.get_project(A)
    assert a is not None and a.state is ProjectState.MISSING and summary.missing == [A]


def test_retire_waits_for_a_job_still_holding_the_project(
    archive: tuple[Settings, Database],
) -> None:
    """A job that was running when the folder vanished still writes its own rows (log, hash
    cache); deleting the project under it would break them, so Retire refuses until it stops."""
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    job_id = sealer.enqueue(db, a.id, JobKind.VERIFY, Trigger.AUTO, utcnow())
    db.set_job_state(job_id, JobState.RUNNING, utcnow())
    shutil.rmtree(settings.archive_root / A)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    with pytest.raises(sealer.SealerError, match="still stopping"):
        sealer.request_retire(db, settings, a.id, utcnow())
    assert db.get_project(a.id) is not None
    db.set_job_state(job_id, JobState.CANCELLED, utcnow())
    sealer.request_retire(db, settings, a.id, utcnow())
    assert db.get_project(a.id) is None


def test_mirror_set_aside_when_accept_could_not_move_it(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    """If setting the mirror aside on Accept fails, the next sync still never mixes the two
    histories: the zip of the mirror validates with the reference implementation."""
    settings, db = archive
    a = db.get_project(A)
    assert a is not None
    (settings.archive_root / A / "01_MASTERS" / "m.mov").write_bytes(b"changed")
    sealer.run_scan_cycle(db, settings, now=utcnow())
    assert db.get_project(a.id).state is ProjectState.NEEDS_REVIEW  # type: ignore[union-attr]

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(history_mirror, "supersede_mirror", fail)
    time.sleep(1.1)  # distinct generation names
    sealer.request_accept_new_version(db, a.id, utcnow())
    run_all_jobs(db, settings)
    assert db.get_project(a.id).state is ProjectState.SEALED  # type: ignore[union-attr]
    mirror = mirror_of(settings, A)
    on_disk = sorted(p.name for p in (settings.archive_root / A / "ascmhl").iterdir())
    assert sorted(p.name for p in mirror.iterdir()) == on_disk
    assert (mirror.parent / history_mirror.SUPERSEDED_DIR).is_dir()
