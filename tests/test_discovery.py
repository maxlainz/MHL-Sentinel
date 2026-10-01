"""Discovery over the synthetic archive (D18, D19)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from mhl_sentinel.discovery import discover_projects, find_stray_entries

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_fixtures.py"

REAL = [
    "2024/2024-03_CLIENTE-A_CAMPANA-UNO",
    "2024/2024-11_CLIENTE-B_CAMPANA-DOS",
    "2025/2025-01_CLIENTE-C_LARGO",
    "2025/2025-06_CLIENTE-D_CAMPANA-TRES",
]


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    spec = importlib.util.spec_from_file_location("make_fixtures_disc", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    out = tmp_path_factory.mktemp("arch") / "archive"
    module.build(out, 1, "small")
    return out


def test_depth_1_finds_the_four_dated_projects(archive: Path) -> None:
    rels = [c.rel_path for c in discover_projects(archive, 1)]
    assert [r for r in rels if r.split("/")[-1][:4].isdigit()] == REAL
    assert rels == sorted(rels)
    assert not any(r.startswith(("_RESOURCES", "@Recycle", "#snapshot")) for r in rels)
    # Intermediate levels are transparent (D18): the root-level project is a "container" here,
    # so its subfolders show up as projects. Documented limitation of a fixed depth.
    assert "SIN-CATEGORIA_CLIENTE-E/01_MASTERS" in rels
    assert "SIN-CATEGORIA_CLIENTE-E" not in rels


def test_depth_0_finds_root_level_project_and_year_folders(archive: Path) -> None:
    cands = discover_projects(archive, 0)
    assert [c.name for c in cands] == ["2024", "2025", "SIN-CATEGORIA_CLIENTE-E"]
    assert all("/" not in c.rel_path for c in cands)


def test_has_history_and_fields(archive: Path) -> None:
    proj = archive / REAL[0]
    (proj / "ascmhl").mkdir()
    by_rel = {c.rel_path: c for c in discover_projects(archive, 1)}
    assert by_rel[REAL[0]].has_history is True
    assert by_rel[REAL[0]].name == "2024-03_CLIENTE-A_CAMPANA-UNO"
    assert by_rel[REAL[1]].has_history is False
    (proj / "ascmhl").rmdir()


def test_custom_ignore_prefixes(archive: Path) -> None:
    rels = [c.rel_path for c in discover_projects(archive, 0, ignore_prefixes="_@#.2")]
    assert rels == ["SIN-CATEGORIA_CLIENTE-E"]


def test_negative_depth_rejected(archive: Path) -> None:
    with pytest.raises(ValueError):
        discover_projects(archive, -1)


def test_stray_entries(archive: Path) -> None:
    stray = archive / "2024" / "notes.txt"
    stray.write_text("x")
    try:
        assert find_stray_entries(archive, 1) == [
            "2024/notes.txt",
            "SIN-CATEGORIA_CLIENTE-E/00_README.md",
        ]
        assert find_stray_entries(archive, 0) == []  # .DS_Store ignored by prefix
    finally:
        stray.unlink()
