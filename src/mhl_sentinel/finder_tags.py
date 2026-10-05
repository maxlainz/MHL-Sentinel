"""Finder tags on project folders by state (D70), opt-in in Settings.

The tags are a visual hint for the team in the macOS Finder; the database stays the source of
truth. A Finder tag lives in the ``com.apple.metadata:_kMDItemUserTags`` extended attribute: a
binary plist with a list of ``"Name\\n<colour index>"`` strings. Over SMB the Mac sends it as a
named stream, and Samba with ``vfs_fruit`` + ``streams_xattr`` stores the stream as the Linux
xattr ``user.DosStream.<stream>:$DATA`` with one trailing NUL byte. The container writes the
folder on disk, not through SMB, so it writes that exact form. That the NAS stores tags this way
is an assumption until it is measured there (D70).

The app only adds or removes its own three tags and keeps any other tag the team has set. A tag
change touches neither file contents nor the folder mtime, so it never shows up as a change.

The macOS SMB client caches a folder listing, tags of the subfolders included, for as long as
that folder's mtime does not change (see the note `Caché de directorios del cliente SMB en
macOS`). So after retagging, the app bumps the mtime of each parent folder, like ``touch``.
"""

from __future__ import annotations

import errno
import os
import plistlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mhl_sentinel.db import Database
from mhl_sentinel.models import ProjectState

STREAM = "com.apple.metadata:_kMDItemUserTags"
XATTR = f"user.DosStream.{STREAM}:$DATA"  # Samba streams_xattr naming

# Finder colour indexes: 0 none, 1 grey, 2 green, 3 purple, 4 blue, 5 yellow, 6 red, 7 orange.
TAG_OK = "MHL OK"
TAG_PENDING = "MHL pendiente"
TAG_REVIEW = "MHL revisar"
COLOURS: dict[str, int] = {TAG_OK: 2, TAG_PENDING: 5, TAG_REVIEW: 6}

_TAG_FOR_STATE: dict[ProjectState, str] = {
    ProjectState.SEALED: TAG_OK,
    ProjectState.UNSEALED: TAG_PENDING,
    ProjectState.CHANGED: TAG_PENDING,
    ProjectState.QUEUED: TAG_PENDING,
    ProjectState.HASHING: TAG_PENDING,
    ProjectState.NEEDS_REVIEW: TAG_REVIEW,
    ProjectState.ERROR: TAG_REVIEW,
}  # ignored and missing carry no tag


def tag_for(state: ProjectState) -> str | None:
    return _TAG_FOR_STATE.get(state)


_NO_XATTR = {getattr(errno, n) for n in ("ENODATA", "ENOATTR") if hasattr(errno, n)}


def _xattr_missing(exc: OSError) -> bool:
    return exc.errno in _NO_XATTR


def read_raw(path: Path) -> bytes | None:
    """The stored stream (trailing NUL removed), or None if the folder has no tags."""
    try:
        raw: bytes = os.getxattr(path, XATTR)  # type: ignore[attr-defined,unused-ignore]
    except OSError as exc:
        if _xattr_missing(exc):
            return None
        raise
    return raw[:-1] if raw.endswith(b"\0") else raw


def write_raw(path: Path, data: bytes | None) -> None:
    """Store ``data`` as the stream, or remove it (``None``)."""
    if data is None:
        try:
            os.removexattr(path, XATTR)  # type: ignore[attr-defined,unused-ignore]
        except OSError as exc:
            if not _xattr_missing(exc):
                raise
        return
    os.setxattr(path, XATTR, data + b"\0")  # type: ignore[attr-defined,unused-ignore]


def decode(data: bytes | None) -> list[str] | None:
    """Tag entries of a stream (``[]`` if there is none), or None if it cannot be read."""
    if data is None:
        return []
    try:
        value = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError):
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return value


def encode(entries: list[str]) -> bytes | None:
    if not entries:
        return None
    return plistlib.dumps(entries, fmt=plistlib.FMT_BINARY)


def _name(entry: str) -> str:
    return entry.split("\n", 1)[0]


def merged(entries: list[str], tag: str | None) -> list[str]:
    """``entries`` without the app's tags, plus ``tag`` (with its colour) if given."""
    out = [e for e in entries if _name(e) not in COLOURS]
    if tag is not None:
        out.append(f"{tag}\n{COLOURS[tag]}")
    return out


def apply(path: Path, tag: str | None) -> bool:
    """Leave ``path`` with ``tag`` as the only app tag (``None``: none). True if it wrote."""
    entries = decode(read_raw(path))
    if entries is None:
        return False  # never overwrite a stream we cannot read
    new = merged(entries, tag)
    if new == entries:
        return False
    write_raw(path, encode(new))
    return True


def touch(path: Path) -> None:
    """Set the mtime of ``path`` to now (contents untouched)."""
    os.utime(path)


@dataclass
class TagSync:
    """Brings the folders' tags in line with the project states, writing only on a change.

    ``applied`` remembers what each project got (or failed to get) so a tick writes nothing when
    no state changed and an error is reported once, not every minute. After a restart it is
    empty, so every folder is checked once; with tags disabled that clears any left behind.
    """

    apply_fn: Callable[[Path, str | None], bool] = apply
    touch_fn: Callable[[Path], None] = touch
    applied: dict[int, str | None] = field(default_factory=dict)
    supported: bool = field(default_factory=lambda: hasattr(os, "setxattr"))  # not on macOS

    def sync(self, db: Database, archive_root: Path, enabled: bool) -> list[str]:
        """Returns the error messages (one per project and state). With tags off, a failure
        to clean up is not reported: the folder may simply not support extended attributes."""
        errors: list[str] = []
        if not self.supported:
            return errors
        parents: dict[Path, str] = {}  # folder to touch → a project in it, for the message
        for project in db.list_projects():
            if project.state is ProjectState.MISSING:
                self.applied.pop(project.id, None)
                continue
            tag = tag_for(project.state) if enabled else None
            if project.id in self.applied and self.applied[project.id] == tag:
                continue
            self.applied[project.id] = tag
            folder = archive_root / project.rel_path
            try:
                if self.apply_fn(folder, tag):
                    parents.setdefault(folder.parent, project.rel_path)
            except OSError as exc:
                if enabled:
                    errors.append(
                        f"Finder tag not set on {project.rel_path}: {exc.strerror or exc}"
                    )
        for parent, rel_path in parents.items():
            try:
                self.touch_fn(parent)
            except OSError as exc:  # the tag is set; Macs just see it later
                errors.append(f"Finder may show the old tag of {rel_path}: {exc.strerror or exc}")
        return errors
