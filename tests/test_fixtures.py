"""El generador de fixtures es determinista y los .mhl 1.x heredados son válidos."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

import xxhash

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_fixtures.py"


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_fixtures", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_fixtures"] = module
    spec.loader.exec_module(module)
    return module


def snapshot(root: Path) -> dict[str, tuple[int, str, int]]:
    return {
        p.relative_to(root).as_posix(): (
            p.stat().st_size,
            xxhash.xxh128(p.read_bytes()).hexdigest(),
            int(p.stat().st_mtime),
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_deterministic(tmp_path: Path) -> None:
    gen = load_generator()
    assert gen.main(["--out", str(tmp_path / "a"), "--seed", "7"]) == 0
    assert gen.main(["--out", str(tmp_path / "b"), "--seed", "7"]) == 0
    first = snapshot(tmp_path / "a")
    assert first == snapshot(tmp_path / "b")
    assert len(first) > 50


def test_layout(tmp_path: Path) -> None:
    gen = load_generator()
    gen.main(["--out", str(tmp_path)])
    for rel in (
        "2024/2024-03_CLIENTE-A_CAMPANA-UNO/01_MASTERS",
        "2024/2024-11_CLIENTE-B_CAMPANA-DOS/02_OCF/A001R2EC",
        "2025/2025-01_CLIENTE-C_LARGO/04_VFX/seq_0001",
        "2025/2025-06_CLIENTE-D_CAMPANA-TRES/00_README.md",
        "_RESOURCES",
        "@Recycle",
        "#snapshot",
        "SIN-CATEGORIA_CLIENTE-E/01_MASTERS",
        ".DS_Store",
        "2024/2024-03_CLIENTE-A_CAMPANA-UNO/._junk.mov",
    ):
        assert (tmp_path / rel).exists(), rel


def test_legacy_mhl_hashes(tmp_path: Path) -> None:
    gen = load_generator()
    gen.main(["--out", str(tmp_path)])
    mhls = sorted(tmp_path.rglob("*.mhl"))
    assert len(mhls) == 2
    seen: set[str] = set()
    for mhl in mhls:
        root = ElementTree.parse(mhl).getroot()
        assert root.tag == "hashlist"
        assert root.get("version") == "1.0"
        hashes = root.findall("hash")
        assert hashes
        for h in hashes:
            data = (mhl.parent / (h.findtext("file") or "")).read_bytes()
            assert int(h.findtext("size") or -1) == len(data)
            xx = h.findtext("xxhash64be")
            md5 = h.findtext("md5")
            if xx is not None:
                assert xx == xxhash.xxh64(data).hexdigest()
                seen.add("xxhash64be")
            if md5 is not None:
                assert md5 == hashlib.md5(data).hexdigest()
                seen.add("md5")
    assert seen == {"xxhash64be", "md5"}
