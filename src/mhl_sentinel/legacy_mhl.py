"""Read legacy MHL 1.x manifests to verify files and seed expected hashes (D39).

Legacy manifests are flat ``<hashlist version="1.0|1.1">`` files, one per
offload folder, with paths relative to the folder holding the ``.mhl``. They
are not ASC MHL (no namespace ``urn:ASC:MHL:v2.0``) and ``ascmhl`` has no
import for them, so we read them ourselves and translate the digests to ASC MHL
format names (research spec §5):

- ``md5``, ``sha1``: same name, same hex.
- ``xxhash64be``: canonical big-endian hex = ASC MHL ``xxh64``.
- ``xxhash64``: little-endian byte order; reversed, it is ``xxh64``.
- ``xxhash``: XXH32 as a decimal number; no ASC MHL equivalent, skipped with a
  warning.

Pure module: no database, no config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

ASC_MHL_NAMESPACE = "urn:ASC:MHL:v2.0"
HISTORY_DIR = "ascmhl"
_LEGACY_VERSIONS = {"1.0", "1.1"}
_HEX16 = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True, slots=True)
class LegacyEntry:
    """One ``<hash>`` of a legacy manifest. ``rel_path`` is relative to the project root."""

    rel_path: str
    size: int | None
    hashes: dict[str, str]  # ASC MHL format name -> lowercase hex digest


@dataclass(slots=True)
class LegacyManifest:
    path: Path
    entries: dict[str, LegacyEntry] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _root_info(path: Path) -> tuple[str, str | None] | None:
    """Return (root tag, version attribute) or None if the file is not readable XML."""
    try:
        for _event, elem in ElementTree.iterparse(path, events=("start",)):
            return elem.tag, elem.get("version")
    except (ElementTree.ParseError, OSError):
        return None
    return None


def find_legacy_manifests(project_root: Path) -> list[Path]:
    """Every ``*.mhl`` outside ``ascmhl/`` whose root is a 1.0/1.1 ``<hashlist>``."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = sorted(d for d in dirnames if d != HISTORY_DIR)
        for name in sorted(filenames):
            if not name.lower().endswith(".mhl"):
                continue
            path = Path(dirpath) / name
            info = _root_info(path)
            if info is None:
                continue
            tag, version = info
            if tag == "hashlist" and version in _LEGACY_VERSIONS:
                found.append(path)
    return found


def _reverse_hex(value: str) -> str:
    return bytes.fromhex(value)[::-1].hex()


def _convert(tag: str, text: str) -> tuple[str, str] | None:
    """Map a legacy hash element to (ASC MHL format, hex); None if unsupported/invalid."""
    value = text.strip().lower()
    if tag in ("md5", "sha1"):
        return tag, value
    if tag == "xxhash64be":
        return "xxh64", value
    if tag == "xxhash64":
        if len(value) != 16 or not set(value) <= _HEX16:
            return None
        return "xxh64", _reverse_hex(value)
    return None


def read_legacy_manifest(path: Path, project_root: Path) -> LegacyManifest:
    manifest = LegacyManifest(path=path)
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError) as exc:
        manifest.warnings.append(f"{path.name}: unreadable ({exc})")
        return manifest
    base = path.parent.relative_to(project_root)
    for node in root.findall("hash"):
        raw_name = (node.findtext("file") or "").strip().replace("\\", "/")
        if not raw_name:
            manifest.warnings.append(f"{path.name}: <hash> without <file>")
            continue
        rel = (PurePosixPath(base.as_posix()) / raw_name).as_posix()
        rel = os.path.normpath(rel).replace(os.sep, "/")
        size_text = (node.findtext("size") or "").strip()
        size = int(size_text) if size_text.isdigit() else None
        hashes: dict[str, str] = {}
        for child in node:
            if child.tag in ("file", "size", "lastmodificationdate", "hashdate", "directory"):
                continue
            if child.tag == "xxhash":
                manifest.warnings.append(
                    f"{path.name}: {rel}: XXH32 (xxhash) has no ASC MHL equivalent, skipped"
                )
                continue
            converted = _convert(child.tag, child.text or "")
            if converted is None:
                if child.tag in ("md5", "sha1", "xxhash64", "xxhash64be"):
                    manifest.warnings.append(f"{path.name}: {rel}: invalid {child.tag} value")
                continue
            fmt, digest = converted
            hashes.setdefault(fmt, digest)
        if not hashes:
            continue
        previous = manifest.entries.get(rel)
        if previous is None:
            manifest.entries[rel] = LegacyEntry(rel, size, hashes)
        else:
            for fmt, digest in hashes.items():
                if previous.hashes.setdefault(fmt, digest) != digest:
                    manifest.warnings.append(f"{path.name}: {rel}: conflicting {fmt}, kept first")
    return manifest


def expected_hashes_for_project(project_root: Path) -> dict[str, dict[str, str]]:
    """Merge all legacy manifests: ``{rel_path: {format: digest}}``; first value wins."""
    merged, _warnings = expected_hashes_with_warnings(project_root)
    return merged


def expected_hashes_with_warnings(
    project_root: Path,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    merged: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    for path in find_legacy_manifests(project_root):
        manifest = read_legacy_manifest(path, project_root)
        warnings.extend(manifest.warnings)
        for rel, entry in manifest.entries.items():
            slot = merged.setdefault(rel, {})
            for fmt, digest in entry.hashes.items():
                if slot.setdefault(fmt, digest) != digest:
                    warnings.append(
                        f"{path.name}: {rel}: conflicting {fmt} across manifests, kept first"
                    )
    return merged, warnings
