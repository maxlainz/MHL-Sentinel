"""State database: pragmas, migrations, network-FS refusal and repository methods."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mhl_sentinel.clock import from_iso, to_iso, utcnow_iso
from mhl_sentinel.db import (
    MIGRATIONS,
    SCHEMA_VERSION,
    Database,
    NetworkFilesystemError,
    ReviewItem,
    SealedFile,
    VerifyResult,
    is_network_fs,
)
from mhl_sentinel.models import ChangeKind, FileStat, JobKind, JobState, ProjectState, Trigger

T0 = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
TABLES = {
    "projects",
    "files",
    "sealed_files",
    "file_hashes",
    "scans",
    "jobs",
    "job_log",
    "review_items",
    "verify_results",
    "settings_kv",
}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    with Database(tmp_path / "state.db", mounts_file=tmp_path / "no-mounts") as database:
        yield database


def test_clock_round_trip() -> None:
    assert to_iso(T0) == "2026-10-01T20:00:00.000000Z"
    assert from_iso(to_iso(T0)) == T0
    assert utcnow_iso().endswith("Z")
    with pytest.raises(ValueError):
        to_iso(datetime(2026, 10, 1))


def test_pragmas_and_schema(db: Database) -> None:
    conn = db.conn
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names >= TABLES
    assert db.user_version == SCHEMA_VERSION == len(MIGRATIONS) == 2


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    mounts = tmp_path / "no-mounts"
    with Database(path, mounts_file=mounts) as first:
        pid = first.upsert_project("2024-01_CLIENTE-CAMPANA", "2024-01_CLIENTE-CAMPANA", True, T0)
        assert first.migrate() == SCHEMA_VERSION
        assert first.migrate() == SCHEMA_VERSION
    with Database(path, mounts_file=mounts) as second:
        assert second.user_version == SCHEMA_VERSION
        project = second.get_project(pid)
        assert project is not None and project.preexisting


def test_forward_migration_from_previous_version(tmp_path: Path) -> None:
    """A /config left by the previous image (v1) is migrated by the next one (auto-update);
    re-opening is a no-op and the data survives."""
    path = tmp_path / "state.db"
    mounts = tmp_path / "no-mounts"
    with Database(path, mounts_file=mounts) as old:
        pid = old.upsert_project("2024-01_CLIENTE-CAMPANA", "2024-01_CLIENTE-CAMPANA", True, T0)
    future = (*MIGRATIONS, "CREATE TABLE future_slot (k TEXT PRIMARY KEY, v TEXT);")
    with Database(path, mounts_file=mounts) as new:
        assert new.migrate(future) == SCHEMA_VERSION + 1
        assert new.migrate(future) == SCHEMA_VERSION + 1  # idempotent
        assert new.get_project(pid) is not None
        names = {r[0] for r in new.conn.execute("SELECT name FROM sqlite_master")}
        assert "future_slot" in names
    with pytest.raises(RuntimeError, match="newer"):  # database newer than the app (rollback)
        Database(path, mounts_file=mounts).open()


def test_close_checkpoints_the_wal(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    db = Database(path, mounts_file=tmp_path / "no-mounts").open()
    for i in range(50):
        db.upsert_project(f"2024-{i:02d}_CLIENTE-CAMPANA", "x", False, T0)
    wal = path.with_name(path.name + "-wal")
    assert wal.exists() and wal.stat().st_size > 0
    db.close()
    assert not wal.exists() or wal.stat().st_size == 0
    with Database(path, mounts_file=tmp_path / "no-mounts") as again:
        assert len(again.list_projects()) == 50


def test_newer_schema_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    mounts = tmp_path / "no-mounts"
    with Database(path, mounts_file=mounts) as database:
        database.conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer"):
        Database(path, mounts_file=mounts).open()


MOUNTS = """\
sysfs /sys sysfs rw 0 0
/dev/sda1 / ext4 rw 0 0
/dev/sdb1 /config ext4 rw 0 0
//nas/share /archive cifs rw 0 0
nas:/export /mnt/nfs\\040dir nfs4 rw 0 0
user@host:/x /mnt/ssh fuse.sshfs rw 0 0
"""


def test_is_network_fs(tmp_path: Path) -> None:
    mounts = tmp_path / "mounts"
    mounts.write_text(MOUNTS, encoding="utf-8")
    assert not is_network_fs(Path("/config"), mounts)
    assert not is_network_fs(Path("/config/sub"), mounts)
    assert not is_network_fs(Path("/tmp"), mounts)
    assert is_network_fs(Path("/archive"), mounts)
    assert is_network_fs(Path("/archive/2024-01_CLIENTE-CAMPANA"), mounts)
    assert is_network_fs(Path("/mnt/nfs dir/state"), mounts)
    assert is_network_fs(Path("/mnt/ssh"), mounts)
    assert not is_network_fs(Path("/archive"), tmp_path / "missing")


def test_open_refuses_network_fs(tmp_path: Path) -> None:
    mounts = tmp_path / "mounts"
    mounts.write_text(f"//nas/share {tmp_path.resolve()} cifs rw 0 0\n", encoding="utf-8")
    with pytest.raises(NetworkFilesystemError):
        Database(tmp_path / "state.db", mounts_file=mounts).open()
    assert not (tmp_path / "state.db").exists()


def test_projects(db: Database) -> None:
    pid = db.upsert_project("A/2024-01_CLIENTE-CAMPANA", "2024-01_CLIENTE-CAMPANA", True, T0)
    again = db.upsert_project("A/2024-01_CLIENTE-CAMPANA", "renamed", False, T0 + timedelta(1))
    assert again == pid
    p = db.get_project("A/2024-01_CLIENTE-CAMPANA")
    assert p is not None
    assert p.id == pid and p.name == "renamed" and p.preexisting
    assert p.state is ProjectState.UNSEALED and p.first_seen == to_iso(T0)
    other = db.upsert_project("B/2024-02_CLIENTE-CAMPANA", "2024-02_CLIENTE-CAMPANA", False, T0)

    db.set_state(pid, ProjectState.NEEDS_REVIEW, review_reason="modified files")
    db.update_project_fields(pid, last_scan_at=T0, file_count=3, total_bytes=30)
    p = db.get_project(pid)
    assert p is not None
    assert p.state is ProjectState.NEEDS_REVIEW and p.review_reason == "modified files"
    assert p.last_scan_at == to_iso(T0) and p.file_count == 3
    assert [x.id for x in db.list_projects()] == [pid, other]
    assert [x.id for x in db.list_projects(ProjectState.NEEDS_REVIEW)] == [pid]
    db.set_state(pid, ProjectState.SEALED)
    p = db.get_project(pid)
    assert p is not None and p.review_reason is None
    with pytest.raises(ValueError):
        db.update_project_fields(pid, rel_path="x")
    assert db.get_project(999) is None


def test_files_and_sealed_files(db: Database) -> None:
    pid = db.upsert_project("P", "P", False, T0)
    db.replace_files(pid, [FileStat("a.mov", 10, 1), FileStat("b/c.wav", 20, 2)])
    db.replace_files(pid, [FileStat("a.mov", 11, 3)])
    assert db.get_files(pid) == {"a.mov": FileStat("a.mov", 11, 3)}
    rows = [SealedFile("a.mov", 11, 3, "ab" * 16), SealedFile("x", 0, 0, None)]
    db.replace_sealed_files(pid, rows)
    assert db.get_sealed_files(pid) == {r.rel_path: r for r in rows}


def test_cached_hash_lookup_and_prune(db: Database) -> None:
    pid = db.upsert_project("P", "P", False, T0)
    db.put_hash(pid, "a.mov", 10, 100, "xxh128", "aa", T0)
    db.put_hash(pid, "a.mov", 10, 100, "md5", "bb", T0)
    db.put_hash(pid, "b.mov", 5, 50, "xxh128", "cc", T0)
    assert db.get_cached_hashes(pid, "a.mov", 10, 100) == {"xxh128": "aa", "md5": "bb"}
    assert db.get_cached_hashes(pid, "a.mov", 10, 101) == {}  # mtime changed: no cache
    assert db.get_cached_hashes(pid, "a.mov", 11, 100) == {}  # size changed: no cache
    db.put_hash(pid, "a.mov", 12, 120, "xxh128", "dd", T0)  # rehash replaces the row
    assert db.get_cached_hashes(pid, "a.mov", 12, 120) == {"xxh128": "dd"}
    assert db.get_cached_hashes(pid, "a.mov", 10, 100) == {"md5": "bb"}
    removed = db.prune_hashes(pid, [FileStat("a.mov", 12, 120)])
    assert removed == 2  # stale a.mov md5 + b.mov
    assert db.get_cached_hashes(pid, "b.mov", 5, 50) == {}
    assert db.get_cached_hashes(pid, "a.mov", 12, 120) == {"xxh128": "dd"}


def test_scans(db: Database) -> None:
    pid = db.upsert_project("P", "P", False, T0)
    sid = db.start_scan("project", pid, T0)
    db.finish_scan(sid, files=3, bytes=30, added=1, status="ok", now=T0 + timedelta(seconds=5))
    row = db.conn.execute("SELECT * FROM scans WHERE id = ?", (sid,)).fetchone()
    assert row["files"] == 3 and row["added"] == 1 and row["status"] == "ok"
    assert row["finished_at"] == to_iso(T0 + timedelta(seconds=5))
    assert db.start_scan("discovery", None, T0) != sid


def test_duplicate_job_rule(db: Database) -> None:
    pid = db.upsert_project("P", "P", False, T0)
    j1 = db.enqueue_job(JobKind.SEAL, pid, Trigger.AUTO, 10, T0)
    assert db.enqueue_job(JobKind.SEAL, pid, Trigger.MANUAL, 100, T0) == j1
    j2 = db.enqueue_job(JobKind.VERIFY, pid, Trigger.AUTO, 1, T0)
    assert j2 != j1
    db.set_job_state(j1, JobState.RUNNING, T0)
    assert db.enqueue_job(JobKind.SEAL, pid, Trigger.AUTO, 10, T0) == j1
    db.set_job_state(j1, JobState.DONE, T0)
    j3 = db.enqueue_job(JobKind.SEAL, pid, Trigger.AUTO, 10, T0)
    assert j3 not in (j1, j2)
    # Jobs without project (root manifest) are deduplicated too.
    r1 = db.enqueue_job(JobKind.ROOT_MANIFEST, None, Trigger.AUTO, 0, T0)
    assert db.enqueue_job(JobKind.ROOT_MANIFEST, None, Trigger.AUTO, 0, T0) == r1


def test_next_job_order_progress_and_log(db: Database) -> None:
    a = db.upsert_project("A", "A", False, T0)
    b = db.upsert_project("B", "B", False, T0)
    low = db.enqueue_job(JobKind.VERIFY, a, Trigger.AUTO, 1, T0)
    old = db.enqueue_job(JobKind.SEAL, a, Trigger.AUTO, 10, T0)
    new = db.enqueue_job(JobKind.SEAL, b, Trigger.AUTO, 10, T0 + timedelta(minutes=1))
    nxt = db.next_job(T0)
    assert nxt is not None and nxt.id == old and nxt.kind is JobKind.SEAL
    db.set_job_state(old, JobState.RUNNING, T0)
    nxt = db.next_job(T0)
    assert nxt is not None and nxt.id == new
    db.update_job_progress(old, 1, 4, 100, 400)
    db.log(old, "info", "hashing a.mov", T0)
    db.log(old, "warning", "file changed", T0)
    job = db.get_job(old)
    assert job is not None
    assert (job.files_done, job.files_total, job.bytes_done, job.bytes_total) == (1, 4, 100, 400)
    assert job.started_at == to_iso(T0) and job.state is JobState.RUNNING
    assert [e.msg for e in db.get_job_log(old)] == ["hashing a.mov", "file changed"]
    db.set_job_state(old, JobState.FAILED, T0, error="boom")
    job = db.get_job(old)
    assert job is not None and job.finished_at == to_iso(T0) and job.error == "boom"
    assert [j.id for j in db.list_jobs(2)] == [new, old]
    assert low in [j.id for j in db.list_jobs(10)]


def test_requeue_running_jobs(db: Database) -> None:
    pid = db.upsert_project("P", "P", False, T0)
    j = db.enqueue_job(JobKind.APPEND, pid, Trigger.AUTO, 5, T0)
    db.set_job_state(j, JobState.RUNNING, T0)
    assert db.next_job(T0) is None
    assert db.requeue_running_jobs() == 1
    job = db.get_job(j)
    assert job is not None and job.state is JobState.QUEUED and job.started_at is None
    nxt = db.next_job(T0)
    assert nxt is not None and nxt.id == j
    assert db.requeue_running_jobs() == 0


def test_review_items_and_kv(db: Database) -> None:
    pid = db.upsert_project("P", "P", False, T0)
    items = [
        ReviewItem("b.mov", ChangeKind.DELETED, old_size=5, old_mtime_ns=1),
        ReviewItem("a.mov", ChangeKind.MODIFIED, 5, 6, 1, 2),
    ]
    db.replace_review_items(pid, items)
    assert db.get_review_items(pid) == sorted(items, key=lambda i: i.rel_path)
    db.clear_review_items(pid)
    assert db.get_review_items(pid) == []
    assert db.get_kv("first_discovery_done") is None
    db.set_kv("first_discovery_done", "1")
    db.set_kv("first_discovery_done", "2")
    assert db.get_kv("first_discovery_done") == "2"


def test_transaction_rolls_back_and_nests(db: Database) -> None:
    with pytest.raises(RuntimeError), db.transaction():
        db.set_kv("k", "v")  # nested: joins the outer transaction
        raise RuntimeError
    assert db.get_kv("k") is None


def test_concurrent_writers(db: Database) -> None:
    pid = db.upsert_project("P", "P", False, T0)

    def worker(n: int) -> None:
        for i in range(50):
            db.put_hash(pid, f"f{n}-{i}", i, i, "xxh128", "00", T0)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    count = db.conn.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0]
    assert count == 200


def test_migration_v1_to_v2_on_a_database_of_the_previous_image(tmp_path: Path) -> None:
    """D58, D63: the v2 script only adds columns; rows written by a v1 image survive with the
    new columns empty (no project is missing, no job bypasses the working hours)."""
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.executescript(f"BEGIN;\n{MIGRATIONS[0]}\nPRAGMA user_version = 1;\nCOMMIT;")
    conn.execute(
        "INSERT INTO projects (rel_path, name, state, preexisting, first_seen, error)"
        " VALUES ('2024/2024-01_CLIENTE-CAMPANA', '2024-01_CLIENTE-CAMPANA', 'error', 1, ?,"
        " 'folder missing')",
        (to_iso(T0),),
    )
    conn.execute(
        "INSERT INTO jobs (kind, project_id, trigger, state, priority, created_at)"
        " VALUES ('verify', 1, 'auto', 'queued', 10, ?)",
        (to_iso(T0),),
    )
    conn.close()

    with Database(path, mounts_file=tmp_path / "no-mounts") as db:
        assert db.user_version == 2
        project = db.get_project("2024/2024-01_CLIENTE-CAMPANA")
        assert project is not None and project.state is ProjectState.ERROR
        assert project.missing_since is None and project.state_before_missing is None
        job = db.next_job(T0)
        assert job is not None and job.kind is JobKind.VERIFY and job.bypass_hours is False
        assert db.next_job(T0, bypass_only=True) is None
        db.update_project_fields(
            project.id,
            state=ProjectState.MISSING,
            missing_since=T0,
            state_before_missing=ProjectState.SEALED,
        )
        again = db.get_project(project.id)
        assert again is not None and again.state_before_missing is ProjectState.SEALED
        assert again.missing_since == to_iso(T0)


def test_bypass_jobs_promotion_and_finished_jobs(db: Database) -> None:
    pid = db.upsert_project("2024-01_CLIENTE-CAMPANA", "2024-01_CLIENTE-CAMPANA", True, T0)
    auto = db.enqueue_job(JobKind.VERIFY, pid, Trigger.AUTO, 10, T0)
    seal = db.enqueue_job(JobKind.SEAL, None, Trigger.MANUAL, 130, T0)
    assert db.next_job(T0, bypass_only=True) is None
    # A manual request over a queued automatic job promotes it instead of duplicating it.
    assert db.enqueue_job(JobKind.VERIFY, pid, Trigger.MANUAL, 110, T0, bypass_hours=True) == auto
    promoted = db.get_job(auto)
    assert promoted is not None and promoted.trigger is Trigger.MANUAL
    assert promoted.priority == 110 and promoted.bypass_hours
    assert db.next_job(T0, bypass_only=True) == promoted
    next_any = db.next_job(T0)
    assert next_any is not None and next_any.id == seal
    # An automatic duplicate never demotes it.
    assert db.enqueue_job(JobKind.VERIFY, pid, Trigger.AUTO, 10, T0) == auto
    assert db.get_job(auto) == promoted

    # Seal now (D73): the flag is set once; a second call reports it was already there.
    assert db.set_job_bypass_hours(seal)
    flagged = db.get_job(seal)
    assert flagged is not None and flagged.bypass_hours and flagged.state is JobState.QUEUED
    assert not db.set_job_bypass_hours(seal)
    assert not db.set_job_bypass_hours(9999)

    done = db.record_finished_job(JobKind.RETIRE, None, Trigger.MANUAL, JobState.DONE, T0)
    row = db.get_job(done)
    assert row is not None and row.state is JobState.DONE and row.project_id is None
    assert row.started_at == row.finished_at == to_iso(T0)
    assert db.next_job(T0) == db.get_job(seal)  # never picked by the hasher


def test_rename_and_delete_project_cascade(db: Database) -> None:
    pid = db.upsert_project("2024/2024-01_A", "2024-01_A", True, T0)
    other = db.upsert_project("2024/2024-02_B", "2024-02_B", True, T0)
    db.replace_files(pid, [FileStat("a.mov", 1, 1)])
    db.replace_sealed_files(pid, [SealedFile("a.mov", 1, 1, "aa")])
    db.put_hash(pid, "a.mov", 1, 1, "xxh128", "aa", T0)
    db.replace_review_items(pid, [ReviewItem("a.mov", ChangeKind.DELETED, 1)])
    scan = db.start_scan("project", pid, T0)
    db.finish_scan(scan, files=1, bytes=1, now=T0)
    job = db.enqueue_job(JobKind.SEAL, pid, Trigger.MANUAL, 130, T0)
    db.log(job, "info", "x", T0)
    db.replace_files(other, [FileStat("b.mov", 1, 1)])

    db.rename_project(pid, "2025/2024-01_A_RENAMED", "2024-01_A_RENAMED")
    moved = db.get_project(pid)
    assert moved is not None and moved.rel_path == "2025/2024-01_A_RENAMED"
    assert moved.name == "2024-01_A_RENAMED" and db.get_files(pid)  # data follows the row

    db.delete_project(pid)
    assert db.get_project(pid) is None
    for table in ("files", "sealed_files", "file_hashes", "review_items", "scans", "jobs"):
        n = db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (pid,))
        assert n.fetchone()[0] == 0, table
    assert db.conn.execute("SELECT COUNT(*) FROM job_log").fetchone()[0] == 0
    assert db.get_files(other)  # the other project is untouched


def test_mount_table_ignores_short_lines_and_shorter_later_mounts(tmp_path: Path) -> None:
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "garbage\n//nas/share /data cifs rw 0 0\n/dev/sda1 / ext4 rw 0 0\n", encoding="utf-8"
    )
    assert is_network_fs(Path("/data/x"), mounts)  # the later, shorter ``/`` does not shadow it


def test_update_project_fields_conversions(db: Database) -> None:
    pid = db.upsert_project("A/2024-01_CLIENTE-CAMPANA", "2024-01_CLIENTE-CAMPANA", True, T0)
    db.update_project_fields(pid)  # no fields: nothing to do
    db.update_project_fields(pid, preexisting=False, error=Path("sub") / "dir", total_bytes=5)
    p = db.get_project(pid)
    assert p is not None
    assert p.preexisting is False and p.error == "sub/dir" and p.total_bytes == 5


def test_closed_database_refuses_queries_and_close_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db", mounts_file=tmp_path / "no-mounts").open()
    database.close()
    database.close()  # second close is a no-op
    with pytest.raises(RuntimeError, match="not open"):
        _ = database.conn


def test_verify_results_without_any_job_are_empty(db: Database) -> None:
    pid = db.upsert_project("A/2024-01_CLIENTE-CAMPANA", "2024-01_CLIENTE-CAMPANA", True, T0)
    assert db.get_verify_results(pid) == []


def test_open_twice_is_a_noop(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db", mounts_file=tmp_path / "no-mounts")
    try:
        first = database.open()
        conn = first.conn
        assert database.open() is first and database.conn is conn
    finally:
        database.close()


def test_queued_and_running_job_of_a_project(db: Database) -> None:
    pid = db.upsert_project("A/2024-01_CLIENTE-CAMPANA", "2024-01_CLIENTE-CAMPANA", True, T0)
    assert db.queued_job(pid) is None and db.running_job(pid) is None
    low = db.enqueue_job(JobKind.VERIFY, pid, Trigger.AUTO, 50, T0)
    high = db.enqueue_job(JobKind.SEAL, pid, Trigger.MANUAL, 130, T0)
    queued = db.queued_job(pid)
    assert queued is not None and queued.id == high  # highest priority first
    assert db.running_job(pid) is None
    db.set_job_state(high, JobState.RUNNING, T0)
    running = db.running_job(pid)
    assert running is not None and running.id == high
    queued = db.queued_job(pid)
    assert queued is not None and queued.id == low


def test_verify_results_default_to_the_latest_job(db: Database) -> None:
    pid = db.upsert_project("A/2024-01_CLIENTE-CAMPANA", "2024-01_CLIENTE-CAMPANA", True, T0)
    first = db.enqueue_job(JobKind.VERIFY, pid, Trigger.AUTO, 50, T0)
    db.set_job_state(first, JobState.DONE, T0)  # duplicate rule: one open verify per project
    second = db.enqueue_job(JobKind.VERIFY, pid, Trigger.AUTO, 50, T0 + timedelta(1))
    db.replace_verify_results(first, pid, [VerifyResult("a.mov", "x", "x", "ok")])
    db.replace_verify_results(second, pid, [VerifyResult("b.mov", "y", "z", "corrupt")])
    assert db.get_verify_results(pid) == [VerifyResult("b.mov", "y", "z", "corrupt")]
    assert db.get_verify_results(pid, first) == [VerifyResult("a.mov", "x", "x", "ok")]
