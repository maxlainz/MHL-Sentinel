"""Shared enums and value objects (contract: docs/arquitectura.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProjectState(StrEnum):
    """States of a project; transitions are decided by ``sealer.classify`` only."""

    UNSEALED = "unsealed"  # no ascmhl/ yet (D15, D31)
    QUEUED = "queued"  # seal/append requested, waiting for non-working hours
    HASHING = "hashing"  # job running (paused during working hours, D33)
    SEALED = "sealed"  # history exists and last scan matches sealed_files
    CHANGED = "changed"  # only added files since last seal → append after settle (D9, D48)
    NEEDS_REVIEW = "needs_review"  # modified/deleted/legacy mismatch/corruption (D9, D17)
    IGNORED = "ignored"  # Ignore button (D19)
    ERROR = "error"


class JobKind(StrEnum):
    SEAL = "seal"
    APPEND = "append"
    ACCEPT_NEW_VERSION = "accept_new_version"
    VERIFY = "verify"  # hito 4
    ROOT_MANIFEST = "root_manifest"  # hito 4


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Trigger(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class ChangeKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class FileStat:
    """One regular file inside a project. ``rel_path`` is POSIX, relative to the project root."""

    rel_path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class ProjectCandidate:
    """A folder found by discovery. ``rel_path`` is POSIX, relative to the archive root."""

    rel_path: str
    name: str
    has_history: bool  # an ascmhl/ folder exists


@dataclass(slots=True)
class ScanDiff:
    """Result of comparing a fresh scan with ``sealed_files`` (and the previous scan)."""

    files: list[FileStat] = field(default_factory=list)  # the fresh snapshot
    added: list[FileStat] = field(default_factory=list)
    modified: list[tuple[FileStat, FileStat]] = field(default_factory=list)  # (sealed, current)
    deleted: list[FileStat] = field(default_factory=list)  # from sealed_files
    stable: bool = False  # identical to the previous scan snapshot
    newest_mtime_ns: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)
