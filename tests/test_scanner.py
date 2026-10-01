"""Scanner, diff and settle rule (D14, D31)."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from mhl_sentinel.models import FileStat
from mhl_sentinel.scanner import diff_against_sealed, is_settled, scan_project

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_fixtures.py"
PROJ = "2024/2024-03_CLIENTE-A_CAMPANA-UNO"
IGNORES = ["._*", ".DS_Store"]
HOUR_NS = 3600 * 1_000_000_000


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    spec = importlib.util.spec_from_file_location("make_fixtures_scan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    out = tmp_path_factory.mktemp("arch") / "archive"
    module.build(out, 1, "small")
    return out


def fs(path: str, size: int = 10, mtime: int = 0) -> FileStat:
    return FileStat(path, size, mtime)


def test_scan_excludes_junk_and_md(archive: Path) -> None:
    out = scan_project(archive / PROJ, IGNORES, ["*.md"])
    paths = [f.rel_path for f in out.files]
    assert out.errors == []
    assert paths == sorted(paths)
    assert not any(p.endswith((".DS_Store", "._junk.mov", ".md")) for p in paths)
    assert len(paths) == 7  # 3 masters + 2 grades + 2 deliverables
    assert "01_MASTERS/CLIENTE-A_CAMPANA-UNO_v1.mov" in paths
    assert all(f.size > 0 and f.mtime_ns > 0 for f in out.files)


def test_scan_without_filters_includes_everything(archive: Path) -> None:
    paths = {f.rel_path for f in scan_project(archive / PROJ, [], []).files}
    assert {".DS_Store", "._junk.mov", "00_README.md"} <= paths


def test_scan_skips_ascmhl_symlinks_and_dir_patterns(tmp_path: Path) -> None:
    (tmp_path / "ascmhl").mkdir()
    (tmp_path / "ascmhl" / "0001.mhl").write_text("x")
    (tmp_path / "#recycle").mkdir()
    (tmp_path / "#recycle" / "gone.mov").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "ascmhl").mkdir()  # nested ascmhl is a normal folder
    (tmp_path / "sub" / "ascmhl" / "a.txt").write_text("x")
    (tmp_path / "keep.mov").write_text("x")
    (tmp_path / "link.mov").symlink_to(tmp_path / "keep.mov")
    out = scan_project(tmp_path, [r"\#recycle/"], [])
    assert [f.rel_path for f in out.files] == ["keep.mov", "sub/ascmhl/a.txt"]


def test_scan_collects_permission_errors(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("x")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "f.txt").write_text("x")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with privileges that ignore permissions")
        out = scan_project(tmp_path, [], [])
    finally:
        locked.chmod(0o755)
    assert [f.rel_path for f in out.files] == ["ok.txt"]
    assert len(out.errors) == 1 and out.errors[0].startswith("locked")


def test_diff_unsealed_everything_added() -> None:
    files = [fs("b", mtime=5), fs("a", mtime=9)]
    d = diff_against_sealed(files, {}, None)
    assert [f.rel_path for f in d.added] == ["a", "b"]
    assert not d.modified and not d.deleted and not d.stable
    assert d.newest_mtime_ns == 9


def test_diff_added_modified_deleted() -> None:
    sealed = {p: fs(p, 10, 1000) for p in ("same", "resized", "touched", "gone", "jitter")}
    files = [
        fs("same", 10, 1000),
        fs("resized", 11, 1000),
        fs("touched", 10, 1000 + 2_000_000_000),
        fs("jitter", 10, 1000 + 1_000_000_000),  # == tolerance: not modified
        fs("new", 1, 5),
    ]
    d = diff_against_sealed(files, sealed, None)
    assert [f.rel_path for f in d.added] == ["new"]
    assert [(a.rel_path, b.size) for a, b in d.modified] == [("resized", 11), ("touched", 10)]
    assert d.modified[0][0].size == 10
    assert [f.rel_path for f in d.deleted] == ["gone"]


def test_diff_tolerance_parameter() -> None:
    sealed = {"a": fs("a", 1, 0)}
    files = [fs("a", 1, 10)]
    assert diff_against_sealed(files, sealed, None, mtime_tolerance_ns=5).modified
    assert not diff_against_sealed(files, sealed, None, mtime_tolerance_ns=10).modified


def test_diff_stable() -> None:
    files = [fs("a", 1, 1), fs("b", 2, 2)]
    prev = {f.rel_path: f for f in files}
    assert diff_against_sealed(files, {}, prev).stable is True
    assert diff_against_sealed(files, {}, {}).stable is False
    assert diff_against_sealed(files, {}, {**prev, "a": fs("a", 1, 2)}).stable is False
    assert diff_against_sealed(files, {}, {"a": prev["a"]}).stable is False
    assert diff_against_sealed([], {}, {}).stable is True
    assert diff_against_sealed([], {}, {}).newest_mtime_ns == 0


def test_is_settled() -> None:
    files = [fs("a", 1, 100 * HOUR_NS)]
    d = diff_against_sealed(files, {}, {"a": files[0]})
    assert is_settled(d, 100 * HOUR_NS + 168 * HOUR_NS, 168)
    assert not is_settled(d, 100 * HOUR_NS + 168 * HOUR_NS - 1, 168)
    unstable = diff_against_sealed(files, {}, None)
    assert not is_settled(unstable, 10**30, 168)
