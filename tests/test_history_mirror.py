"""Unit tests of the history mirror (D59-D62): edge cases the project-level tests do not hit."""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import pytest

from mhl_sentinel import history_mirror as hm
from mhl_sentinel.config import Settings
from mhl_sentinel.db import ProjectRow
from mhl_sentinel.models import ProjectState

REL = "2025/2025-01_CLIENTE-CAMPANA"
NAME = "2025-01_CLIENTE-CAMPANA"


def settings_for(tmp_path: Path) -> Settings:
    return Settings(archive_root=tmp_path / "archive", config_dir=tmp_path / "config")


def project_row(rel: str = REL) -> ProjectRow:
    return ProjectRow(
        id=1, rel_path=rel, name=rel.rsplit("/", 1)[-1], state=ProjectState.SEALED,
        preexisting=False, first_seen="2026-01-01T00:00:00.000000Z", last_scan_at=None,
        last_change_at=None, stable_since=None, last_generation_no=1, last_sealed_at=None,
        last_verified_at=None, file_count=None, total_bytes=None, error=None,
        review_reason=None,
    )  # fmt: skip


def write_history(root: Path, files: dict[str, bytes]) -> Path:
    folder = root / hm.HISTORY_DIR
    for name, data in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    return folder


def test_project_base_rejects_paths_outside_the_archive(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    for bad in ("", "/abs/path", "a/../b", ".."):
        with pytest.raises(ValueError, match="not a project path"):
            hm.mirror_dir(settings, bad)
    assert hm.mirror_dir(settings, REL) == settings.config_dir / "history" / REL / "ascmhl"


def test_chain_bytes_and_needs_sync(tmp_path: Path) -> None:
    project = tmp_path / "project"
    mirror = tmp_path / "mirror"
    assert hm.chain_bytes(mirror) is None
    assert hm.needs_sync(project, mirror) is False  # no history on the project: nothing to mirror
    write_history(project, {hm.CHAIN_FILE: b"one"})
    assert hm.needs_sync(project, mirror) is True  # no mirror yet
    mirror.mkdir()
    (mirror / hm.CHAIN_FILE).write_bytes(b"one")
    assert hm.chain_bytes(mirror) == b"one"
    assert hm.needs_sync(project, mirror) is False
    (project / hm.HISTORY_DIR / hm.CHAIN_FILE).write_bytes(b"two")
    assert hm.needs_sync(project, mirror) is True


def test_sync_without_history_does_nothing(tmp_path: Path) -> None:
    mirror = tmp_path / "mirror"
    assert hm.sync_mirror(tmp_path / "project", mirror) is False
    assert not mirror.exists()


def test_sync_copies_new_files_skips_hidden_and_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    mirror = tmp_path / "mirror"
    write_history(
        project,
        {
            hm.CHAIN_FILE: b"chain1",
            "0001_a.mhl": b"gen1",
            "sub/0002_b.mhl": b"gen2",
            "._0001_a.mhl": b"appledouble",
            ".0003.mhl.tmp": b"half",
        },
    )
    (mirror).mkdir()
    (mirror / ".old.mirror.tmp").write_bytes(b"leftover")
    assert hm.sync_mirror(project, mirror) is True
    assert (mirror / "0001_a.mhl").read_bytes() == b"gen1"
    assert (mirror / "sub" / "0002_b.mhl").read_bytes() == b"gen2"
    assert (mirror / hm.CHAIN_FILE).read_bytes() == b"chain1"
    assert not (mirror / "._0001_a.mhl").exists()
    assert not (mirror / ".0003.mhl.tmp").exists()
    assert not (mirror / ".old.mirror.tmp").exists()  # stale temp of a killed copy
    assert hm.sync_mirror(project, mirror) is False  # nothing new

    (project / hm.HISTORY_DIR / hm.CHAIN_FILE).write_bytes(b"chain2")
    write_history(project, {"0004_c.mhl": b"gen4"})
    assert hm.sync_mirror(project, mirror) is True
    assert (mirror / hm.CHAIN_FILE).read_bytes() == b"chain2"
    assert (mirror / "0004_c.mhl").read_bytes() == b"gen4"


def test_sync_without_chain_copies_manifests_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    mirror = tmp_path / "mirror"
    write_history(project, {"0001_a.mhl": b"gen1"})
    assert hm.sync_mirror(project, mirror) is True
    assert not (mirror / hm.CHAIN_FILE).exists()


def test_sync_sets_aside_a_mirror_of_another_history(tmp_path: Path) -> None:
    project = tmp_path / "project"
    mirror = tmp_path / "base" / "ascmhl"
    write_history(project, {hm.CHAIN_FILE: b"new", "0001_new.mhl": b"n"})
    mirror.mkdir(parents=True)
    (mirror / "0001_old.mhl").write_bytes(b"o")
    (mirror / hm.CHAIN_FILE).write_bytes(b"old")
    assert hm.sync_mirror(project, mirror) is True
    assert sorted(p.name for p in mirror.iterdir()) == ["0001_new.mhl", hm.CHAIN_FILE]
    aside = list((mirror.parent / hm.SUPERSEDED_DIR).iterdir())
    assert len(aside) == 1
    assert (aside[0] / "0001_old.mhl").read_bytes() == b"o"


class FrozenStamp:
    """Stand-in for ``datetime`` in history_mirror: ``now()`` is always the same second."""

    @staticmethod
    def now(_tz: object) -> FrozenStamp:
        return FrozenStamp()

    def strftime(self, _fmt: str) -> str:
        return "20260101T000000Z"


def test_set_aside_twice_in_one_second_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hm, "datetime", FrozenStamp)
    project = tmp_path / "project"
    mirror = tmp_path / "base" / "ascmhl"
    write_history(project, {hm.CHAIN_FILE: b"new", "new.mhl": b"n"})
    for marker in ("first", "second"):
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / f"{marker}.mhl").write_bytes(marker.encode())
        hm.sync_mirror(project, mirror)
        for stray in mirror.iterdir():
            stray.unlink()  # next round starts from a mirror that is empty again
    names = sorted(p.name for p in (mirror.parent / hm.SUPERSEDED_DIR).iterdir())
    assert names == ["20260101T000000Z", "20260101T000000Z-1"]


def test_supersede_mirror_moves_history_and_avoids_stamp_collision(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    assert hm.supersede_mirror(settings, REL, "20260101T000000Z") is None
    mirror = hm.mirror_dir(settings, REL)
    for round_no, expected in enumerate(("20260101T000000Z", "20260101T000000Z-1")):
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / "m.mhl").write_bytes(str(round_no).encode())
        target = hm.supersede_mirror(settings, REL, "20260101T000000Z")
        assert target == mirror.parent / hm.SUPERSEDED_DIR / expected
        assert (target / "m.mhl").read_bytes() == str(round_no).encode()
        assert not mirror.exists()


def test_remove_mirror(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    assert hm.remove_mirror(settings, REL) is False
    base = hm.mirror_dir(settings, REL).parent
    (base / hm.HISTORY_DIR).mkdir(parents=True)
    (base / hm.SUPERSEDED_DIR / "s").mkdir(parents=True)
    assert hm.remove_mirror(settings, REL) is True
    assert not (settings.config_dir / hm.MIRROR_ROOT / "2025").exists()  # empty parents pruned


def test_prune_stops_at_a_non_empty_parent(tmp_path: Path) -> None:
    stop = tmp_path / "history"
    deep = stop / "a" / "b"
    deep.mkdir(parents=True)
    (stop / "a" / "keep.txt").write_text("x")
    hm._prune_empty_parents(deep, stop)
    assert not deep.exists() and (stop / "a").exists()


def test_move_mirror_follows_and_replaces_a_stale_destination(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    old_rel, new_rel = "2025/2025-01_CLIENTE-ANTES", "2025/2025-01_CLIENTE-DESPUES"
    assert hm.move_mirror(settings, old_rel, new_rel) is False
    old_base = hm.mirror_dir(settings, old_rel).parent
    new_base = hm.mirror_dir(settings, new_rel).parent
    (old_base / hm.HISTORY_DIR).mkdir(parents=True)
    (old_base / hm.HISTORY_DIR / "m.mhl").write_bytes(b"fresh")
    (new_base / hm.HISTORY_DIR).mkdir(parents=True)
    (new_base / hm.HISTORY_DIR / "stale.mhl").write_bytes(b"stale")
    assert hm.move_mirror(settings, old_rel, new_rel) is True
    assert (new_base / hm.HISTORY_DIR / "m.mhl").read_bytes() == b"fresh"
    assert not (new_base / hm.HISTORY_DIR / "stale.mhl").exists()
    assert not old_base.exists()


def zip_names(data: bytes | None) -> list[str]:
    assert data is not None
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return sorted(zf.namelist())


def test_zip_history_from_mirror_with_superseded_skipping_hidden(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    base = hm.mirror_dir(settings, REL).parent
    (base / hm.HISTORY_DIR / "sub").mkdir(parents=True)
    (base / hm.HISTORY_DIR / "0001.mhl").write_bytes(b"1")
    (base / hm.HISTORY_DIR / "sub" / "0002.mhl").write_bytes(b"2")
    (base / hm.HISTORY_DIR / "._0001.mhl").write_bytes(b"x")
    (base / hm.SUPERSEDED_DIR / "s1").mkdir(parents=True)
    (base / hm.SUPERSEDED_DIR / "s1" / "old.mhl").write_bytes(b"o")
    assert zip_names(hm.zip_history(settings, project_row())) == [
        f"{NAME}/ascmhl/0001.mhl",
        f"{NAME}/ascmhl/sub/0002.mhl",
        f"{NAME}/ascmhl_superseded/s1/old.mhl",
    ]


def test_zip_history_falls_back_to_the_archive(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    assert hm.zip_history(settings, project_row()) is None
    write_history(settings.archive_root / REL, {"0001.mhl": b"1"})
    assert zip_names(hm.zip_history(settings, project_row())) == [f"{NAME}/ascmhl/0001.mhl"]


def test_zip_history_without_visible_files_is_none(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    write_history(settings.archive_root / REL, {".hidden": b"1"})
    assert hm.zip_history(settings, project_row()) is None


def test_zip_history_unreachable_archive_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    real_is_dir = Path.is_dir

    def flaky(self: Path) -> bool:
        if self.parts[-2:] == (NAME, "ascmhl") and settings.archive_root in self.parents:
            raise OSError("stale NFS handle")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", flaky)
    assert hm.zip_history(settings, project_row()) is None


def test_copy_atomic_leaves_no_temp_when_the_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src.mhl"
    src.write_bytes(b"data")
    dst = tmp_path / "dst.mhl"

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copystat", boom)
    with pytest.raises(OSError, match="disk full"):
        hm._copy_atomic(src, dst)
    assert not dst.exists()
    assert list(tmp_path.glob(f".*{hm.TEMP_SUFFIX}")) == []
