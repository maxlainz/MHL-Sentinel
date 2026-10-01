"""End to end: ``mhl-sentinel run-once`` over the synthetic archive (hito 1).

Every manifest written is checked by the reference as a real CLI process (norm
``conformidad-mhl.md``): ``xsd-schema-check`` and ``ascmhl-debug verify``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from click.testing import CliRunner
from lxml import etree

from helpers_ascmhl import assert_xsd_valid, run_cli
from mhl_sentinel.cli import main
from mhl_sentinel.config import load_settings, save_yaml
from mhl_sentinel.db import Database
from mhl_sentinel.models import ChangeKind, ProjectState

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_fixtures.py"
NS = {"m": "urn:ASC:MHL:v2.0"}
A = "2024/2024-03_CLIENTE-A_CAMPANA-UNO"
B = "2024/2024-11_CLIENTE-B_CAMPANA-DOS"  # carries two legacy MHL 1.x (xxhash64be, md5)
REAL = [A, B, "2025/2025-01_CLIENTE-C_LARGO", "2025/2025-06_CLIENTE-D_CAMPANA-TRES"]


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_fixtures_e2e", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def setup_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    archive, config = tmp_path / "archive", tmp_path / "config"
    generator().build(archive, 1, "small")
    monkeypatch.setenv("MHLS_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("MHLS_CONFIG_DIR", str(config))
    return archive, config


def cli(*args: str) -> str:
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


def states(config: Path) -> dict[str, ProjectState]:
    with Database(config / "state.db") as db:
        return {p.rel_path: p.state for p in db.list_projects()}


def assert_verifies(project: Path) -> None:
    result = run_cli("ascmhl-debug", "verify", project)
    assert result.returncode == 0, result.stdout + result.stderr


def manifests(project: Path) -> list[Path]:
    return sorted((project / "ascmhl").glob("*.mhl"))


def touch_later(path: Path, seconds: int = 10) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


def test_run_once_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive, config = setup_archive(tmp_path, monkeypatch)

    # 1. First run on an existing archive: everything preexisting and unsealed, no jobs (D15).
    cli("run-once")
    with Database(config / "state.db") as db:
        projects = db.list_projects()
        assert {p.rel_path for p in projects} >= set(REAL)
        assert all(p.state is ProjectState.UNSEALED and p.preexisting for p in projects)
        assert db.list_jobs() == []

    # 2. Seal everything.
    out = cli("run-once", "--seal-all", "--ignore-working-hours")
    assert set(states(config).values()) == {ProjectState.SEALED}, out
    for rel in states(config):
        project = archive / rel
        (gen1,) = manifests(project)
        assert gen1.name.startswith("0001_")
        assert_xsd_valid(gen1)
        assert_verifies(project)

    # D39: legacy hashes inherited as verified next to our xxh128 original.
    (gen1,) = manifests(archive / B)
    tree = etree.parse(str(gen1))
    actions: dict[str, set[tuple[str, str | None]]] = {}
    for h in tree.findall("m:hashes/m:hash", namespaces=NS):
        path = str(h.findtext("m:path", namespaces=NS))
        actions[path] = {(etree.QName(c).localname, c.get("action")) for c in list(h)[1:]}
    ocf = {p: a for p, a in actions.items() if p.startswith("02_OCF/") and p.endswith(".mov")}
    assert len(ocf) == 6
    for path, acts in ocf.items():
        legacy = "xxh64" if path.startswith("02_OCF/A001R2EC/") else "md5"
        assert acts == {("xxh128", "original"), (legacy, "verified")}, (path, acts)

    # 3. A new file in a sealed project: changed, not settled yet (168 h by default).
    new_file = archive / A / "05_DELIVERABLES" / "deliverable_03.mp4"
    new_file.write_bytes(b"new deliverable" * 1000)
    cli("run-once", "--ignore-working-hours")
    assert states(config)[A] is ProjectState.CHANGED

    # 4. Settle time 0 → automatic append: partial generation 0002 (D48).
    settings = load_settings(config)
    save_yaml(settings.model_copy(update={"settle_hours": 0}), settings.config_file)
    cli("run-once", "--ignore-working-hours")
    assert states(config)[A] is ProjectState.SEALED
    gens = manifests(archive / A)
    assert [g.name[:5] for g in gens] == ["0001_", "0002_"]
    assert_xsd_valid(gens[1])
    paths = [p.text for p in etree.parse(str(gens[1])).findall("m:hashes/m:hash/m:path", NS)]
    assert paths == ["05_DELIVERABLES/deliverable_03.mp4"]
    assert_verifies(archive / A)

    # 5. A modified file → review, nothing written (D9).
    master = archive / A / "01_MASTERS" / "CLIENTE-A_CAMPANA-UNO_v1.mov"
    data = bytearray(master.read_bytes())
    data[0] ^= 0xFF
    master.write_bytes(bytes(data))
    touch_later(master)
    cli("run-once", "--ignore-working-hours")
    with Database(config / "state.db") as db:
        row = db.get_project(A)
        assert row is not None and row.state is ProjectState.NEEDS_REVIEW
        items = db.get_review_items(row.id)
    assert [(i.rel_path, i.change) for i in items] == [
        ("01_MASTERS/CLIENTE-A_CAMPANA-UNO_v1.mov", ChangeKind.MODIFIED)
    ]
    assert len(manifests(archive / A)) == 2

    # 6. Accept as new version (D17): fresh history, the old one set aside.
    cli("projects", "accept", A)
    cli("run-once", "--ignore-working-hours")
    assert states(config)[A] is ProjectState.SEALED
    (fresh,) = manifests(archive / A)
    assert fresh.name.startswith("0001_")
    assert_xsd_valid(fresh)
    (old,) = (archive / A / "ascmhl_superseded").iterdir()
    assert sorted(p.name[:5] for p in old.glob("*.mhl")) == ["0001_", "0002_"]
    assert (old / "ascmhl_chain.xml").is_file()
    assert_verifies(archive / A)
    info = run_cli("ascmhl", "info", "-v", archive / A)
    assert info.returncode == 0 and info.stdout.count("Generation ") == 1, info.stdout


def test_legacy_mismatch_blocks_the_first_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, config = setup_archive(tmp_path, monkeypatch)
    clip = archive / B / "02_OCF" / "B001" / "B001C001_241103_R2EC.mov"
    data = bytearray(clip.read_bytes())
    data[-1] ^= 0x01  # same size, same mtime: silent corruption
    st = clip.stat()
    clip.write_bytes(bytes(data))
    os.utime(clip, ns=(st.st_atime_ns, st.st_mtime_ns))

    cli("run-once", "--seal-all", "--ignore-working-hours")
    with Database(config / "state.db") as db:
        project = db.get_project(B)
        assert project is not None and project.state is ProjectState.NEEDS_REVIEW
        assert project.review_reason and "legacy MHL" in project.review_reason
        items = db.get_review_items(project.id)
    assert [(i.rel_path, i.change) for i in items] == [
        ("02_OCF/B001/B001C001_241103_R2EC.mov", ChangeKind.MODIFIED)
    ]
    assert not (archive / B / "ascmhl").exists()
    assert states(config)[A] is ProjectState.SEALED


def test_other_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_archive(tmp_path, monkeypatch)
    assert "settle_hours: 168" in cli("settings", "show")
    cli("run-once")
    listing = cli("projects", "list")
    assert A in listing and "unsealed" in listing
    assert "tick: working_now=" in cli("serve", "--once-tick")
