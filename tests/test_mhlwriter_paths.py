"""Error paths of the ASC MHL writer: nothing is written and the history stays intact."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import xxhash

from helpers_ascmhl import run_cli
from mhl_sentinel.mhlwriter import (
    SUPERSEDED_DIR,
    MHLReviewError,
    MHLWriteError,
    retire_history,
    write_project_generation,
    write_root_references_generation,
)


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def xxh(path: Path) -> dict[str, str]:
    return {"xxh128": xxhash.xxh128(path.read_bytes()).hexdigest()}


def make_project(parent: Path, name: str = "2025-01_CLIENTE-CAMPANA") -> Path:
    project = parent / name
    project.mkdir(parents=True)
    (project / "a.mov").write_bytes(b"aaa" * 10)
    return project


def seal(project: Path) -> Path:
    return write_project_generation(project, {"a.mov": xxh(project / "a.mov")})


def manifests(project: Path) -> list[str]:
    return sorted(p.name for p in (project / "ascmhl").glob("*.mhl"))


def assert_reference_verifies(project: Path) -> None:
    result = run_cli("ascmhl-debug", "verify", project)
    assert result.returncode == 0, result.stdout + result.stderr


def test_nested_history_inside_a_project_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    inner = project / "inner"
    inner.mkdir()
    (inner / "b.mov").write_bytes(b"bbb")
    write_project_generation(inner, {"b.mov": xxh(inner / "b.mov")})
    with pytest.raises(MHLWriteError, match="nested ASC MHL histories"):
        write_project_generation(project, {"a.mov": xxh(project / "a.mov")})
    assert not (project / "ascmhl").exists()


def test_file_without_precomputed_hash(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    with pytest.raises(MHLWriteError, match=r"no precomputed hash for a\.mov"):
        write_project_generation(project, {"other.mov": {"xxh128": "0" * 32}})
    assert not (project / "ascmhl").exists()


def test_file_lacking_a_directory_hash_format(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    with pytest.raises(MHLWriteError, match=r"a.mov lacks \['xxh128'\]"):
        write_project_generation(project, {"a.mov": {"md5": "0" * 32}})
    assert not (project / "ascmhl").exists()


def test_hashes_for_paths_not_on_disk(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    hashes = {"a.mov": xxh(project / "a.mov"), "ghost.mov": {"xxh128": "0" * 32}}
    with pytest.raises(MHLWriteError, match=r"not on disk or ignored: \['ghost.mov'\]"):
        write_project_generation(project, hashes)
    assert not (project / "ascmhl").exists()


def test_modified_file_is_a_review_case_and_writes_nothing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    seal(project)
    before = manifests(project)
    (project / "a.mov").write_bytes(b"changed" * 10)
    with pytest.raises(MHLReviewError, match=r"hash mismatch.*a\.mov \(xxh128\)"):
        write_project_generation(project, {"a.mov": xxh(project / "a.mov")})
    assert manifests(project) == before
    assert not [p for p in (project / "ascmhl").iterdir() if p.name.startswith(".")]


def test_deleted_file_is_a_review_case(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "b.mov").write_bytes(b"b")
    write_project_generation(
        project, {"a.mov": xxh(project / "a.mov"), "b.mov": xxh(project / "b.mov")}
    )
    (project / "b.mov").unlink()
    with pytest.raises(MHLReviewError, match=r"missing: .*b\.mov"):
        write_project_generation(project, {"a.mov": xxh(project / "a.mov")})
    assert manifests(project) == [manifests(project)[0]]


def test_known_file_given_only_unrecorded_formats(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    seal(project)
    with pytest.raises(MHLWriteError, match="none of the recorded formats"):
        write_project_generation(
            project, {"a.mov": {"md5": "0" * 32}}, directory_hash_formats=("md5",)
        )
    assert len(manifests(project)) == 1


def test_new_file_with_only_inherited_formats_has_no_original(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    with pytest.raises(MHLWriteError, match="at least one format must be original"):
        write_project_generation(
            project, {"a.mov": xxh(project / "a.mov")}, inherited_formats={"xxh128"}
        )
    assert not (project / "ascmhl").exists()


def test_inherited_format_next_to_an_original_one_is_valid(tmp_path: Path) -> None:
    import hashlib

    project = make_project(tmp_path)
    data = (project / "a.mov").read_bytes()
    hashes = {"a.mov": {**xxh(project / "a.mov"), "md5": hashlib.md5(data).hexdigest()}}
    write_project_generation(project, hashes, inherited_formats={"md5"})
    assert_reference_verifies(project)


def test_retire_history_without_history_is_none(tmp_path: Path) -> None:
    assert retire_history(tmp_path, dt.datetime(2026, 1, 1, tzinfo=dt.UTC)) is None
    assert not (tmp_path / SUPERSEDED_DIR).exists()


def test_retire_history_same_second_gets_a_suffix(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    now = dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=dt.UTC)
    targets = []
    for _ in range(2):
        seal(project)
        target = retire_history(project, now)
        assert target is not None
        targets.append(target.name)
        assert not (project / "ascmhl").exists()
    assert targets == ["2026-01-01T100000Z", "2026-01-01T100000Z-1"]


def test_root_references_errors(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    project = make_project(root / "2025")
    with pytest.raises(MHLWriteError, match="needs at least one reference"):
        write_root_references_generation(root, [])
    with pytest.raises(MHLWriteError, match="no ASC MHL history at 2025"):
        write_root_references_generation(root, [project])


def test_root_reference_to_a_grandchild_history_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    project = make_project(root / "2025")
    seal(project)
    nested = project / "inner"
    nested.mkdir()
    (nested / "b.mov").write_bytes(b"bbb")
    write_project_generation(nested, {"b.mov": xxh(nested / "b.mov")})
    with pytest.raises(MHLWriteError, match="is not a direct child history"):
        write_root_references_generation(root, [nested])


def test_partial_generation_with_a_changed_file_is_a_review_case(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    seal(project)
    (project / "a.mov").write_bytes(b"changed" * 10)
    with pytest.raises(MHLReviewError, match=r"hash mismatch.*a\.mov"):
        write_project_generation(project, {"a.mov": xxh(project / "a.mov")}, partial=True)
    assert len(manifests(project)) == 1
