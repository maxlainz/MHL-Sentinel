"""Project scan (stat only, no hashing) and comparison with the sealed snapshot (D14, D31).

Pure module: filesystem in, dataclasses out.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import GitIgnoreSpec

from mhl_sentinel.models import FileStat, ScanDiff

HISTORY_DIR = "ascmhl"


@dataclass(slots=True)
class ScanOutcome:
    files: list[FileStat] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # "rel_path: reason"


def scan_project(
    project_root: Path, ignore_patterns: Sequence[str], exclude_globs: Sequence[str]
) -> ScanOutcome:
    """Recursively list regular files of a project.

    ``ascmhl/`` is always skipped. ``ignore_patterns`` use gitignore semantics (same as ASC MHL
    ignore, spec App. C; ``\\#recycle`` is the escaped form because ``#`` starts a comment).
    ``exclude_globs`` are fnmatch patterns on the basename (D14, e.g. ``*.md``). Symlinks are
    skipped. Per-entry errors are collected in ``errors``. Files sorted by ``rel_path``.
    """
    spec = GitIgnoreSpec.from_lines(list(ignore_patterns))
    out = ScanOutcome()
    _walk(project_root, "", spec, tuple(exclude_globs), out)
    out.files.sort(key=lambda f: f.rel_path)
    out.errors.sort()
    return out


def _walk(
    directory: Path, rel_dir: str, spec: GitIgnoreSpec, globs: tuple[str, ...], out: ScanOutcome
) -> None:
    try:
        it = os.scandir(directory)
    except OSError as exc:
        out.errors.append(f"{rel_dir or '.'}: {exc.strerror or exc}")
        return
    with it:
        for entry in it:
            rel = f"{rel_dir}{entry.name}"
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not rel_dir and entry.name == HISTORY_DIR:
                        continue
                    if spec.match_file(rel + "/"):
                        continue
                    _walk(Path(entry.path), rel + "/", spec, globs, out)
                elif entry.is_file(follow_symlinks=False):
                    if spec.match_file(rel) or any(
                        fnmatch.fnmatchcase(entry.name, g) for g in globs
                    ):
                        continue
                    st = entry.stat(follow_symlinks=False)
                    out.files.append(
                        FileStat(rel_path=rel, size=st.st_size, mtime_ns=st.st_mtime_ns)
                    )
            except OSError as exc:
                out.errors.append(f"{rel}: {exc.strerror or exc}")


def diff_against_sealed(
    files: Sequence[FileStat],
    sealed: Mapping[str, FileStat],
    previous: Mapping[str, FileStat] | None,
    mtime_tolerance_ns: int = 1_000_000_000,
) -> ScanDiff:
    """Compare a fresh scan with the sealed snapshot (``{}`` for an unsealed project).

    ``modified`` pairs are ``(sealed, current)``; a file is modified when its size differs or
    ``|mtime diff| > mtime_tolerance_ns``. ``stable`` means ``previous`` has exactly the same
    paths, sizes and mtimes as ``files``.
    """
    current = {f.rel_path: f for f in files}
    diff = ScanDiff(files=sorted(files, key=lambda f: f.rel_path))
    for path in sorted(current):
        cur = current[path]
        old = sealed.get(path)
        if old is None:
            diff.added.append(cur)
        elif old.size != cur.size or abs(old.mtime_ns - cur.mtime_ns) > mtime_tolerance_ns:
            diff.modified.append((old, cur))
    diff.deleted = [sealed[p] for p in sorted(sealed) if p not in current]
    diff.stable = previous is not None and dict(previous) == current
    diff.newest_mtime_ns = max((f.mtime_ns for f in files), default=0)
    return diff


def is_settled(diff: ScanDiff, now_ns: int, settle_hours: float) -> bool:
    """D31: stable between scans and newest mtime older than ``settle_hours``."""
    return diff.stable and now_ns - diff.newest_mtime_ns >= settle_hours * 3600e9
