"""Write ASC MHL generations from precomputed hashes (hito 0 spike, D28/D29).

Two entry points:

- :func:`write_project_generation` appends one generation to the history of a
  project folder using ``ascmhl``'s own model (``MHLHistory``,
  ``MHLGenerationCreationSession``) and writer, but with hashes computed by us
  (D28): the library never re-reads the media files.
- :func:`write_root_references_generation` appends one generation to the
  history at the archive root whose manifest only holds ``<references>`` to
  the latest manifest of each project (D29). ``ascmhl``'s writer always emits a
  ``<hashes>`` element, which is XSD-invalid when empty, so this manifest is
  serialised here with lxml; the chain is still written by ``ascmhl``.

Both write the manifest and the chain to temporary files inside ``ascmhl/``
and rename them at the end (norm ``conformidad-mhl.md``: never a partial
generation). Temporary names do not end in ``.mhl``, so ``ascmhl`` ignores
them if a crash leaves them behind.

``ascmhl`` 1.2's library is not a stable API (research spec §2.6); this module
uses a few private methods of ``MHLHistory`` and pins that version (D1).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import platform
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from ascmhl import chain_xml_parser, hashlist_xml_parser, utils
from ascmhl.generator import MHLGenerationCreationSession
from ascmhl.hasher import DirectoryHashContext
from ascmhl.hashlist import MHLCreatorInfo, MHLHashList, MHLProcess, MHLTool
from ascmhl.history import MHLHistory
from ascmhl.ignore import MHLIgnoreSpec
from ascmhl.traverse import post_order_lexicographic
from lxml import etree

TOOL_NAME = "MHL Sentinel"
MHL_NAMESPACE = "urn:ASC:MHL:v2.0"

# Ignore patterns proposed for a NAS archive. They use gitignore syntax
# (spec App. C): a leading "#" starts a comment, so the Synology recycle bin
# must be escaped as "\#recycle" or it silently matches nothing.
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".DS_Store",
    "._*",
    "Thumbs.db",
    "@eaDir",
    r"\#recycle",
    "ascmhl/",
)

PRIMARY_HASH_FORMAT = "xxh128"  # D30


class MHLWriteError(RuntimeError):
    """The generation was not written; the history on disk is unchanged."""


def write_project_generation(
    project_root: str | os.PathLike[str],
    file_hashes: Mapping[str, Mapping[str, str]],
    ignore_patterns: Sequence[str] = DEFAULT_IGNORE_PATTERNS,
    tool_version: str = "0.0.0",
    *,
    directory_hash_formats: Sequence[str] = (PRIMARY_HASH_FORMAT,),
    inherited_formats: Collection[str] = (),
) -> Path:
    """Append a generation to the ASC MHL history of ``project_root``.

    ``file_hashes`` maps every file of the project (relative POSIX path, after
    applying the ignore patterns) to ``{hash_format: hex_digest}``. The set of
    paths must match the folder exactly and every file already in the history
    must still exist: otherwise nothing is written (D28, never a partial
    generation). Files are only ``stat``-ed for size and mtime, never read.

    Actions follow ``ascmhl``: first appearance -> ``original``; hash equal to
    the first one recorded for that format -> ``verified``; a format not yet
    recorded for a known file -> ``verified`` if the existing format verifies.
    A mismatch (``failed``) aborts the write: in MHL Sentinel a modification is
    a review case (D9, D17), not a generation.

    ``inherited_formats`` (D39): formats that, on a file's first appearance,
    are written as ``verified`` instead of ``original`` because they were
    checked against a legacy MHL 1.x manifest in the same read.

    Directory hashes and the root hash are computed from the precomputed file
    hashes for each format in ``directory_hash_formats`` (every file must carry
    them).

    Returns the path of the new manifest.
    """
    root = Path(project_root).resolve()
    history = MHLHistory.load_from_path(str(root))
    if history.child_histories:
        # Nested histories inside a project are out of scope for the spike:
        # the session would split hashes across several histories.
        raise MHLWriteError(f"nested ASC MHL histories inside {root} are not supported")

    ignore_spec = MHLIgnoreSpec(history.latest_ignore_patterns(), list(ignore_patterns))
    session = MHLGenerationCreationSession(history, ignore_spec)

    expected_paths = set(file_hashes)
    seen_paths: set[str] = set()
    seen_dirs: set[str] = {"."}
    failures: list[str] = []
    dir_content: dict[str, dict[str, str]] = {}
    dir_structure: dict[str, dict[str, str]] = {}

    for folder_path, children in post_order_lexicographic(str(root), ignore_spec.get_path_spec()):
        contexts = {fmt: DirectoryHashContext(fmt) for fmt in directory_hash_formats}
        for item_name, is_dir in children:
            item_path = os.path.join(folder_path, item_name)
            if is_dir:
                seen_dirs.add(Path(item_path).relative_to(root).as_posix())
                content = dir_content.pop(item_path)
                structure = dir_structure.pop(item_path)
                for fmt, context in contexts.items():
                    context.append_directory_hashes(item_path, content[fmt], structure[fmt])
                continue

            rel_path = Path(item_path).relative_to(root).as_posix()
            hashes = file_hashes.get(rel_path)
            if hashes is None:
                raise MHLWriteError(f"no precomputed hash for {rel_path}")
            missing_formats = [fmt for fmt in directory_hash_formats if fmt not in hashes]
            if missing_formats:
                raise MHLWriteError(f"{rel_path} lacks {missing_formats}")
            seen_paths.add(rel_path)

            failures.extend(
                _append_file(session, history, item_path, rel_path, hashes, inherited_formats)
            )
            for fmt, context in contexts.items():
                context.append_file_hash(item_path, hashes[fmt])

        content_lookup = {fmt: c.final_content_hash_str() for fmt, c in contexts.items()}
        structure_lookup = {fmt: c.final_structure_hash_str() for fmt, c in contexts.items()}
        dir_content[folder_path] = content_lookup
        dir_structure[folder_path] = structure_lookup
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(folder_path))
        # For the project root this sets <processinfo><roothash>.
        session.append_multiple_format_directory_hashes(
            folder_path, mtime, content_lookup, structure_lookup
        )

    unknown = expected_paths - seen_paths
    if unknown:
        raise MHLWriteError(f"hashes given for paths not on disk or ignored: {sorted(unknown)}")
    missing = (
        {Path(p).relative_to(root).as_posix() for p in history.set_of_file_paths()}
        - seen_paths
        - seen_dirs
    )  # the history also records directories
    if missing:
        raise MHLWriteError(f"files recorded in the history are missing: {sorted(missing)}")
    if failures:
        raise MHLWriteError(f"hash mismatch (review, not a generation): {failures}")

    new_hash_list = session.new_hash_lists[history]
    new_hash_list.creator_info = _creator_info(tool_version)
    new_hash_list.process_info.process = MHLProcess("in-place")
    new_hash_list.process_info.ignore_spec = session.get_relevant_ignore_pattern(history)
    # Turns "new" entries into "verified" and asserts the existing format was verified.
    history._validate_new_hash_list(new_hash_list)
    return _commit(history, new_hash_list, _write_with_ascmhl)


def write_root_references_generation(
    archive_root: str | os.PathLike[str],
    child_project_roots: Sequence[str | os.PathLike[str]],
    ignore_patterns: Sequence[str] = DEFAULT_IGNORE_PATTERNS,
    tool_version: str = "0.0.0",
) -> Path:
    """Append a references-only generation to the history at ``archive_root``.

    # D29: references-only root. The manifest holds one ``<hashlistreference>``
    (path relative to the archive root + C4 of the manifest file) per project,
    pointing at the latest generation of each project history, and no
    ``<hashes>`` element at all. No media file is read; only the child
    manifests are (C4 and the chain check done by ``MHLHistory.load_from_path``).
    ``<roothash>`` is omitted (optional in the XSD).
    """
    root = Path(archive_root).resolve()
    history = MHLHistory.load_from_path(str(root))

    references: list[tuple[str, str]] = []
    for child_root in child_project_roots:
        rel_child = os.path.relpath(Path(child_root).resolve(), root)
        child = history.child_history_mappings.get(rel_child)
        if child is None or not child.hash_lists:
            raise MHLWriteError(f"no ASC MHL history at {rel_child}")
        if child.parent_history is not history:
            raise MHLWriteError(f"{rel_child} is not a direct child history of {root}")
        latest = child.hash_lists[-1]
        rel_manifest = Path(os.path.relpath(latest.file_path, root)).as_posix()
        references.append((rel_manifest, str(latest.generate_reference_hash())))
    if not references:
        raise MHLWriteError("a references-only manifest needs at least one reference")

    patterns = MHLIgnoreSpec(history.latest_ignore_patterns(), list(ignore_patterns))
    xml = _references_only_manifest(
        _creator_info(tool_version), patterns.get_pattern_list(), references
    )

    def write(hash_list: Any, tmp_path: str) -> None:
        Path(tmp_path).parent.mkdir(exist_ok=True)
        Path(tmp_path).write_bytes(xml)
        hash_list.file_path = tmp_path

    return _commit(history, MHLHashList(), write)


# --- internals -------------------------------------------------------------


def _append_file(
    session: Any,
    history: Any,
    abs_path: str,
    rel_path: str,
    hashes: Mapping[str, str],
    inherited_formats: Collection[str],
) -> list[str]:
    """Add one file's hashes to the session; return the formats that failed.

    Formats already in the history go first, as ``ascmhl create`` does
    (``seal_file_path``): a new format is only accepted once an existing one
    verified. ``append_multiple_format_file_hashes`` is not used because in
    ``ascmhl`` 1.2 it seeds its entry list with the ``MHLHashEntry`` class
    itself (``hash_entries = [MHLHashEntry]``) and crashes when writing.
    """
    stat = os.stat(abs_path)
    mtime = dt.datetime.fromtimestamp(stat.st_mtime)
    existing: list[str] = list(history.find_existing_hash_formats_for_path(rel_path))
    if existing and not any(fmt in hashes for fmt in existing):
        raise MHLWriteError(f"{rel_path}: none of the recorded formats {existing} was given")
    is_new_file = history.find_original_hash_entry_for_path(rel_path) is None
    ordered = [f for f in existing if f in hashes] + [f for f in hashes if f not in existing]

    failed: list[str] = []
    for fmt in ordered:
        action = "verified" if is_new_file and fmt in inherited_formats else None
        ok = session.append_file_hash(
            abs_path, stat.st_size, mtime, fmt, hashes[fmt], action=action
        )
        if not ok:
            failed.append(f"{rel_path} ({fmt})")
    if is_new_file and all(fmt in inherited_formats for fmt in ordered):
        raise MHLWriteError(f"{rel_path}: at least one format must be original")
    return failed


def _creator_info(tool_version: str) -> Any:
    info = MHLCreatorInfo()
    info.tool = MHLTool(TOOL_NAME, tool_version)
    info.creation_date = utils.datetime_now_isostring()
    info.host_name = platform.node()
    return info


def _write_with_ascmhl(hash_list: Any, tmp_path: str) -> None:
    hashlist_xml_parser.write_hash_list(hash_list, tmp_path)


def _commit(history: Any, hash_list: Any, write: Any) -> Path:
    """Write manifest + chain to temp files, then rename (manifest first).

    Replaces ``MHLGenerationCreationSession.commit`` /
    ``MHLHistory.write_new_generation``, which write straight to the final
    paths (research spec §2.7). If the process dies between the two renames
    the manifest exists without a chain entry; ``ascmhl`` does not flag that,
    so the hito 1 recovery must look for it. The reverse order would leave a
    chain entry pointing at a missing manifest (exit 33 on every later load).
    """
    asc_dir = Path(history.asc_mhl_path)
    file_name, generation_number = history._new_generation_filename()
    final_path = asc_dir / str(file_name)
    tmp_manifest = asc_dir / f".{file_name}.tmp"
    chain_path = Path(history.chain.file_path)
    tmp_chain = asc_dir / f".{chain_path.name}.tmp"
    try:
        write(hash_list, str(tmp_manifest))
        _fsync(tmp_manifest)
        hash_list.generation_number = generation_number
        hash_list.file_path = str(tmp_manifest)  # write_chain reads the C4 from file_path
        history.chain.file_path = str(tmp_chain)
        chain_xml_parser.write_chain(history.chain, _ChainEntry(final_path, hash_list))
        _fsync(tmp_chain)
        os.replace(tmp_manifest, final_path)
        os.replace(tmp_chain, chain_path)
    finally:
        history.chain.file_path = str(chain_path)
        tmp_manifest.unlink(missing_ok=True)
        tmp_chain.unlink(missing_ok=True)
    hash_list.file_path = str(final_path)
    _fsync_dir(asc_dir)
    return final_path


class _ChainEntry:
    """What ``chain_xml_parser.write_chain`` reads from the new hash list:
    the final file name, the sequence number and the C4 of the (temp) file."""

    def __init__(self, final_path: Path, hash_list: Any) -> None:
        self.file_path = str(final_path)
        self.generation_number = hash_list.generation_number
        self._tmp_path = str(hash_list.file_path)

    def generate_reference_hash(self) -> str:
        tmp = MHLHashList()
        tmp.file_path = self._tmp_path
        return str(tmp.generate_reference_hash())


def _references_only_manifest(
    creator_info: Any, ignore_patterns: Sequence[str], references: Sequence[tuple[str, str]]
) -> bytes:
    """Serialise a manifest with creatorinfo, processinfo and references only.

    Element order and content follow ``ASCMHL.xsd`` (ascmitc/mhl v1.2): in
    ``HashListType`` ``hashes`` has ``minOccurs="0"``; ``references`` needs at
    least one ``hashlistreference`` (``path`` + ``c4``).
    """

    def el(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
        child = etree.SubElement(parent, f"{{{MHL_NAMESPACE}}}{tag}")
        if text is not None:
            child.text = text
        return child

    hashlist = etree.Element(
        f"{{{MHL_NAMESPACE}}}hashlist",
        nsmap={None: MHL_NAMESPACE},  # type: ignore[dict-item]  # lxml-stubs: default ns
    )
    hashlist.set("version", "2.0")

    creator = el(hashlist, "creatorinfo")
    el(creator, "creationdate", str(creator_info.creation_date))
    el(creator, "hostname", str(creator_info.host_name))
    tool = el(creator, "tool", str(creator_info.tool.name))
    tool.set("version", str(creator_info.tool.version))

    process = el(hashlist, "processinfo")
    el(process, "process", "in-place")
    ignore = el(process, "ignore")
    for pattern in ignore_patterns:
        el(ignore, "pattern", pattern)

    refs = el(hashlist, "references")
    for path, c4 in references:
        ref = el(refs, "hashlistreference")
        el(ref, "path", path)
        el(ref, "c4", c4)

    return etree.tostring(hashlist, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):  # not supported on every filesystem (e.g. some SMB)
        _fsync(path)
