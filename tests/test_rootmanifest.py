"""References-only root history (hito 4, D29, D43, issue #3).

The reference is the oracle (norm ``conformidad-mhl.md``): ``ascmhl info -v`` and
``ascmhl-debug verify`` over the archive root, ``xsd-schema-check`` of the root manifest. Projects
sit one level below the root (year folders, D18): the reference must still map them as direct
child histories of the root history.
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from helpers_ascmhl import assert_chain_xsd_valid, assert_xsd_valid, run_cli
from mhl_sentinel import rootmanifest, sealer
from mhl_sentinel.clock import utcnow
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database
from mhl_sentinel.discovery import discover_projects, find_stray_entries
from mhl_sentinel.models import JobKind, JobState, ProjectState, Trigger

NS = {"m": "urn:ASC:MHL:v2.0"}
PROJECTS = ("2024/2024-03_CLIENTE-A", "2024/2024-11_CLIENTE-B", "2025/2025-01_CLIENTE-C")


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
def archive(tmp_path: Path) -> Iterator[tuple[Settings, Database]]:
    root = tmp_path / "archive"
    for rel in PROJECTS:
        (root / rel / "01_MASTERS").mkdir(parents=True)
        (root / rel / "01_MASTERS" / "master.mov").write_bytes(rel.encode() * 400)
        (root / rel / "00_README.md").write_text("readme\n")
    # Ignored by prefix (D19) at the root and inside a year folder: not projects, not strays.
    (root / "_RESOURCES").mkdir()
    (root / "_RESOURCES" / "music.wav").write_bytes(b"wav" * 10)
    (root / "@Recycle").mkdir()
    (root / "@Recycle" / "old.mov").write_bytes(b"old" * 10)
    (root / "2024" / ".DS_Store").write_bytes(b"\0")
    settings = Settings(archive_root=root, config_dir=tmp_path / "config", exclude_globs=["*.md"])
    with Database(settings.db_path) as db:
        sealer.run_scan_cycle(db, settings, now=utcnow())
        for project in db.list_projects():
            sealer.request_seal(db, project.id, utcnow())
        run_all_jobs(db, settings)
        assert {p.state for p in db.list_projects()} == {ProjectState.SEALED}
        yield settings, db


def references(manifest: Path) -> list[str]:
    tree = etree.parse(str(manifest))
    assert tree.find("m:hashes", NS) is None  # references only (D29)
    assert tree.find("m:processinfo/m:roothash", NS) is None  # D43
    return [str(p.text) for p in tree.findall("m:references/m:hashlistreference/m:path", NS)]


def reference_ok(root: Path) -> None:
    info = run_cli("ascmhl", "info", "-v", root)
    assert info.returncode == 0, info.stdout + info.stderr
    verify = run_cli("ascmhl-debug", "verify", root)
    assert verify.returncode == 0, verify.stdout + verify.stderr


def root_manifests(root: Path) -> list[Path]:
    return sorted((root / "ascmhl").glob("*.mhl"))


def test_root_over_year_folders_is_valid_for_the_reference(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    root = settings.archive_root
    assert db.get_kv(rootmanifest.ROOT_MANIFEST_STALE_KEY) == "1"
    assert rootmanifest.root_manifest_needed(db, settings)

    events: list[tuple[str, dict[str, Any]]] = []
    manifest = rootmanifest.refresh_root_manifest(
        db, settings, now=utcnow(), publish=lambda k, p: events.append((k, p))
    )
    assert manifest is not None and manifest.name.startswith("0001_")
    refs = references(manifest)
    assert [r.split("/ascmhl/")[0] for r in refs] == list(PROJECTS)
    assert_xsd_valid(manifest)
    assert_chain_xsd_valid(root / "ascmhl" / "ascmhl_chain.xml")
    reference_ok(root)
    info = run_cli("ascmhl", "info", "-v", root)
    assert info.stdout.count("Child History at") == len(PROJECTS), info.stdout

    patterns = [str(p.text) for p in etree.parse(str(manifest)).findall(".//m:pattern", NS)]
    assert {"*.md", "_*", "@*", r"\#*", ".*"} <= set(patterns)

    assert db.get_kv(rootmanifest.ROOT_MANIFEST_STALE_KEY) == "0"
    assert db.get_kv(rootmanifest.LAST_ROOT_MANIFEST_KEY) is not None
    assert [k for k, _ in events] == ["root.updated"]
    assert events[0][1]["projects"] == len(PROJECTS)

    # Nothing changed: nothing written.
    assert not rootmanifest.root_manifest_needed(db, settings)
    assert rootmanifest.refresh_root_manifest(db, settings, now=utcnow()) is None

    # The root history is neither a project nor a stray entry, at any depth setting.
    assert "ascmhl" not in {c.name for c in discover_projects(root, 0)}
    assert not [s for s in find_stray_entries(root, 1) if s.startswith("ascmhl")]


def test_stale_flag_lifecycle_through_jobs(archive: tuple[Settings, Database]) -> None:
    settings, db = archive
    root = settings.archive_root
    assert sealer.schedule_root_manifest(db, settings, now=utcnow())
    assert not sealer.schedule_root_manifest(db, settings, now=utcnow())  # already queued
    job = db.next_job(utcnow())
    assert job is not None and job.kind is JobKind.ROOT_MANIFEST and job.priority == 5
    assert job.project_id is None
    run_all_jobs(db, settings)
    done = db.get_job(job.id)
    assert done is not None and done.state is JobState.DONE, done
    assert len(root_manifests(root)) == 1
    assert not sealer.schedule_root_manifest(db, settings, now=utcnow())

    # A new generation in a project (append) makes the root stale again.
    time.sleep(1.1)
    project = db.get_project(PROJECTS[0])
    assert project is not None
    (root / PROJECTS[0] / "01_MASTERS" / "extra.mov").write_bytes(b"extra" * 10)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    sealer.enqueue(db, project.id, JobKind.APPEND, Trigger.AUTO, utcnow())
    run_all_jobs(db, settings)
    assert db.get_kv(rootmanifest.ROOT_MANIFEST_STALE_KEY) == "1"
    assert sealer.schedule_root_manifest(db, settings, now=utcnow())
    run_all_jobs(db, settings)
    _gen1, gen2 = root_manifests(root)
    assert any("/ascmhl/0002_" in r for r in references(gen2))
    assert_xsd_valid(gen2)
    reference_ok(root)


def test_accept_and_vanished_project_regenerate_the_root(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    root = settings.archive_root
    assert rootmanifest.refresh_root_manifest(db, settings, now=utcnow()) is not None

    # Accept as new version: the old reference points at a manifest that left ascmhl/.
    time.sleep(1.1)
    master = root / PROJECTS[1] / "01_MASTERS" / "master.mov"
    master.write_bytes(b"new grade" * 100)
    sealer.run_scan_cycle(db, settings, now=utcnow())
    project = db.get_project(PROJECTS[1])
    assert project is not None and project.state is ProjectState.NEEDS_REVIEW
    sealer.request_accept_new_version(db, project.id, utcnow())
    run_all_jobs(db, settings)
    assert rootmanifest.root_manifest_needed(db, settings)
    # Until the root is regenerated the reference cannot load it (assert in ascmhl 1.2).
    stale_info = run_cli("ascmhl", "info", "-v", root)
    assert stale_info.returncode != 0
    assert "assert referenced_hash_list is not None" in stale_info.stdout + stale_info.stderr
    assert rootmanifest.dangling_references(root) != []
    events: list[tuple[str, dict[str, Any]]] = []
    manifest = rootmanifest.refresh_root_manifest(
        db, settings, now=utcnow(), publish=lambda k, p: events.append((k, p))
    )
    assert manifest is not None and manifest.name.startswith("0001_")  # fresh root history
    assert events[0][1]["rebuilt"] is True
    (retired,) = (root / "ascmhl_superseded").iterdir()
    assert [p.name[:5] for p in retired.glob("*.mhl")] == ["0001_"]
    assert rootmanifest.dangling_references(root) == []
    reference_ok(root)

    # A project folder disappears: the next root only references the others (H12).
    time.sleep(1.1)
    shutil.rmtree(root / PROJECTS[2])
    sealer.run_scan_cycle(db, settings, now=utcnow())
    gone = db.get_project(PROJECTS[2])
    assert gone is not None and gone.state is ProjectState.MISSING  # D58
    assert rootmanifest.root_manifest_needed(db, settings)  # the referenced set changed
    manifest = rootmanifest.refresh_root_manifest(db, settings, now=utcnow())
    assert manifest is not None and manifest.name.startswith("0001_")  # rebuilt again
    assert len(list((root / "ascmhl_superseded").iterdir())) == 2
    assert [r.split("/ascmhl/")[0] for r in references(manifest)] == list(PROJECTS[:2])
    assert_xsd_valid(manifest)
    reference_ok(root)


def test_stray_files_are_reported_by_the_reference(archive: tuple[Settings, Database]) -> None:
    """A loose file between projects (D49) is in no manifest: the reference calls it new."""
    settings, db = archive
    root = settings.archive_root
    (root / "2024" / "notes.txt").write_text("loose\n")
    assert rootmanifest.refresh_root_manifest(db, settings, now=utcnow()) is not None
    verify = run_cli("ascmhl-debug", "verify", root)
    assert verify.returncode == 21, verify.stdout + verify.stderr
    assert "found new file 2024/notes.txt" in verify.stdout + verify.stderr


def test_no_root_without_a_sealed_project(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    (root / "2025" / "2025-01_CLIENTE").mkdir(parents=True)
    settings = Settings(archive_root=root, config_dir=tmp_path / "config")
    with Database(settings.db_path) as db:
        sealer.run_scan_cycle(db, settings, now=utcnow())
        assert not rootmanifest.root_manifest_needed(db, settings)
        assert rootmanifest.refresh_root_manifest(db, settings, now=utcnow(), force=True) is None
        assert not (root / "ascmhl").exists()


def test_prefix_in_the_archive_path_is_not_used(tmp_path: Path) -> None:
    settings = Settings(archive_root=tmp_path / "_share" / "archive", config_dir=tmp_path / "c")
    assert "_*" not in rootmanifest.prefix_patterns(settings)
    assert "@*" in rootmanifest.prefix_patterns(settings)


def test_root_ignore_patterns_have_no_duplicates(tmp_path: Path) -> None:
    settings = Settings(
        archive_root=tmp_path / "archive",
        config_dir=tmp_path / "c",
        exclude_globs=[".DS_Store", "*.md", "*.md"],
    )
    patterns = rootmanifest.root_ignore_patterns(settings)
    assert len(patterns) == len(set(patterns))
    assert patterns.count(".DS_Store") == 1 and patterns.count("*.md") == 1


def test_missing_root_history_makes_a_refresh_needed(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    assert rootmanifest.refresh_root_manifest(db, settings, now=utcnow()) is not None
    assert not rootmanifest.root_manifest_needed(db, settings)
    shutil.rmtree(settings.archive_root / "ascmhl")
    assert rootmanifest.root_manifest_needed(db, settings)


def test_dangling_references_skips_a_chain_entry_whose_file_is_gone(
    archive: tuple[Settings, Database],
) -> None:
    settings, db = archive
    root = settings.archive_root
    assert rootmanifest.dangling_references(root) == []  # no root history yet
    assert rootmanifest.refresh_root_manifest(db, settings, now=utcnow()) is not None
    assert rootmanifest.dangling_references(root) == []  # intact
    for manifest in root_manifests(root):
        manifest.unlink()
    assert rootmanifest.dangling_references(root) == []  # the reference reports that itself
