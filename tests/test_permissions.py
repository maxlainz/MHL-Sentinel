"""D69: with UMASK 000 everything the app creates under /archive is 777 (folders) / 666 (files).

macOS over SMB needs write permission on a folder to delete what is inside; the team's access
is decided by the QTS share permissions, not by POSIX bits. The writer never fixes a mode of its
own (no ``mode=``, ``chmod``, ``tempfile`` or ``copystat`` on the archive), so the umask decides.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import xxhash

from mhl_sentinel.mhlwriter import (
    SUPERSEDED_DIR,
    retire_history,
    write_project_generation,
    write_root_references_generation,
)
from mhl_sentinel.sealer import quarantine_orphan_manifests

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _umask_000(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    old = os.umask(0)
    yield
    os.umask(old)
    monkeypatch.undo()
    time.tzset()


def _xxh(path: Path) -> dict[str, str]:
    return {"xxh128": xxhash.xxh128(path.read_bytes()).hexdigest()}


def _seal(project: Path) -> Path:
    files = sorted(p.name for p in project.glob("*.mov"))
    return write_project_generation(project, {name: _xxh(project / name) for name in files})


def _assert_open(created: list[Path]) -> None:
    assert created
    for path in created:
        mode = stat.S_IMODE(path.stat().st_mode)
        want = 0o777 if path.is_dir() else 0o666
        assert mode == want, f"{path.name}: {oct(mode)} != {oct(want)}"


def _tree(folder: Path) -> list[Path]:
    return [folder, *folder.rglob("*")]


def test_everything_written_in_the_archive_is_open_to_the_team(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    project = root / "2025" / "2025-01_CLIENTE-CAMPANA"
    project.mkdir(parents=True)
    (project / "a.mov").write_bytes(b"aaa" * 10)

    _seal(project)
    (project / "b.mov").write_bytes(b"bbb")
    _seal(project)
    write_root_references_generation(root, [project])
    _assert_open(_tree(project / "ascmhl") + _tree(root / "ascmhl"))

    orphan = project / "ascmhl" / "0009_extra.mhl"
    orphan.write_bytes(b"<hashlist/>")
    _assert_open(quarantine_orphan_manifests(project))

    target = retire_history(project, dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert target is not None
    _seal(project)  # a new history next to the retired one (Accept as new version)
    _assert_open(_tree(project / SUPERSEDED_DIR) + _tree(project / "ascmhl"))


def test_image_defaults_to_umask_000() -> None:
    dockerfile = (REPO / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (REPO / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    assert re.search(r"\bUMASK=000\b", dockerfile)
    assert 'UMASK="${UMASK:-000}"' in entrypoint
    assert 'umask "$UMASK"' in entrypoint
    assert 'exec gosu "$PUID:$PGID" "$@"' in entrypoint
    # never chown/chmod the mounted data, only /config
    code = [line.split("#", 1)[0].strip() for line in entrypoint.splitlines()]
    assert [line for line in code if "chown" in line] == ['chown "$PUID:$PGID" /config']
    assert not [line for line in code if "chmod" in line]
    compose = (REPO / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'PGID: "${PGID:-100}"' in compose
    assert 'UMASK: "${UMASK:-000}"' in compose
