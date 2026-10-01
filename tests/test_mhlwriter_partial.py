"""D48: a partial generation (only the added files) is valid for the reference."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import xxhash
from lxml import etree

from helpers_ascmhl import XSD_DIR, assert_xsd_valid, run_cli
from mhl_sentinel.mhlwriter import MHLWriteError, write_project_generation

NS = {"m": "urn:ASC:MHL:v2.0"}


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def xxh(path: Path) -> dict[str, str]:
    return {"xxh128": xxhash.xxh128(path.read_bytes()).hexdigest()}


def test_partial_generation_validates(tmp_path: Path) -> None:
    project = tmp_path / "2025-01_CLIENTE-CAMPANA"
    (project / "01_MASTERS").mkdir(parents=True)
    (project / "01_MASTERS" / "a.mov").write_bytes(b"aaa" * 100)
    (project / "readme.txt").write_bytes(b"hi")
    full = {p: xxh(project / p) for p in ("01_MASTERS/a.mov", "readme.txt")}
    write_project_generation(project, full)

    (project / "01_MASTERS" / "b.mov").write_bytes(b"bbb" * 50)
    gen2 = write_project_generation(
        project, {"01_MASTERS/b.mov": xxh(project / "01_MASTERS" / "b.mov")}, partial=True
    )
    assert gen2.name.startswith("0002_")

    tree = etree.parse(str(gen2))
    assert tree.find("m:hashes/m:directoryhash", namespaces=NS) is None
    assert tree.find("m:processinfo/m:roothash", namespaces=NS) is None
    paths = [p.text for p in tree.findall("m:hashes/m:hash/m:path", namespaces=NS)]
    assert paths == ["01_MASTERS/b.mov"]

    assert_xsd_valid(gen2)
    assert (XSD_DIR / "ASCMHL.xsd").exists()
    verify = run_cli("ascmhl-debug", "verify", project)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    info = run_cli("ascmhl", "info", "-v", project)
    assert info.returncode == 0, info.stdout + info.stderr
    assert "Generation 1" in info.stdout or "generation 1" in info.stdout.lower(), info.stdout
    assert info.stdout.lower().count("generation") >= 2, info.stdout
    assert not [f for f in os.listdir(project / "ascmhl") if f.startswith(".")]


def test_partial_rejects_missing_or_ignored(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    (project / "a.mov").write_bytes(b"a")
    (project / ".DS_Store").write_bytes(b"x")
    write_project_generation(project, {"a.mov": xxh(project / "a.mov")})
    with pytest.raises(MHLWriteError):
        write_project_generation(project, {"nope.mov": {"xxh128": "0" * 32}}, partial=True)
    with pytest.raises(MHLWriteError):
        write_project_generation(project, {".DS_Store": xxh(project / ".DS_Store")}, partial=True)
    with pytest.raises(MHLWriteError):
        write_project_generation(project, {}, partial=True)
