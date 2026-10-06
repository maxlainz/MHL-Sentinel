"""Finder tags on project folders by state (D70), opt-in in Settings.

The tags are a visual hint for the team in the macOS Finder; the database stays the source of
truth. A Finder tag lives in the ``com.apple.metadata:_kMDItemUserTags`` extended attribute: a
binary plist with a list of ``"Name\\n<colour index>"`` strings. Over SMB the Mac sends it as a
named stream, and Samba with ``vfs_fruit`` + ``streams_xattr`` stores the stream as the Linux
xattr ``user.DosStream.<stream>:$DATA`` with one trailing NUL byte. The container writes the
folder on disk, not through SMB, so it writes that exact form. That the NAS stores tags this way
is an assumption until it is measured there (D70).

With the option on, the app owns the tags of a project folder: it leaves exactly one of its
three tags (none for ``ignored``) and removes any other, and it puts right a tag changed by hand,
so the tag always tells the real state and cannot be faked from the Finder (D72). With the option
off it touches nothing. A tag change touches neither file contents nor the folder mtime, so it
never shows up as a change.

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


def write_raw(path: Path, data: bytes) -> None:
    """Store ``data`` as the stream."""
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


def encode(entries: list[str]) -> bytes:
    return plistlib.dumps(entries, fmt=plistlib.FMT_BINARY)


def wanted(tag: str | None) -> list[str]:
    """The whole tag list a project folder must carry: the app's tag alone, or nothing."""
    return [] if tag is None else [f"{tag}\n{COLOURS[tag]}"]


def apply(path: Path, tag: str | None) -> bool:
    """Leave ``path`` with exactly ``tag`` (``None``: no tag at all). True if it wrote.

    Any other tag goes, whoever set it and whatever its colour or name, and so does a stream
    that cannot be read: a tag nobody can fake is the point (D72). "No tag" is written as an
    empty list rather than by removing the stream, so the Finder does not fall back to a colour
    label left in the folder's Finder info (unmeasured, #24)."""
    want = wanted(tag)
    if decode(read_raw(path)) == want:
        return False
    write_raw(path, encode(want))
    return True


def touch(path: Path) -> None:
    """Set the mtime of ``path`` to now (contents untouched)."""
    os.utime(path)


@dataclass
class TagSync:
    """Brings the folders' tags in line with the project states (D70, D72).

    Every call reads the tags of every project folder and rewrites the ones that differ, so a
    tag changed by hand in the Finder is put right at the next tick. Reading one small xattr per
    project is cheap; writing happens only on a difference. ``reported`` remembers which failure
    went to the log so an error is reported once, not every minute.
    """

    apply_fn: Callable[[Path, str | None], bool] = apply
    touch_fn: Callable[[Path], None] = touch
    reported: dict[int, str | None] = field(default_factory=dict)
    supported: bool = field(default_factory=lambda: hasattr(os, "setxattr"))  # not on macOS

    def sync(self, db: Database, archive_root: Path, enabled: bool) -> list[str]:
        """Returns the error messages (one per project and tag). With tags off it does
        nothing at all: the folders are the team's again (D72)."""
        errors: list[str] = []
        if not self.supported or not enabled:
            self.reported.clear()
            return errors
        parents: dict[Path, str] = {}  # folder to touch → a project in it, for the message
        projects = db.list_projects()
        live = {p.id for p in projects if p.state is not ProjectState.MISSING}
        for gone in self.reported.keys() - live:  # SQLite may reuse a forgotten project's id
            del self.reported[gone]
        for project in projects:
            if project.id not in live:
                continue
            tag = tag_for(project.state)
            folder = archive_root / project.rel_path
            try:
                if self.apply_fn(folder, tag):
                    parents.setdefault(folder.parent, project.rel_path)
            except OSError as exc:
                if project.id not in self.reported or self.reported[project.id] != tag:
                    self.reported[project.id] = tag
                    errors.append(
                        f"Finder tag not set on {project.rel_path}: {exc.strerror or exc}"
                    )
                continue
            self.reported.pop(project.id, None)
        for parent, rel_path in parents.items():
            try:
                self.touch_fn(parent)
            except OSError as exc:  # the tag is set; Macs just see it later
                errors.append(f"Finder may show the old tag of {rel_path}: {exc.strerror or exc}")
        return errors
