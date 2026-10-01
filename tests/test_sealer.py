"""sealer.py: state table, orphan manifests (issue #1) and where superseded histories go (D17)."""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import xxhash

from helpers_ascmhl import run_cli
from mhl_sentinel import sealer
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, ProjectRow
from mhl_sentinel.mhlwriter import SUPERSEDED_DIR, write_project_generation
from mhl_sentinel.models import FileStat, JobKind, JobState, ProjectState, ScanDiff, Trigger

NOW = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def row(state: ProjectState, *, sealed: bool = True, preexisting: bool = False) -> ProjectRow:
    return ProjectRow(
        id=1, rel_path="2025/2025-01_CLIENTE-CAMPANA", name="2025-01_CLIENTE-CAMPANA",
        state=state, preexisting=preexisting, first_seen="2026-01-01T00:00:00.000000Z",
        last_scan_at=None, last_change_at=None, stable_since=None,
        last_generation_no=1 if sealed else None, last_sealed_at=None, last_verified_at=None,
        file_count=None, total_bytes=None, error=None, review_reason="old reason",
    )  # fmt: skip


F = FileStat("a.mov", 10, 1)
F2 = FileStat("a.mov", 11, 1)


def diff(
    added: bool = False, modified: bool = False, deleted: bool = False, stable: bool = True
) -> ScanDiff:
    d = ScanDiff(files=[F], stable=stable, newest_mtime_ns=0)
    if added:
        d.added = [FileStat("new.mov", 1, 1)]
    if modified:
        d.modified = [(F, F2)]
    if deleted:
        d.deleted = [FileStat("gone.mov", 1, 1)]
    return d


S = Settings(settle_hours=1)


@pytest.mark.parametrize(
    ("state", "sealed", "d", "expected"),
    [
        (ProjectState.IGNORED, True, diff(modified=True), ProjectState.IGNORED),
        (ProjectState.QUEUED, True, diff(modified=True), ProjectState.QUEUED),
        (ProjectState.HASHING, False, diff(added=True), ProjectState.HASHING),
        (ProjectState.UNSEALED, False, diff(added=True), ProjectState.UNSEALED),
        (ProjectState.SEALED, True, diff(), ProjectState.SEALED),
        (ProjectState.SEALED, True, diff(added=True), ProjectState.CHANGED),
        (ProjectState.SEALED, True, diff(added=True, deleted=True), ProjectState.NEEDS_REVIEW),
        (ProjectState.SEALED, True, diff(modified=True), ProjectState.NEEDS_REVIEW),
        (ProjectState.CHANGED, True, diff(), ProjectState.SEALED),
        (ProjectState.CHANGED, True, diff(added=True), ProjectState.CHANGED),
        (ProjectState.NEEDS_REVIEW, True, diff(), ProjectState.NEEDS_REVIEW),
        (ProjectState.ERROR, True, diff(added=True), ProjectState.CHANGED),
        (ProjectState.ERROR, False, diff(added=True), ProjectState.UNSEALED),
        (ProjectState.ERROR, True, diff(), ProjectState.SEALED),
    ],
)
def test_classify_matrix(state: ProjectState, sealed: bool, d: ScanDiff, expected: Any) -> None:
    got, reason = sealer.classify(row(state, sealed=sealed), d, S, NOW)
    assert got is expected
    if expected is ProjectState.NEEDS_REVIEW and state is ProjectState.SEALED:
        assert reason is not None and "since the last seal" in reason
    if expected in (ProjectState.SEALED, ProjectState.CHANGED, ProjectState.UNSEALED):
        assert reason is None


def test_review_reason_lists_at_most_ten_paths() -> None:
    d = ScanDiff(deleted=[FileStat(f"f{i:02d}.mov", 1, 1) for i in range(13)])
    reason = sealer.review_reason_for(d)
    assert reason.startswith("13 deleted") and "f09.mov" in reason and "f10.mov" not in reason
    assert reason.endswith("(+3 more)")


@pytest.mark.parametrize(
    ("state", "preexisting", "stable", "expected"),
    [
        (ProjectState.UNSEALED, False, True, True),
        (ProjectState.UNSEALED, True, True, False),  # D15: preexisting never automatic
        (ProjectState.UNSEALED, False, False, False),  # not stable between scans
        (ProjectState.CHANGED, True, True, True),  # appends do not care about preexisting
        (ProjectState.CHANGED, False, False, False),
        (ProjectState.SEALED, False, True, False),
        (ProjectState.NEEDS_REVIEW, False, True, False),
    ],
)
def test_should_auto_enqueue(
    state: ProjectState, preexisting: bool, stable: bool, expected: bool
) -> None:
    project = row(state, sealed=state is not ProjectState.UNSEALED, preexisting=preexisting)
    assert sealer.should_auto_enqueue(project, diff(stable=stable), S, NOW) is expected


def test_settle_time_counts_from_newest_mtime() -> None:
    d = diff()
    d.newest_mtime_ns = int((NOW - timedelta(minutes=30)).timestamp() * 1e9)
    project = row(ProjectState.UNSEALED, sealed=False)
    assert not sealer.should_auto_enqueue(project, d, S, NOW)
    assert sealer.should_auto_enqueue(project, d, S, NOW + timedelta(minutes=31))


# --- requests and scan cycle on a tiny archive -------------------------------------------------


def make_archive(tmp_path: Path) -> tuple[Settings, Database]:
    root = tmp_path / "archive"
    for name in ("2025-01_CLIENTE-CAMPANA", "2025-02_CLIENTE-OTRA"):
        (root / "2025" / name / "01_MASTERS").mkdir(parents=True)
        (root / "2025" / name / "01_MASTERS" / "m.mov").write_bytes(name.encode() * 100)
    settings = Settings(archive_root=root, config_dir=tmp_path / "config", settle_hours=0)
    return settings, Database(settings.db_path).open()


def open_gate() -> threading.Event:
    gate = threading.Event()
    gate.set()
    return gate


def run_all_jobs(db: Database, settings: Settings) -> None:
    while (job := db.next_job(NOW)) is not None:
        sealer.run_job(db, settings, job, gate=open_gate(), stop=threading.Event())


def test_requests_and_missing_folder(tmp_path: Path) -> None:
    settings, db = make_archive(tmp_path)
    summary = sealer.run_scan_cycle(db, settings, now=NOW)
    assert summary.discovered == 2 and not summary.enqueued
    first, second = db.list_projects()
    assert first.preexisting and first.state is ProjectState.UNSEALED

    with pytest.raises(sealer.SealerError):
        sealer.request_accept_new_version(db, first.id, NOW)
    sealer.request_seal(db, first.id, NOW)
    assert db.get_project(first.id).state is ProjectState.QUEUED  # type: ignore[union-attr]
    sealer.request_ignore(db, second.id, NOW)
    run_all_jobs(db, settings)
    sealed = db.get_project(first.id)
    assert sealed is not None and sealed.state is ProjectState.SEALED
    assert sealed.last_generation_no == 1
    assert db.get_kv(sealer.ROOT_MANIFEST_STALE_KEY) == "1"

    sealer.request_unignore(db, second.id, NOW)
    assert db.get_project(second.id).state is ProjectState.UNSEALED  # type: ignore[union-attr]

    shutil.rmtree(settings.archive_root / first.rel_path)
    summary = sealer.run_scan_cycle(db, settings, now=NOW)
    assert summary.missing == [first.rel_path]
    gone = db.get_project(first.id)
    assert gone is not None and gone.state is ProjectState.ERROR and gone.error == "folder missing"


def test_cancel_returns_the_project_to_its_previous_state(tmp_path: Path) -> None:
    """D53: Cancel withdraws a manual Seal/Accept; automatic jobs are not cancellable."""
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    first, second = db.list_projects()
    with pytest.raises(sealer.SealerError):
        sealer.request_cancel(db, first.id, NOW)  # nothing queued

    job_id = sealer.request_seal(db, first.id, NOW)
    assert sealer.request_cancel(db, first.id, NOW) == job_id
    project = db.get_project(first.id)
    assert project is not None and project.state is ProjectState.UNSEALED
    job = db.get_job(job_id)
    assert job is not None and job.state is JobState.CANCELLED and job.finished_at
    assert db.next_job(NOW) is None

    # Seal, run it, then force a review and cancel the Accept: review and reason survive.
    sealer.request_seal(db, first.id, NOW)
    run_all_jobs(db, settings)
    db.set_state(first.id, ProjectState.NEEDS_REVIEW, review_reason="modified: 01_MASTERS/a.mov")
    sealer.request_accept_new_version(db, first.id, NOW)
    sealer.request_cancel(db, first.id, NOW)
    project = db.get_project(first.id)
    assert project is not None and project.state is ProjectState.NEEDS_REVIEW
    assert project.review_reason == "modified: 01_MASTERS/a.mov"

    # An automatic job (append after settle) is not offered for cancellation.
    sealer.enqueue(db, second.id, JobKind.APPEND, Trigger.AUTO, NOW)
    db.set_state(second.id, ProjectState.QUEUED)
    assert sealer.cancellable_job(db, db.get_project(second.id)) is None  # type: ignore[arg-type]
    with pytest.raises(sealer.SealerError):
        sealer.request_cancel(db, second.id, NOW)


def test_new_project_after_first_discovery_seals_itself(tmp_path: Path) -> None:
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    new = settings.archive_root / "2025" / "2025-03_CLIENTE-NUEVO"
    new.mkdir()
    (new / "a.mov").write_bytes(b"x" * 10)
    sealer.run_scan_cycle(db, settings, now=NOW)  # first sight: not stable yet
    project = db.get_project("2025/2025-03_CLIENTE-NUEVO")
    assert project is not None and not project.preexisting
    assert project.state is ProjectState.UNSEALED
    summary = sealer.run_scan_cycle(db, settings, now=NOW)
    assert summary.enqueued == [("2025/2025-03_CLIENTE-NUEVO", JobKind.SEAL)]
    run_all_jobs(db, settings)
    assert db.get_project(project.id).state is ProjectState.SEALED  # type: ignore[union-attr]


def test_stopped_job_goes_back_to_queue(tmp_path: Path) -> None:
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    project = db.list_projects()[0]
    job_id = sealer.request_seal(db, project.id, NOW)
    stop = threading.Event()
    stop.set()
    job = db.get_job(job_id)
    assert job is not None
    sealer.run_job(db, settings, job, gate=open_gate(), stop=stop)
    assert db.get_job(job_id).state is JobState.QUEUED  # type: ignore[union-attr]
    assert db.get_project(project.id).state is ProjectState.QUEUED  # type: ignore[union-attr]
    assert not (settings.archive_root / project.rel_path / "ascmhl").exists()


# --- D57: Cancel on a running Seal/Accept ------------------------------------------------------


def _several_files(settings: Settings, project: ProjectRow, n: int = 5) -> None:
    folder = settings.archive_root / project.rel_path / "02_OCF"
    folder.mkdir()
    for i in range(n):
        (folder / f"clip_{i}.bin").write_bytes(bytes([i]) * 1000)


def _cached_files(db: Database, settings: Settings, project: ProjectRow) -> int:
    root = settings.archive_root / project.rel_path
    count = 0
    for p in root.rglob("*"):
        if p.is_file():
            st = p.stat()
            rel = p.relative_to(root).as_posix()
            count += bool(db.get_cached_hashes(project.id, rel, st.st_size, st.st_mtime_ns))
    return count


def _cancel_after_first_file(cancel: threading.Event) -> sealer.ProgressFn:
    def on_progress(files_done: int, files_total: int, bytes_done: int, bytes_total: int) -> None:
        del files_total, bytes_done, bytes_total
        if files_done == 1:
            cancel.set()

    return on_progress


def test_cancel_while_reading_aborts_the_seal_and_keeps_the_hashes(tmp_path: Path) -> None:
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    project = db.list_projects()[0]
    _several_files(settings, project)
    job_id = sealer.request_seal(db, project.id, NOW)
    job = db.get_job(job_id)
    assert job is not None
    cancel = threading.Event()
    sealer.run_job(
        db,
        settings,
        job,
        gate=open_gate(),
        stop=threading.Event(),
        cancel=cancel,
        on_progress=_cancel_after_first_file(cancel),
    )
    done = db.get_job(job_id)
    assert done is not None and done.state is JobState.CANCELLED and done.finished_at
    assert 0 < done.files_done < done.files_total == 6
    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.UNSEALED
    assert not (settings.archive_root / project.rel_path / "ascmhl").exists()
    assert _cached_files(db, settings, project) == 1  # checkpoint kept for the next Seal (D28)
    assert any("cancelled while reading" in e.msg for e in db.get_job_log(job_id))


def test_cancel_while_reading_an_accept_goes_back_to_review(tmp_path: Path) -> None:
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    project = db.list_projects()[0]
    sealer.request_seal(db, project.id, NOW)
    run_all_jobs(db, settings)
    _several_files(settings, project)
    db.set_state(project.id, ProjectState.NEEDS_REVIEW, review_reason="added: 02_OCF/clip_0.bin")
    job_id = sealer.request_accept_new_version(db, project.id, NOW)
    job = db.get_job(job_id)
    assert job is not None
    cancel = threading.Event()
    stop = threading.Event()
    stop.set()  # SIGTERM at the same time: cancel wins
    sealer.run_job(db, settings, job, gate=open_gate(), stop=stop, cancel=cancel)
    assert db.get_job(job_id).state is JobState.QUEUED  # type: ignore[union-attr]
    cancel.set()
    job = db.get_job(job_id)
    assert job is not None
    sealer.run_job(db, settings, job, gate=open_gate(), stop=stop, cancel=cancel)
    assert db.get_job(job_id).state is JobState.CANCELLED  # type: ignore[union-attr]
    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.NEEDS_REVIEW
    assert after.review_reason == "added: 02_OCF/clip_0.bin"
    assert after.last_generation_no == 1  # the history was not retired
    history = settings.archive_root / project.rel_path / "ascmhl"
    assert sorted(p.name for p in history.iterdir() if p.name.startswith(".")) == []


def test_cancel_while_paused_by_working_hours(tmp_path: Path) -> None:
    """The gate is closed (working hours): the hasher waits inside the job; Cancel ends it."""
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    project = db.list_projects()[0]
    job_id = sealer.request_seal(db, project.id, NOW)
    job = db.get_job(job_id)
    assert job is not None
    cancel = threading.Event()
    worker = threading.Thread(
        target=sealer.run_job,
        args=(db, settings, job),
        kwargs={"gate": threading.Event(), "stop": threading.Event(), "cancel": cancel},
    )
    worker.start()
    deadline = time.monotonic() + 10
    while db.get_project(project.id).state is not ProjectState.HASHING:  # type: ignore[union-attr]
        assert time.monotonic() < deadline
        time.sleep(0.01)
    cancel.set()
    worker.join(10)
    assert not worker.is_alive()
    assert db.get_job(job_id).state is JobState.CANCELLED  # type: ignore[union-attr]
    assert db.get_project(project.id).state is ProjectState.UNSEALED  # type: ignore[union-attr]


def test_cancel_is_ignored_for_automatic_and_verify_jobs(tmp_path: Path) -> None:
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    project = db.list_projects()[0]
    sealer.request_seal(db, project.id, NOW)
    run_all_jobs(db, settings)
    job_id = sealer.enqueue(db, project.id, JobKind.VERIFY, Trigger.AUTO, NOW)
    job = db.get_job(job_id)
    assert job is not None
    cancel = threading.Event()
    cancel.set()
    sealer.run_job(db, settings, job, gate=open_gate(), stop=threading.Event(), cancel=cancel)
    assert db.get_job(job_id).state is JobState.DONE  # type: ignore[union-attr]
    assert db.get_project(project.id).state is ProjectState.SEALED  # type: ignore[union-attr]


def test_request_cancel_refuses_a_running_job(tmp_path: Path) -> None:
    """The CLI/DB path cannot half-cancel: a running job belongs to the supervisor (D57)."""
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    project = db.list_projects()[0]
    job_id = sealer.request_seal(db, project.id, NOW)
    db.set_job_state(job_id, JobState.RUNNING, NOW)
    db.set_state(project.id, ProjectState.HASHING)
    hashing = db.get_project(project.id)
    assert hashing is not None
    running = sealer.cancellable_job(db, hashing)
    assert running is not None and running.id == job_id
    with pytest.raises(sealer.SealerError, match="through the supervisor"):
        sealer.request_cancel(db, project.id, NOW)


# --- issue #1: orphan manifests ----------------------------------------------------------------


def xxh_all(project: Path) -> dict[str, dict[str, str]]:
    return {
        p.relative_to(project).as_posix(): {"xxh128": xxhash.xxh128(p.read_bytes()).hexdigest()}
        for p in sorted(project.rglob("*"))
        if p.is_file() and p.relative_to(project).parts[0] not in ("ascmhl", SUPERSEDED_DIR)
    }


def test_orphan_manifest_is_set_aside_and_numbering_comes_from_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "2025-01_CLIENTE-CAMPANA"
    project.mkdir()
    (project / "a.mov").write_bytes(b"a" * 100)
    write_project_generation(project, xxh_all(project))

    (project / "b.mov").write_bytes(b"b" * 50)
    real_replace = os.replace
    calls: list[str] = []

    def crash_on_chain(src: Any, dst: Any) -> None:
        calls.append(str(dst))
        if str(dst).endswith("ascmhl_chain.xml"):
            raise OSError("simulated crash between the two renames")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", crash_on_chain)
    with pytest.raises(OSError, match="simulated crash"):
        write_project_generation(project, {"b.mov": xxh_all(project)["b.mov"]}, partial=True)
    monkeypatch.setattr(os, "replace", real_replace)
    orphan = [p for p in (project / "ascmhl").glob("0002_*.mhl")]
    assert len(orphan) == 1  # manifest renamed, chain not: the orphan of issue #1

    moved = sealer.quarantine_orphan_manifests(project)
    assert [p.name for p in moved] == [orphan[0].name + ".orphan"]
    assert not list((project / "ascmhl").glob("0002_*.mhl"))
    assert sealer.quarantine_orphan_manifests(project) == []  # idempotent

    gen2 = write_project_generation(project, {"b.mov": xxh_all(project)["b.mov"]}, partial=True)
    assert gen2.name.startswith("0002_")
    verify = run_cli("ascmhl-debug", "verify", project)
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_scan_cycle_reports_orphans(tmp_path: Path) -> None:
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    project = db.list_projects()[0]
    sealer.request_seal(db, project.id, NOW)
    run_all_jobs(db, settings)
    asc = settings.archive_root / project.rel_path / "ascmhl"
    stray = asc / "0002_fake_2026-10-01_000000Z.mhl"
    stray.write_bytes(next(asc.glob("0001_*.mhl")).read_bytes())
    summary = sealer.run_scan_cycle(db, settings, now=NOW)
    assert summary.orphans == [f"{project.rel_path}/ascmhl/{stray.name}.orphan"]
    assert db.get_project(project.id).state is ProjectState.SEALED  # type: ignore[union-attr]


# --- D17: where the superseded history can live ------------------------------------------------


def test_superseded_history_must_live_outside_ascmhl(tmp_path: Path) -> None:
    """Proof for the contract question: ascmhl 1.2 does NOT ignore ``ascmhl/superseded/``.

    ``MHLHistory.load_from_path`` walks ``ascmhl/`` recursively and loads every ``*.mhl`` as a
    generation (joining the bare name to ``ascmhl/``), so ``info`` and ``verify`` crash. A
    sibling ``ascmhl_superseded/`` plus the ignore pattern ``ascmhl_superseded/`` works.
    """
    project = tmp_path / "2025-01_CLIENTE-CAMPANA"
    (project / "01_MASTERS").mkdir(parents=True)
    (project / "01_MASTERS" / "a.mov").write_bytes(b"a" * 100)
    write_project_generation(project, xxh_all(project))
    time.sleep(1.1)  # distinct generation file names
    (project / "01_MASTERS" / "a.mov").write_bytes(b"A" * 120)  # "accepted" modification

    retired = sealer.retire_history(project, NOW)
    assert retired is not None and retired.parent.name == SUPERSEDED_DIR
    write_project_generation(project, xxh_all(project))  # DEFAULT_IGNORE_PATTERNS has it

    verify = run_cli("ascmhl-debug", "verify", project)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    info = run_cli("ascmhl", "info", "-v", project)
    assert info.returncode == 0, info.stdout + info.stderr
    assert info.stdout.count("Generation ") == 1, info.stdout

    # The other option of the contract, for the record: inside ascmhl/ the reference breaks.
    (project / "ascmhl" / "superseded").mkdir()
    shutil.move(str(retired), str(project / "ascmhl" / "superseded" / retired.name))
    for tool, args in (("ascmhl", ("info", "-v")), ("ascmhl-debug", ("verify",))):
        broken = run_cli(tool, *args, project)
        assert broken.returncode != 0, broken.stdout
        assert "FileNotFoundError" in broken.stdout + broken.stderr


# --- startup recovery after an abrupt recreation (SIGKILL between the two renames) -------------


def test_startup_recovery_sets_orphans_aside_and_removes_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, db = make_archive(tmp_path)
    sealer.run_scan_cycle(db, settings, now=NOW)
    project = db.list_projects()[0]
    sealer.request_seal(db, project.id, NOW)
    run_all_jobs(db, settings)
    folder = settings.archive_root / project.rel_path
    (folder / "01_MASTERS" / "b.mov").write_bytes(b"b" * 50)

    real_replace = os.replace

    def crash_on_chain(src: Any, dst: Any) -> None:
        if str(dst).endswith("ascmhl_chain.xml"):
            raise OSError("simulated SIGKILL between the two renames")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", crash_on_chain)
    with pytest.raises(OSError, match="simulated"):
        write_project_generation(
            folder, {"01_MASTERS/b.mov": xxh_all(folder)["01_MASTERS/b.mov"]}, partial=True
        )
    monkeypatch.setattr(os, "replace", real_replace)
    asc = folder / "ascmhl"
    orphan = next(asc.glob("0002_*.mhl"))
    # SIGKILL skips _commit's ``finally``: the temp files stay too.
    stale = [asc / f".{orphan.name}.tmp", asc / ".ascmhl_chain.xml.tmp"]
    for path in stale:
        path.write_bytes(b"<partial")
    (settings.archive_root / "ascmhl").mkdir()
    root_tmp = settings.archive_root / "ascmhl" / ".0001_root.mhl.tmp"
    root_tmp.write_bytes(b"<partial")

    result = sealer.recover_history_dirs(db, settings)
    rel = f"{project.rel_path}/ascmhl"
    assert result.orphans == [f"{rel}/{orphan.name}.orphan"]
    assert sorted(result.temp_files) == sorted(
        [f"{rel}/{p.name}" for p in stale] + [f"ascmhl/{root_tmp.name}"]
    )
    assert result.errors == []
    assert not any(p.exists() for p in [*stale, root_tmp])
    assert sealer.recover_history_dirs(db, settings) == sealer.FsRecovery()  # idempotent

    # The next generation is numbered from the chain and the history verifies.
    sealer.run_scan_cycle(db, settings, now=NOW)  # sees b.mov: changed
    sealer.run_scan_cycle(db, settings, now=NOW)  # stable (settle_hours=0): append queued
    run_all_jobs(db, settings)
    after = db.get_project(project.id)
    assert after is not None and after.state is ProjectState.SEALED
    assert after.last_generation_no == 2
    assert [p.name[:4] for p in sorted(asc.glob("*.mhl"))] == ["0001", "0002"]
    verify = run_cli("ascmhl-debug", "verify", folder)
    assert verify.returncode == 0, verify.stdout + verify.stderr
