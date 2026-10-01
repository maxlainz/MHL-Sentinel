"""Legacy MHL 1.x reader (D39) against the synthetic fixtures."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import xxhash

from mhl_sentinel.legacy_mhl import (
    expected_hashes_for_project,
    find_legacy_manifests,
    read_legacy_manifest,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_fixtures.py"


def build_fixtures(out: Path) -> None:
    spec = importlib.util.spec_from_file_location("make_fixtures_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_fixtures_legacy"] = module
    spec.loader.exec_module(module)
    module.build(out, 1, "small")


def project(tmp_path: Path) -> Path:
    build_fixtures(tmp_path / "archive")
    return tmp_path / "archive" / "2024" / "2024-11_CLIENTE-B_CAMPANA-DOS"


def test_find_and_read(tmp_path: Path) -> None:
    root = project(tmp_path)
    found = find_legacy_manifests(root)
    assert [p.name for p in found] == [
        "A001R2EC_2024-11-02_101500.mhl",
        "B001_2024-11-03_090000.mhl",
    ]
    m = read_legacy_manifest(found[0], root)
    assert not m.warnings
    assert len(m.entries) == 4
    for rel, entry in m.entries.items():
        assert rel.startswith("02_OCF/A001R2EC/")  # relative to the PROJECT root
        data = (root / rel).read_bytes()
        assert entry.size == len(data)
        assert entry.hashes == {"xxh64": xxhash.xxh64(data).hexdigest()}


def test_expected_hashes_md5_and_xxh64(tmp_path: Path) -> None:
    root = project(tmp_path)
    expected = expected_hashes_for_project(root)
    assert len(expected) == 6
    for rel, hashes in expected.items():
        data = (root / rel).read_bytes()
        if rel.startswith("02_OCF/B001/"):
            assert hashes == {"md5": hashlib.md5(data).hexdigest()}
        else:
            assert "xxh64" in hashes


def test_ascmhl_dir_and_asc_namespace_excluded(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "ascmhl").mkdir(parents=True)
    (root / "ascmhl" / "0001_x.mhl").write_text('<hashlist version="1.0"/>')
    (root / "v2.mhl").write_text('<hashlist version="2.0" xmlns="urn:ASC:MHL:v2.0"/>')
    (root / "broken.mhl").write_text("<not xml")
    (root / "ok.mhl").write_text('<hashlist version="1.1"/>')
    assert [p.name for p in find_legacy_manifests(root)] == ["ok.mhl"]


def test_xxhash64_little_endian_reversal(tmp_path: Path) -> None:
    data = b"some media bytes" * 1000
    canonical = xxhash.xxh64(data).hexdigest()
    little = xxhash.xxh64(data).digest()[::-1].hex()  # byte order reversed
    assert bytes.fromhex(canonical)[::-1].hex() == little
    root = tmp_path / "p"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.mov").write_bytes(data)
    xml = (
        '<hashlist version="1.0">'
        "<hash><file>a.mov</file><size>16000</size>"
        f"<xxhash64>{little.upper()}</xxhash64><xxhash>123456</xxhash></hash>"
        "<hash><file>..\\x\\b.mov</file><md5>AB</md5></hash>"
        "</hashlist>"
    )
    (root / "sub" / "A.mhl").write_text(xml)
    m = read_legacy_manifest(root / "sub" / "A.mhl", root)
    assert m.entries["sub/a.mov"].hashes == {"xxh64": canonical}
    assert any("XXH32" in w for w in m.warnings)
    assert "x/b.mov" in m.entries  # separators normalised, ".." resolved


def test_conflicts_keep_first_with_warning(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    for name, digest in (("a.mhl", "11" * 16), ("b.mhl", "22" * 16)):
        (root / name).write_text(
            f'<hashlist version="1.0"><hash><file>f</file><md5>{digest}</md5></hash></hashlist>'
        )
    from mhl_sentinel.legacy_mhl import expected_hashes_with_warnings

    merged, warnings = expected_hashes_with_warnings(root)
    assert merged == {"f": {"md5": "11" * 16}}
    assert any("conflicting" in w for w in warnings)
