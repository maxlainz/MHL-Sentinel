"""Hito 0 spike (D28, D29, D39): ASC MHL generations from precomputed hashes.

The reference implementation is the oracle (norm ``conformidad-mhl.md``):
every manifest goes through ``ascmhl-debug xsd-schema-check`` and the
histories through ``ascmhl-debug verify`` / ``ascmhl info``, as real CLI
processes. The synthetic archive is built in ``tmp_path``; no network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import xxhash
from ascmhl import chain_xml_parser, hashlist_xml_parser
from ascmhl.hashlist import MHLHashList, MHLProcess
from ascmhl.history import MHLHistory
from lxml import etree

from mhl_sentinel.mhlwriter import (
    TOOL_NAME,
    MHLWriteError,
    _creator_info,
    write_project_generation,
    write_root_references_generation,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
XSD_DIR = REPO_ROOT / "tests" / "xsd"
NS = {"m": "urn:ASC:MHL:v2.0", "d": "urn:ASC:MHL:DIRECTORY:v2.0"}

# The CLI checks GitHub for updates on every run (research spec §2.1) and has
# no switch to disable it. A dead proxy makes that request fail at once; the
# Updater swallows the error. TZ=UTC: dates are written with the current
# offset (research spec §2.7).
CLI_ENV = {
    **os.environ,
    "TZ": "UTC",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "HTTP_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "",
    "no_proxy": "",
}


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def run_cli(tool: str, *args: str | Path) -> subprocess.CompletedProcess[str]:
    if shutil.which("uv"):
        cmd = ["uv", "run", "--project", str(REPO_ROOT), "--no-sync", tool]
    else:
        cmd = [str(Path(sys.executable).parent / tool)]
    return subprocess.run(
        [*cmd, *map(str, args)],
        env=CLI_ENV,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def assert_xsd_valid(manifest: Path) -> None:
    result = run_cli("ascmhl-debug", "xsd-schema-check", manifest, "-xsd", XSD_DIR / "ASCMHL.xsd")
    assert result.returncode == 0, result.stdout + result.stderr


def assert_chain_xsd_valid(chain: Path) -> None:
    # The combined XSD imports ASCMHL.xsd locally first, so the remote
    # schemaLocation inside ASCMHLDirectory.xsd is never fetched.
    xsd = XSD_DIR / "ASCMHLDirectory__combined.xsd"
    result = run_cli("ascmhl-debug", "xsd-schema-check", "-df", chain, "-xsd", xsd)
    assert result.returncode == 0, result.stdout + result.stderr


def xxh(data: bytes) -> dict[str, str]:
    return {"xxh128": xxhash.xxh128(data).hexdigest()}


def write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def hashes_for(project: Path) -> dict[str, dict[str, str]]:
    """Our own hasher stand-in: xxh128 of every non-ignored file."""
    skip_dirs = {"ascmhl", "@eaDir", "#recycle"}
    out: dict[str, dict[str, str]] = {}
    for p in sorted(project.rglob("*")):
        rel = p.relative_to(project)
        if not p.is_file() or skip_dirs & set(rel.parts[:-1]):
            continue
        if p.name.startswith("._") or p.name in {".DS_Store", "Thumbs.db"}:
            continue
        out[rel.as_posix()] = xxh(p.read_bytes())
    return out


def chain_entries(history_root: Path) -> list[tuple[int, str, str]]:
    tree = etree.parse(str(history_root / "ascmhl" / "ascmhl_chain.xml"))
    return [
        (
            int(h.get("sequencenr", "-1")),
            str(h.findtext("d:path", namespaces=NS)),
            str(h.findtext("d:c4", namespaces=NS)),
        )
        for h in tree.findall("d:hashlist", namespaces=NS)
    ]


def actions(manifest: Path) -> dict[tuple[str, str], str]:
    tree = etree.parse(str(manifest))
    out: dict[tuple[str, str], str] = {}
    for h in tree.findall("m:hashes/m:hash", namespaces=NS):
        path = str(h.findtext("m:path", namespaces=NS))
        for child in h:
            tag = etree.QName(child).localname
            if tag != "path":
                out[(path, tag)] = str(child.get("action"))
    return out


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "ARCHIVO"
    a = root / "2025" / "2025-01_CLIENTE-CAMPANA"
    b = root / "2025" / "2025-02_CLIENTE-OTRA"
    write_file(a / "01_MASTERS" / "spot_20s.mov", b"master-20" * 100)
    write_file(a / "01_MASTERS" / "spot_10s.mov", b"master-10" * 50)
    write_file(a / "02_PROYECTO" / "proyecto.drp", b"drp")
    write_file(a / "02_PROYECTO" / "vacia" / ".keep", b"")
    write_file(a / ".DS_Store", b"junk")
    write_file(a / "01_MASTERS" / "._spot_20s.mov", b"appledouble")
    write_file(a / "@eaDir" / "thumb.jpg", b"synology")
    write_file(a / "#recycle" / "old.mov", b"trash")
    write_file(b / "01_MASTERS" / "spot.mov", b"otra")
    return root


def projects(archive: Path) -> tuple[Path, Path]:
    year = archive / "2025"
    return year / "2025-01_CLIENTE-CAMPANA", year / "2025-02_CLIENTE-OTRA"


# --- goal 1: project generations from precomputed hashes ---------------------


def test_two_generations_from_precomputed_hashes(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = projects(archive)

    # The library must never read media files: make every hash_file call fail
    # for anything outside ascmhl/ (manifests are hashed for the chain C4).
    import ascmhl.hasher as hasher_module

    real_hash_file = hasher_module.hash_file

    def guarded(path: str, fmt: str) -> str:
        assert f"{os.sep}ascmhl{os.sep}" in path, f"library re-read a media file: {path}"
        return str(real_hash_file(path, fmt))

    monkeypatch.setattr(hasher_module, "hash_file", guarded)
    monkeypatch.setattr("ascmhl.hashlist.hash_file", guarded)
    monkeypatch.setattr("ascmhl.history.hasher.hash_file", guarded)

    gen1 = write_project_generation(project, hashes_for(project), tool_version="0.0.1")
    assert gen1.name.startswith("0001_2025-01_CLIENTE-CAMPANA_")
    acts1 = actions(gen1)
    assert set(acts1.values()) == {"original"}
    assert ("02_PROYECTO/vacia/.keep", "xxh128") in acts1
    # ignored: .DS_Store, ._*, @eaDir, #recycle
    assert all(not p.startswith(("@eaDir", "#recycle", ".DS")) for p, _ in acts1)
    assert ("01_MASTERS/._spot_20s.mov", "xxh128") not in acts1

    tree = etree.parse(str(gen1))
    assert tree.findtext("m:creatorinfo/m:tool", namespaces=NS) == TOOL_NAME
    assert tree.find("m:creatorinfo/m:tool", namespaces=NS).get("version") == "0.0.1"  # type: ignore[union-attr]
    assert tree.findtext("m:processinfo/m:process", namespaces=NS) == "in-place"
    assert tree.find("m:processinfo/m:roothash/m:content/m:xxh128", namespaces=NS) is not None
    patterns = [p.text for p in tree.findall("m:processinfo/m:ignore/m:pattern", namespaces=NS)]
    assert {"._*", "Thumbs.db", "@eaDir", r"\#recycle", "ascmhl/"} <= set(patterns)
    dirs = {
        d.findtext("m:path", namespaces=NS)
        for d in tree.findall("m:hashes/m:directoryhash", namespaces=NS)
    }
    assert {"01_MASTERS", "02_PROYECTO", "02_PROYECTO/vacia"} <= dirs

    # the directory hashes we computed match the reference's own computation
    monkeypatch.undo()
    verify_dh = run_cli("ascmhl-debug", "verify", "-dh", project)
    assert verify_dh.returncode == 0, verify_dh.stdout + verify_dh.stderr
    monkeypatch.setattr(hasher_module, "hash_file", guarded)
    monkeypatch.setattr("ascmhl.hashlist.hash_file", guarded)
    monkeypatch.setattr("ascmhl.history.hasher.hash_file", guarded)

    # second generation: unchanged files -> verified, one added -> original
    write_file(project / "01_MASTERS" / "spot_30s.mov", b"master-30")
    gen2 = write_project_generation(project, hashes_for(project), tool_version="0.0.1")
    assert gen2.name.startswith("0002_")
    acts2 = actions(gen2)
    assert acts2.pop(("01_MASTERS/spot_30s.mov", "xxh128")) == "original"
    assert set(acts2.values()) == {"verified"}

    monkeypatch.undo()
    for manifest in (gen1, gen2):
        assert_xsd_valid(manifest)
    assert_chain_xsd_valid(project / "ascmhl" / "ascmhl_chain.xml")
    assert [(n, p) for n, p, _ in chain_entries(project)] == [(1, gen1.name), (2, gen2.name)]

    verify = run_cli("ascmhl-debug", "verify", project)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    # `verify -dh` compares against the directory hashes of *every*
    # generation, so after an addition generation 1's are stale (exit 12).
    verify_dh = run_cli("ascmhl-debug", "verify", "-dh", project)
    assert verify_dh.returncode == 12, verify_dh.stdout + verify_dh.stderr
    assert "(generation 0001)" in verify_dh.stdout + verify_dh.stderr
    assert "(generation 0002)" not in verify_dh.stdout + verify_dh.stderr

    history = MHLHistory.load_from_path(str(project))
    assert [h.generation_number for h in history.hash_lists] == [1, 2]
    assert not list(project.joinpath("ascmhl").glob(".*.tmp"))


def test_root_hash_matches_ascmhl_create(archive: Path, tmp_path: Path) -> None:
    """Our directory/root hashes equal those of `ascmhl create` on a copy."""
    project, _ = projects(archive)
    copy = tmp_path / "copy" / project.name
    shutil.copytree(project, copy)
    ours = write_project_generation(project, hashes_for(project))
    ignore = ["-i", "._*", "-i", "Thumbs.db", "-i", "@eaDir", "-i", r"\#recycle"]
    ref = run_cli("ascmhl", "create", copy, *ignore)
    assert ref.returncode == 0, ref.stdout + ref.stderr
    theirs = next((copy / "ascmhl").glob("0001_*.mhl"))

    def roothash(path: Path) -> tuple[str | None, str | None]:
        t = etree.parse(str(path))
        base = "m:processinfo/m:roothash/m:{}/m:xxh128"
        return (
            t.findtext(base.format("content"), namespaces=NS),
            t.findtext(base.format("structure"), namespaces=NS),
        )

    assert roothash(ours) == roothash(theirs)
    assert actions(ours) == actions(theirs)


def test_refuses_partial_or_failed_generation(archive: Path) -> None:
    project, _ = projects(archive)
    full = hashes_for(project)
    partial = dict(list(full.items())[:-1])
    with pytest.raises(MHLWriteError, match="no precomputed hash"):
        write_project_generation(project, partial)
    assert not (project / "ascmhl").exists() or not list((project / "ascmhl").glob("*.mhl"))

    write_project_generation(project, full)
    tampered = {**full, "02_PROYECTO/proyecto.drp": xxh(b"other")}
    with pytest.raises(MHLWriteError, match="hash mismatch"):
        write_project_generation(project, tampered)
    (project / "02_PROYECTO" / "proyecto.drp").unlink()
    with pytest.raises(MHLWriteError):
        write_project_generation(project, hashes_for(project))
    assert len(list((project / "ascmhl").glob("*.mhl"))) == 1


# --- goal 2: two formats for one file (D39) ----------------------------------


def test_two_formats_inherited_legacy_hash(archive: Path) -> None:
    project, _ = projects(archive)
    hashes = hashes_for(project)
    master = "01_MASTERS/spot_20s.mov"
    data = (project / master).read_bytes()
    hashes[master] = {**hashes[master], "xxh64": xxhash.xxh64(data).hexdigest()}

    gen1 = write_project_generation(project, hashes, inherited_formats={"xxh64"})
    acts = actions(gen1)
    assert acts[(master, "xxh128")] == "original"
    assert acts[(master, "xxh64")] == "verified"
    assert_xsd_valid(gen1)

    gen2 = write_project_generation(project, hashes, inherited_formats={"xxh64"})
    acts2 = actions(gen2)
    assert acts2[(master, "xxh128")] == "verified"
    assert acts2[(master, "xxh64")] == "verified"
    assert_xsd_valid(gen2)
    verify = run_cli("ascmhl-debug", "verify", project)
    assert verify.returncode == 0, verify.stdout + verify.stderr

    # without inheritance both are original in the same generation
    _, other = projects(archive)
    h = hashes_for(other)
    rel = "01_MASTERS/spot.mov"
    h[rel] = {**h[rel], "xxh64": xxhash.xxh64(b"otra").hexdigest()}
    g = write_project_generation(other, h)
    assert actions(g)[(rel, "xxh64")] == "original"
    assert_xsd_valid(g)


# --- goal 3: references-only root history (D29) ------------------------------


def test_reference_writer_cannot_omit_hashes(tmp_path: Path) -> None:
    """Evidence: ascmhl's writer always emits <hashes>, empty -> XSD-invalid."""
    hash_list = MHLHashList()
    hash_list.creator_info = _creator_info("0")
    hash_list.process_info.process = MHLProcess("in-place")
    out = tmp_path / "ascmhl" / "0001_x.mhl"
    hashlist_xml_parser.write_hash_list(hash_list, str(out))
    assert "<hashes>" in out.read_text()
    result = run_cli("ascmhl-debug", "xsd-schema-check", out, "-xsd", XSD_DIR / "ASCMHL.xsd")
    assert result.returncode != 0


def test_root_references_only(archive: Path) -> None:
    a, b = projects(archive)
    write_project_generation(a, hashes_for(a))
    write_file(a / "01_MASTERS" / "nuevo.mov", b"nuevo")
    latest_a = write_project_generation(a, hashes_for(a))
    latest_b = write_project_generation(b, hashes_for(b))

    root1 = write_root_references_generation(archive, [a, b], tool_version="0.0.1")
    assert root1.parent == archive / "ascmhl"
    assert root1.name.startswith("0001_ARCHIVO_")
    tree = etree.parse(str(root1))
    assert tree.find("m:hashes", namespaces=NS) is None
    refs = [
        (r.findtext("m:path", namespaces=NS), r.findtext("m:c4", namespaces=NS))
        for r in tree.findall("m:references/m:hashlistreference", namespaces=NS)
    ]
    rel = [p.relative_to(archive).as_posix() for p in (latest_a, latest_b)]
    assert [p for p, _ in refs] == rel
    for (_, c4), manifest in zip(refs, (latest_a, latest_b), strict=True):
        tmp = MHLHashList()
        tmp.file_path = str(manifest)
        assert c4 == tmp.generate_reference_hash()
    assert_xsd_valid(root1)

    # a second root generation after project B changes
    write_file(b / "01_MASTERS" / "otro.mov", b"x")
    latest_b2 = write_project_generation(b, hashes_for(b))
    root2 = write_root_references_generation(archive, [a, b], tool_version="0.0.1")
    assert root2.name.startswith("0002_")
    assert_xsd_valid(root2)
    assert_chain_xsd_valid(archive / "ascmhl" / "ascmhl_chain.xml")
    chain = chain_entries(archive)
    assert [(n, p) for n, p, _ in chain] == [(1, root1.name), (2, root2.name)]
    parsed_chain = chain_xml_parser.parse(str(archive / "ascmhl" / "ascmhl_chain.xml"))
    assert [g.hash_format for g in parsed_chain.generations] == ["c4", "c4"]

    # library reload: children found, references resolved to their hash lists
    history = MHLHistory.load_from_path(str(archive))
    assert sorted(history.child_history_mappings) == sorted(
        p.relative_to(archive).as_posix() for p in (a, b)
    )
    latest_root = history.hash_lists[-1]
    assert [os.path.basename(h.file_path) for h in latest_root.referenced_hash_lists] == [
        latest_a.name,
        latest_b2.name,
    ]
    assert latest_root.process_info.root_media_hash is None

    info = run_cli("ascmhl", "info", "-v", archive)
    assert info.returncode == 0, info.stdout + info.stderr
    assert "should have hash" not in info.stdout + info.stderr
    assert "Child History at" in info.stdout

    verify = run_cli("ascmhl-debug", "verify", archive)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "hashes" not in (verify.stdout + verify.stderr).lower()

    # Reference bug: `verify -dh` assumes every manifest has <roothash>
    # (commands.py verify_directory_hash_subcommand) -> AttributeError, exit 1.
    verify_dh = run_cli("ascmhl-debug", "verify", "-dh", archive)
    assert verify_dh.returncode == 1, verify_dh.stdout + verify_dh.stderr
    assert "'NoneType' object has no attribute 'hash_entries'" in verify_dh.stderr

    # D29 consequence: a loose file under the root but outside every project
    # has no record anywhere -> the reference reports it as new (exit 21).
    write_file(archive / "LEEME.txt", b"loose")
    loose = run_cli("ascmhl-debug", "verify", archive)
    assert loose.returncode == 21, loose.stdout + loose.stderr


def test_root_requires_child_history(archive: Path) -> None:
    a, b = projects(archive)
    write_project_generation(a, hashes_for(a))
    with pytest.raises(MHLWriteError, match="no ASC MHL history"):
        write_root_references_generation(archive, [a, b])
    assert not (archive / "ascmhl").exists() or not list((archive / "ascmhl").glob("*.mhl"))
