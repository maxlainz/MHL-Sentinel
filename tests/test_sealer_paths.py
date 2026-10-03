"""sealer.py: error, edge and recovery paths (complements test_sealer, test_missing, test_verify).

Each test drives a real archive in ``tmp_path`` and asserts what the owner would see: project
states, job states, job log lines and files on disk. Error branches are reached by monkeypatching
the collaborator that fails (scanner, hasher, history mirror), never by skipping code.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import xxhash

from helpers_ascmhl import run_cli
from mhl_sentinel import history_mirror, rootmanifest, sealer
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, ProjectRow, SealedFile
from mhl_sentinel.hasher import FileChanged
from mhl_sentinel.mhlwriter import write_project_generation
from mhl_sentinel.models import (
    ChangeKind,
    FileStat,
    JobKind,
    JobState,
    ProjectCandidate,
    ProjectState,
    Trigger,
)
from mhl_sentinel.scanner import ScanOutcome
from test_sealer import (
    NOW,
    make_archive,
    open_gate,
    run_all_jobs,
    set_mtime_before_now,
    xxh_all,
)

A = "2025/2025-01_CLIENTE-CAMPANA"
B = "2025/2025-02_CLIENTE-OTRA"


@pytest.fixture
def archive(tmp_path: Path) -> Iterator[tuple[Settings, Database]]:
    """Two preexisting projects, both ``unsealed`` after the first scan."""
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    yield settings, db
    db.close()


def project_of(db: Database, rel: str) -> ProjectRow:
    project = db.get_project(rel)
    assert project is not None
    return project


def seal(db: Database, settings: Settings, rel: str) -> ProjectRow:
    project = project_of(db, rel)
    sealer.request_seal(db, project.id, NOW)
    run_all_jobs(db, settings)
    sealed = project_of(db, rel)
    assert sealed.state is ProjectState.SEALED
    return sealed


def messages(db: Database, job_id: int) -> list[str]:
    return [e.msg for e in db.get_job_log(job_id)]


def job_of(db: Database, job_id: int) -> Any:
    job = db.get_job(job_id)
    assert job is not None
    return job


def run_one(
    db: Database,
    settings: Settings,
    job_id: int,
    *,
    stop: threading.Event | None = None,
    on_progress: sealer.ProgressFn | None = None,
) -> None:
    sealer.run_job(
        db,
        settings,
        job_of(db, job_id),
        gate=open_gate(),
        stop=stop or threading.Event(),
        on_progress=on_progress,
    )


def reference_verifies(folder: Path) -> None:
    result = run_cli("ascmhl-debug", "verify", folder)
    assert result.returncode == 0, result.stdout + result.stderr


def add_file(folder: Path, rel: str, data: bytes) -> Path:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    set_mtime_before_now(path)
    return path


# --- orphan manifests and startup recovery ----------------------------------------------------


def test_orphan_name_collision_gets_a_numbered_suffix(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    seal(db, settings, A)
    asc = settings.archive_root / A / "ascmhl"
    (asc / "0002_stray.mhl").write_text("<x/>")
    (asc / "0002_stray.mhl.orphan").write_text("<older/>")  # an earlier quarantine

    moved = sealer.quarantine_orphan_manifests(settings.archive_root / A)

    assert [p.name for p in moved] == ["0002_stray.mhl.orphan.1"]
    assert not (asc / "0002_stray.mhl").exists()
    assert (asc / "0002_stray.mhl.orphan").read_text() == "<older/>"  # the old one is untouched
    assert (asc / "0002_stray.mhl.orphan.1").read_text() == "<x/>"


def test_recovery_helpers_ignore_a_folder_without_history(tmp_path: Path) -> None:
    folder = tmp_path / "2025-01_CLIENTE-CAMPANA"
    folder.mkdir()
    assert sealer.remove_stale_temp_files(folder) == []
    assert sealer.quarantine_orphan_manifests(folder) == []


def test_startup_recovery_reports_a_broken_chain_and_keeps_going(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    seal(db, settings, A)
    seal(db, settings, B)
    (settings.archive_root / A / "ascmhl" / "ascmhl_chain.xml").write_text("<not xml")
    stale = settings.archive_root / B / "ascmhl" / ".0001_x.mhl.tmp"
    stale.write_bytes(b"<partial")

    result = sealer.recover_history_dirs(db, settings)

    assert len(result.errors) == 1
    assert result.errors[0].startswith(f"{A}: startup recovery failed: ")
    assert result.temp_files == [f"{B}/ascmhl/{stale.name}"]  # B was still cleaned
    assert not stale.exists()


def test_recover_after_restart_requeues_running_jobs_and_hashing_projects(
    archive: tuple[Settings, Database],
) -> None:
    _settings, db = archive
    project = project_of(db, A)
    job_id = sealer.request_seal(db, project.id, NOW)
    db.set_job_state(job_id, JobState.RUNNING, NOW)
    db.set_state(project.id, ProjectState.HASHING)

    assert sealer.recover_after_restart(db) == 1

    assert job_of(db, job_id).state is JobState.QUEUED
    assert project_of(db, A).state is ProjectState.QUEUED


# --- scan cycle -------------------------------------------------------------------------------


def test_unmounted_root_raises_and_touches_nothing(tmp_path: Path) -> None:
    settings = Settings(archive_root=tmp_path / "not-mounted", config_dir=tmp_path / "config")
    db = Database(settings.db_path).open()
    with pytest.raises(sealer.ArchiveUnavailableError, match="not a directory"):
        sealer.run_scan_cycle(db, settings, now=NOW)
    assert db.list_projects() == []
    db.close()


def test_stop_event_ends_the_cycle_before_scanning_any_project(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    stop = threading.Event()
    stop.set()

    summary = sealer.run_scan_cycle(db, settings, now=NOW, stop=stop)

    assert summary.discovered == 2 and summary.scanned == 0 and summary.states == {}


def test_folder_gone_is_not_decided_on_an_unreadable_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "2025-01_CLIENTE-CAMPANA"
    folder.mkdir()
    assert sealer._folder_gone(folder) is False
    assert sealer._folder_gone(tmp_path / "nowhere") is True

    def deny(self: Path, **kwargs: Any) -> Any:
        raise PermissionError("flaky share")

    monkeypatch.setattr(Path, "lstat", deny)
    assert sealer._folder_gone(folder) is False  # D58: only "no such file" counts as vanished


def test_state_before_missing_that_makes_no_sense_falls_back_to_the_resting_state(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = seal(db, settings, A)
    b = project_of(db, B)
    for rel in (A, B):
        shutil.move(settings.archive_root / rel, settings.archive_root / f"away-{rel[-5:]}")
    sealer.run_scan_cycle(db, settings, now=NOW)
    for rel in (A, B):
        assert project_of(db, rel).state is ProjectState.MISSING
        shutil.move(settings.archive_root / f"away-{rel[-5:]}", settings.archive_root / rel)
    db.update_project_fields(a.id, state_before_missing=ProjectState.HASHING)
    db.update_project_fields(b.id, state_before_missing=None)

    assert sealer.request_retry(db, settings, a.id, NOW) is True
    assert sealer.request_retry(db, settings, b.id, NOW) is True

    assert project_of(db, A).state is ProjectState.SEALED
    assert project_of(db, B).state is ProjectState.UNSEALED
    assert db.has_open_job(a.id, JobKind.VERIFY)  # the sealed one is verified again
    assert not db.has_open_job(b.id, JobKind.VERIFY)  # nothing to verify in an unsealed one


def test_match_moved_needs_a_readable_chain(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    cand = ProjectCandidate("2025/2025-03_CLIENTE-NUEVO", "2025-03_CLIENTE-NUEVO", True)
    (settings.archive_root / cand.rel_path).mkdir()  # claims a history, has none
    assert sealer._match_moved(db, settings, cand, db.list_projects(), NOW) is None


def test_moved_folder_is_followed_even_if_its_mirror_cannot_be_moved(
    archive: tuple[Settings, Database],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings, db = archive
    a = seal(db, settings, A)
    new_rel = "2025/2025-01_CLIENTE-CAMPANA_RENAMED"
    (settings.archive_root / A).rename(settings.archive_root / new_rel)

    def broken(settings: Settings, old: str, new: str) -> bool:
        raise OSError("mirror volume read-only")

    monkeypatch.setattr(history_mirror, "move_mirror", broken)
    with caplog.at_level(logging.WARNING):
        summary = sealer.run_scan_cycle(db, settings, now=NOW)

    assert summary.moved == [(A, new_rel)]
    moved = project_of(db, new_rel)
    assert moved.id == a.id and moved.state is ProjectState.SEALED
    assert f"history mirror of {A} not moved: mirror volume read-only" in caplog.text
    # the scan that follows mirrors the history again at the new path
    assert history_mirror.chain_bytes(history_mirror.mirror_dir(settings, new_rel)) is not None


def test_broken_history_puts_the_project_in_error_and_the_rest_keeps_scanning(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = seal(db, settings, A)
    (settings.archive_root / A / "ascmhl" / "ascmhl_chain.xml").write_text("<not xml")

    summary = sealer.run_scan_cycle(db, settings, now=NOW)

    broken = project_of(db, A)
    assert broken.state is ProjectState.ERROR and broken.error is not None
    assert broken.error.startswith("scan failed: ")
    assert broken.last_generation_no == a.last_generation_no  # nothing else was touched
    assert summary.states[A] is ProjectState.ERROR
    assert summary.errors == [f"{A}: {broken.error}"]
    assert summary.states[B] is ProjectState.UNSEALED and summary.scanned == 1


def test_unreadable_entries_do_not_classify_on_an_incomplete_snapshot(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = archive
    seal(db, settings, A)
    db.update_project_fields(project_of(db, A).id, review_reason="kept reason")

    def partial(*args: Any, **kwargs: Any) -> ScanOutcome:
        return ScanOutcome(files=[], errors=["01_MASTERS/m.mov: permission denied"])

    monkeypatch.setattr(sealer, "scan_project", partial)
    summary = sealer.run_scan_cycle(db, settings, now=NOW)

    project = project_of(db, A)
    assert project.state is ProjectState.ERROR  # not NEEDS_REVIEW with "everything deleted"
    assert project.error == "scan errors: 01_MASTERS/m.mov: permission denied"
    assert project.review_reason == "kept reason"
    assert summary.states[A] is ProjectState.ERROR
    assert f"{A}: {project.error}" in summary.errors
    assert db.get_review_items(project.id) == []


def test_project_hashing_right_now_is_scanned_but_its_mirror_is_left_alone(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = seal(db, settings, A)
    mirror = history_mirror.mirror_dir(settings, A)
    shutil.rmtree(mirror)
    db.set_state(a.id, ProjectState.HASHING)  # a job may be writing the history

    sealer.run_scan_cycle(db, settings, now=NOW)
    assert project_of(db, A).state is ProjectState.HASHING
    assert not mirror.exists()

    db.set_state(a.id, ProjectState.SEALED)
    sealer.run_scan_cycle(db, settings, now=NOW)
    assert history_mirror.chain_bytes(mirror) is not None  # D59: mirrored again once idle


def test_stable_project_without_stable_since_gets_it_from_the_scan(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = project_of(db, A)
    assert a.stable_since is not None  # set by the first sighting
    db.update_project_fields(a.id, stable_since=None)

    sealer.run_scan_cycle(db, settings, now=NOW)  # identical to the previous scan: stable

    after = project_of(db, A)
    assert after.stable_since == "2026-10-01T22:00:00.000000Z"


def test_mirror_failure_during_scan_is_reported_not_fatal(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = archive
    seal(db, settings, A)

    def broken(project_root: Path, mirror: Path) -> bool:
        raise OSError("config volume full")

    monkeypatch.setattr(history_mirror, "needs_sync", broken)
    summary = sealer.run_scan_cycle(db, settings, now=NOW)

    assert f"{A}: history mirror not updated: config volume full" in summary.errors
    assert project_of(db, A).state is ProjectState.SEALED
    assert summary.scanned == 2


# --- user requests ----------------------------------------------------------------------------


def test_requests_on_an_unknown_project_are_refused(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    for request in (
        lambda: sealer.request_seal(db, 999, NOW),
        lambda: sealer.request_cancel(db, 999, NOW),
        lambda: sealer.request_ignore(db, 999, NOW),
        lambda: sealer.request_unignore(db, 999, NOW),
        lambda: sealer.request_accept_new_version(db, 999, NOW),
        lambda: sealer.request_postpone(db, 999, NOW),
        lambda: sealer.request_verify_now(db, 999, NOW),
        lambda: sealer.request_retry(db, settings, 999, NOW),
        lambda: sealer.request_retire(db, settings, 999, NOW),
    ):
        with pytest.raises(sealer.SealerError, match="no project with id 999"):
            request()


def test_request_state_guards(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    a = project_of(db, A)

    with pytest.raises(sealer.SealerError, match="not ignored"):
        sealer.request_unignore(db, a.id, NOW)
    with pytest.raises(sealer.SealerError, match="Postpone needs state needs_review"):
        sealer.request_postpone(db, a.id, NOW)
    with pytest.raises(sealer.SealerError, match="Verify now needs state sealed"):
        sealer.request_verify_now(db, a.id, NOW)
    with pytest.raises(sealer.SealerError, match="only a missing project can be retried"):
        sealer.request_retry(db, settings, a.id, NOW)
    with pytest.raises(sealer.SealerError, match="only a missing project can be retired"):
        sealer.request_retire(db, settings, a.id, NOW)

    db.set_state(a.id, ProjectState.HASHING)
    with pytest.raises(sealer.SealerError, match="cannot ignore while hashing"):
        sealer.request_ignore(db, a.id, NOW)
    assert project_of(db, A).state is ProjectState.HASHING


def test_postpone_keeps_the_project_in_review(archive: tuple[Settings, Database]) -> None:
    _settings, db = archive
    a = project_of(db, A)
    db.set_state(a.id, ProjectState.NEEDS_REVIEW, review_reason="1 modified since the last seal")

    sealer.request_postpone(db, a.id, NOW)

    after = project_of(db, A)
    assert after.state is ProjectState.NEEDS_REVIEW
    assert after.review_reason == "1 modified since the last seal"


def test_retry_with_an_unreadable_folder_changes_nothing(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = archive
    a = project_of(db, A)
    shutil.rmtree(settings.archive_root / A)
    sealer.run_scan_cycle(db, settings, now=NOW)
    assert project_of(db, A).state is ProjectState.MISSING
    target = settings.archive_root / A
    real_is_dir = Path.is_dir

    def is_dir(self: Path, **kwargs: Any) -> bool:
        if self == target:
            raise PermissionError("share went away")
        return real_is_dir(self, **kwargs)

    monkeypatch.setattr(Path, "is_dir", is_dir)

    assert sealer.request_retry(db, settings, a.id, NOW) is False
    assert project_of(db, A).state is ProjectState.MISSING


def test_retire_stops_if_the_history_mirror_cannot_be_deleted(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = archive
    a = seal(db, settings, A)
    shutil.rmtree(settings.archive_root / A)
    sealer.run_scan_cycle(db, settings, now=NOW)

    def broken(settings: Settings, rel_path: str) -> bool:
        raise OSError("read-only file system")

    monkeypatch.setattr(history_mirror, "remove_mirror", broken)
    with pytest.raises(sealer.SealerError, match="history mirror not deleted: read-only"):
        sealer.request_retire(db, settings, a.id, NOW)

    assert project_of(db, A).state is ProjectState.MISSING  # the row (and its history) stay
    assert history_mirror.chain_bytes(history_mirror.mirror_dir(settings, A)) is not None
    assert db.count_jobs(JobState.DONE) == 1  # only the seal: no retire job was recorded


# --- run_job: guards, stops and failures ------------------------------------------------------


def test_job_of_a_vanished_project_row_fails(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    job_id = sealer.request_seal(db, project_of(db, A).id, NOW)
    ghost = dataclasses.replace(job_of(db, job_id), project_id=999)
    nobody = dataclasses.replace(job_of(db, job_id), project_id=None)

    sealer.run_job(db, settings, ghost, gate=open_gate(), stop=threading.Event())
    failed = job_of(db, job_id)
    assert failed.state is JobState.FAILED and failed.error == "project not found"

    sealer.run_job(db, settings, nobody, gate=open_gate(), stop=threading.Event())
    assert "no project" in job_of(db, job_id).error


def test_job_whose_folder_vanished_before_it_started_fails_with_the_project_in_error(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = project_of(db, A)
    job_id = sealer.request_seal(db, a.id, NOW)
    shutil.rmtree(settings.archive_root / A)  # no scan cycle ran in between

    run_one(db, settings, job_id)

    job = job_of(db, job_id)
    assert job.state is JobState.FAILED and job.error == "FileNotFoundError: folder missing"
    after = project_of(db, A)
    assert after.state is ProjectState.ERROR and after.error == job.error
    assert [e.level for e in db.get_job_log(job_id)][-1] == "error"


def test_failure_after_the_scan_cycle_found_the_folder_gone_keeps_missing(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = project_of(db, A)
    job_id = sealer.request_seal(db, a.id, NOW)

    def cycle_marks_it_missing_then_read_fails(*_args: int) -> None:
        db.set_state(a.id, ProjectState.MISSING)
        raise RuntimeError("read error on a vanished share")

    run_one(db, settings, job_id, on_progress=cycle_marks_it_missing_then_read_fails)

    assert job_of(db, job_id).state is JobState.FAILED
    assert project_of(db, A).state is ProjectState.MISSING  # D58: says more than the read error
    assert project_of(db, A).error is None


def test_job_stopped_while_the_project_goes_missing_is_cancelled(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = project_of(db, A)
    job_id = sealer.request_seal(db, a.id, NOW)
    stop = threading.Event()

    def cycle_marks_it_missing(*_args: int) -> None:
        db.set_state(a.id, ProjectState.MISSING)
        stop.set()  # the scan cycle also asks the hasher to stop

    run_one(db, settings, job_id, stop=stop, on_progress=cycle_marks_it_missing)

    assert job_of(db, job_id).state is JobState.CANCELLED
    assert project_of(db, A).state is ProjectState.MISSING
    assert "stopped; the project folder is missing" in messages(db, job_id)
    assert not (settings.archive_root / A / "ascmhl").exists()


def test_file_that_keeps_changing_fails_the_job_and_writes_nothing(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = archive
    job_id = sealer.request_seal(db, project_of(db, A).id, NOW)

    def changing(*args: Any, **kwargs: Any) -> Any:
        raise FileChanged("01_MASTERS/m.mov")

    monkeypatch.setattr(sealer, "hash_project", changing)
    run_one(db, settings, job_id)

    job = job_of(db, job_id)
    assert job.state is JobState.FAILED
    assert job.error is not None and job.error.startswith("file kept changing while being read")
    assert project_of(db, A).state is ProjectState.ERROR
    assert not (settings.archive_root / A / "ascmhl").exists()


def test_file_touched_after_hashing_is_caught_before_writing(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    clip = settings.archive_root / A / "01_MASTERS" / "m.mov"
    job_id = sealer.request_seal(db, project_of(db, A).id, NOW)

    def grow_after_last_file(files_done: int, files_total: int, *_rest: int) -> None:
        if files_done == files_total > 0:
            with clip.open("ab") as fh:
                fh.write(b"more")

    run_one(db, settings, job_id, on_progress=grow_after_last_file)

    job = job_of(db, job_id)
    assert job.state is JobState.FAILED
    assert job.error == "file kept changing while being read: 01_MASTERS/m.mov"
    assert project_of(db, A).state is ProjectState.ERROR
    assert not (settings.archive_root / A / "ascmhl").exists()


def test_scan_errors_during_a_job_fail_it(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = archive
    job_id = sealer.request_seal(db, project_of(db, A).id, NOW)

    def partial(*args: Any, **kwargs: Any) -> ScanOutcome:
        return ScanOutcome(files=[], errors=["01_MASTERS: permission denied"])

    monkeypatch.setattr(sealer, "scan_project", partial)
    run_one(db, settings, job_id)

    job = job_of(db, job_id)
    assert job.state is JobState.FAILED
    assert job.error == "OSError: scan errors: 01_MASTERS: permission denied"
    assert project_of(db, A).state is ProjectState.ERROR


# --- seal paths -------------------------------------------------------------------------------


def _legacy_clip_folder(settings: Settings) -> Path:
    folder = settings.archive_root / A
    add_file(folder, "02_OCF/a.mov", b"a" * 200)
    return folder


def _legacy_mhl(folder: Path, entries: dict[str, str], extra: str = "") -> None:
    body = "".join(
        f"<hash><file>{name}</file><md5>{digest}</md5></hash>" for name, digest in entries.items()
    )
    add_file(
        folder,
        "02_OCF/offload.mhl",
        f'<?xml version="1.0"?><hashlist version="1.1">{body}{extra}</hashlist>'.encode(),
    )


def test_legacy_mhl_that_lists_an_ignored_file_warns_and_seals(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = _legacy_clip_folder(settings)
    add_file(folder, "02_OCF/.DS_Store", b"finder junk")  # ignored by the default patterns
    _legacy_mhl(
        folder,
        {
            "a.mov": hashlib.md5(b"a" * 200).hexdigest(),
            ".DS_Store": hashlib.md5(b"finder junk").hexdigest(),
        },
        extra="<hash><file>a.mov</file><xxhash64>zz</xxhash64></hash>",  # unusable: reported
    )
    sealer.run_scan_cycle(db, settings, now=NOW)
    job_id = sealer.request_seal(db, project_of(db, A).id, NOW)

    run_one(db, settings, job_id)

    assert project_of(db, A).state is ProjectState.SEALED
    log = messages(db, job_id)
    assert "legacy MHL lists 02_OCF/.DS_Store, excluded by the ignore patterns" in log
    assert "legacy MHL: offload.mhl: 02_OCF/a.mov: invalid xxhash64 value" in log
    assert "legacy MHL 1.x: 2 files with origin hashes" in log
    reference_verifies(folder)


def test_legacy_problems_ignores_files_that_were_not_hashed(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    a = project_of(db, A)
    job_id = sealer.request_seal(db, a.id, NOW)
    ctx = sealer._Ctx(
        db,
        settings,
        job_of(db, job_id),
        a,
        settings.archive_root / A,
        open_gate(),
        threading.Event(),
        lambda: NOW,
    )
    present = {"02_OCF/a.mov": FileStat("02_OCF/a.mov", 200, 1)}
    expected = {"02_OCF/a.mov": {"md5": "0" * 32}, "02_OCF/gone.mov": {"md5": "1" * 32}}

    items = sealer._legacy_problems(ctx, expected, present, digests={})

    # a.mov has no fresh digest to compare (not hashed): no verdict; gone.mov is simply gone
    assert [(i.rel_path, i.change) for i in items] == [("02_OCF/gone.mov", ChangeKind.DELETED)]


def test_seal_continues_a_history_written_by_another_tool(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / A
    write_project_generation(folder, xxh_all(folder))  # generation 1 by someone else
    sealer.run_scan_cycle(db, settings, now=NOW)
    a = project_of(db, A)
    assert a.state is ProjectState.UNSEALED and a.last_generation_no is None

    seal(db, settings, A)

    after = project_of(db, A)
    assert after.last_generation_no == 2
    assert sorted(p.name[:4] for p in (folder / "ascmhl").glob("*.mhl")) == ["0001", "0002"]
    reference_verifies(folder)


def test_seal_over_a_foreign_history_that_disagrees_goes_to_review(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / A
    write_project_generation(folder, xxh_all(folder))
    clip = folder / "01_MASTERS" / "m.mov"
    clip.write_bytes(b"edited after the other tool sealed it" * 10)
    set_mtime_before_now(clip)
    sealer.run_scan_cycle(db, settings, now=NOW)
    job_id = sealer.request_seal(db, project_of(db, A).id, NOW)

    run_one(db, settings, job_id)

    a = project_of(db, A)
    assert a.state is ProjectState.NEEDS_REVIEW and a.review_reason
    assert job_of(db, job_id).state is JobState.DONE
    assert messages(db, job_id)[-1] == f"{sealer.REVIEW_LOG_PREFIX}{a.review_reason}"
    assert a.last_generation_no is None
    assert [p.name[:4] for p in (folder / "ascmhl").glob("*.mhl")] == ["0001"]  # nothing written


def test_accept_without_any_history_just_seals(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    a = project_of(db, A)
    db.set_state(a.id, ProjectState.NEEDS_REVIEW, review_reason="legacy mismatch")
    job_id = sealer.request_accept_new_version(db, a.id, NOW)

    run_one(db, settings, job_id)

    assert project_of(db, A).state is ProjectState.SEALED
    assert not any(m.startswith("previous history moved") for m in messages(db, job_id))
    assert not (settings.archive_root / A / "ascmhl_superseded").exists()
    reference_verifies(settings.archive_root / A)


def test_history_mirror_failure_after_a_write_is_only_a_warning(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = archive
    job_id = sealer.request_seal(db, project_of(db, A).id, NOW)

    def broken(project_root: Path, mirror: Path) -> bool:
        raise OSError("config volume full")

    monkeypatch.setattr(history_mirror, "sync_mirror", broken)
    run_one(db, settings, job_id)

    assert job_of(db, job_id).state is JobState.DONE
    assert project_of(db, A).state is ProjectState.SEALED
    warnings = [e.msg for e in db.get_job_log(job_id) if e.level == "warning"]
    assert warnings == ["history mirror not updated: config volume full"]
    reference_verifies(settings.archive_root / A)  # the generation itself is on the archive


# --- append -----------------------------------------------------------------------------------


def test_append_with_a_modified_file_sends_the_project_to_review(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / A
    seal(db, settings, A)
    clip = folder / "01_MASTERS" / "m.mov"
    clip.write_bytes(b"changed" * 30)
    set_mtime_before_now(clip)
    add_file(folder, "01_MASTERS/new.mov", b"n" * 50)
    job_id = sealer.enqueue(db, project_of(db, A).id, JobKind.APPEND, Trigger.AUTO, NOW)

    run_one(db, settings, job_id)

    a = project_of(db, A)
    assert a.state is ProjectState.NEEDS_REVIEW
    assert a.review_reason is not None and "1 modified since the last seal" in a.review_reason
    assert [(i.rel_path, i.change) for i in db.get_review_items(a.id)] == [
        ("01_MASTERS/m.mov", ChangeKind.MODIFIED)
    ]
    assert job_of(db, job_id).state is JobState.DONE
    assert a.last_generation_no == 1 and len(list((folder / "ascmhl").glob("*.mhl"))) == 1


def test_append_with_nothing_new_writes_no_generation(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / A
    seal(db, settings, A)
    job_id = sealer.enqueue(db, project_of(db, A).id, JobKind.APPEND, Trigger.AUTO, NOW)
    db.set_state(project_of(db, A).id, ProjectState.QUEUED)

    run_one(db, settings, job_id)

    assert job_of(db, job_id).state is JobState.DONE
    assert "nothing to append" in messages(db, job_id)
    after = project_of(db, A)
    assert after.state is ProjectState.SEALED and after.last_generation_no == 1
    assert len(list((folder / "ascmhl").glob("*.mhl"))) == 1


def test_append_of_a_file_that_contradicts_its_legacy_mhl_goes_to_review(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / A
    seal(db, settings, A)
    add_file(folder, "02_OCF/new.mov", b"n" * 80)
    _legacy_mhl(folder, {"new.mov": hashlib.md5(b"what the offload read").hexdigest()})
    job_id = sealer.enqueue(db, project_of(db, A).id, JobKind.APPEND, Trigger.AUTO, NOW)

    run_one(db, settings, job_id)

    a = project_of(db, A)
    assert a.state is ProjectState.NEEDS_REVIEW
    assert a.review_reason is not None
    assert a.review_reason.startswith("1 files do not match their legacy MHL 1.x: ")
    assert "02_OCF/new.mov" in a.review_reason
    assert [(i.rel_path, i.change) for i in db.get_review_items(a.id)] == [
        ("02_OCF/new.mov", ChangeKind.MODIFIED)
    ]
    assert a.last_generation_no == 1  # nothing written


# --- verify -----------------------------------------------------------------------------------


def test_verify_where_the_history_disagrees_with_sealed_files_goes_to_review(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / A
    a = seal(db, settings, A)
    clip = folder / "01_MASTERS" / "m.mov"
    clip.write_bytes(b"rewritten later" * 20)
    set_mtime_before_now(clip)
    st = clip.stat()
    # sealed_files believes the new bytes were sealed, but the ascmhl history still says otherwise
    db.replace_sealed_files(
        a.id,
        [
            SealedFile(
                "01_MASTERS/m.mov",
                st.st_size,
                st.st_mtime_ns,
                xxhash.xxh128(clip.read_bytes()).hexdigest(),
            )
        ],
    )
    job_id = sealer.enqueue(db, a.id, JobKind.VERIFY, Trigger.AUTO, NOW)

    run_one(db, settings, job_id)

    after = project_of(db, A)
    assert after.state is ProjectState.NEEDS_REVIEW
    assert after.review_reason is not None
    assert after.review_reason.startswith(sealer.VERIFICATION_PREFIX)
    assert job_of(db, job_id).state is JobState.DONE
    assert messages(db, job_id)[-1].startswith(sealer.REVIEW_LOG_PREFIX)
    assert after.last_generation_no == 1 and after.last_verified_at is None
    assert len(list((folder / "ascmhl").glob("*.mhl"))) == 1  # no generation written


# --- root manifest job ------------------------------------------------------------------------


def run_root_job(db: Database, settings: Settings) -> int:
    job_id = db.enqueue_job(JobKind.ROOT_MANIFEST, None, Trigger.AUTO, 5, NOW)
    run_one(db, settings, job_id)
    return job_id


def test_root_manifest_job_fails_when_the_share_is_not_there(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    shutil.rmtree(settings.archive_root)

    job_id = run_root_job(db, settings)

    job = job_of(db, job_id)
    assert job.state is JobState.FAILED
    assert job.error is not None and job.error.startswith("ArchiveUnavailableError: ")
    entries = db.get_job_log(job_id)
    assert entries[-1].level == "error" and entries[-1].msg == job.error


def test_root_manifest_job_sets_orphans_aside_and_reports_failures(
    archive: tuple[Settings, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = archive
    seal(db, settings, A)
    first = run_root_job(db, settings)
    assert job_of(db, first).state is JobState.DONE
    root_asc = settings.archive_root / "ascmhl"
    assert list(root_asc.glob("*.mhl"))
    (root_asc / "0099_stray.mhl").write_text("<x/>")

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("cannot write the root manifest")

    monkeypatch.setattr(rootmanifest, "refresh_root_manifest", broken)
    second = run_root_job(db, settings)

    assert not (root_asc / "0099_stray.mhl").exists()
    assert (root_asc / "0099_stray.mhl.orphan").read_text() == "<x/>"
    log = db.get_job_log(second)
    assert [(e.level, e.msg) for e in log] == [
        ("warning", "orphan root manifest renamed to 0099_stray.mhl.orphan"),
        ("error", "RuntimeError: cannot write the root manifest"),
    ]
    assert job_of(db, second).state is JobState.FAILED


def test_job_sets_an_orphan_manifest_aside_before_numbering_the_next_generation(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / A
    a = seal(db, settings, A)
    (folder / "ascmhl" / "0002_stray.mhl").write_text("<x/>")
    job_id = sealer.enqueue(db, a.id, JobKind.VERIFY, Trigger.AUTO, NOW)

    run_one(db, settings, job_id)

    assert "orphan manifest renamed to 0002_stray.mhl.orphan (issue #1)" in messages(db, job_id)
    after = project_of(db, A)
    assert after.state is ProjectState.SEALED and after.last_generation_no == 2
    reference_verifies(folder)


def test_seal_sets_aside_an_ascmhl_folder_that_has_no_chain(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    folder = settings.archive_root / A
    (folder / "ascmhl").mkdir()  # left by a crash before the very first generation
    job_id = sealer.request_seal(db, project_of(db, A).id, NOW)

    run_one(db, settings, job_id)

    log = messages(db, job_id)
    assert job_of(db, job_id).state is JobState.DONE, log
    assert any(m.startswith("ascmhl/ without chain moved to ") for m in log)
    assert project_of(db, A).last_generation_no == 1
    assert len(list((folder / "ascmhl_superseded").iterdir())) == 1
    reference_verifies(folder)
