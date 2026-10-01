"""Project discovery by folder levels (D18) with prefix-based ignores (D19).

Pure module: filesystem in, dataclasses out. Persistence is the caller's job.
"""

from __future__ import annotations

import os
from pathlib import Path

from mhl_sentinel.models import ProjectCandidate

HISTORY_DIR = "ascmhl"
# Never projects nor stray entries: the root history of the archive (D29) lives in
# <root>/ascmhl/, and a superseded history (D50) is never a project either.
RESERVED_NAMES: frozenset[str] = frozenset({HISTORY_DIR, "ascmhl_superseded"})


def _list_dirs_and_others(path: Path, ignore_prefixes: str) -> tuple[list[str], list[str]]:
    """Return (directories, other entries) of ``path``, names only, ignored ones dropped.

    Symlinks are never followed: a symlink counts as "other" even if it points to a directory.
    """
    dirs: list[str] = []
    others: list[str] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.name.startswith(tuple(ignore_prefixes)) or entry.name in RESERVED_NAMES:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(entry.name)
                else:
                    others.append(entry.name)
    except OSError:
        return [], []
    return sorted(dirs), sorted(others)


def discover_projects(
    root: Path, project_depth: int, ignore_prefixes: str = "_@#."
) -> list[ProjectCandidate]:
    """Find project folders: ``root`` plus ``project_depth`` directory levels (D18).

    ``project_depth=1`` -> projects are ``root/<X>/<project>`` (e.g. year folders:
    ``2024/2024-03_CLIENTE-A_CAMPANA-UNO``). ``project_depth=0`` -> projects are the direct
    children ``root/<project>``. Intermediate levels are transparent whatever their names.
    Entries whose name starts with any char of ``ignore_prefixes`` are skipped at every level
    (D19). Symlinks are not followed. Result is sorted by ``rel_path`` (POSIX).
    """
    if project_depth < 0:
        raise ValueError("project_depth must be >= 0")
    level: list[Path] = [root]
    for _ in range(project_depth):
        nxt: list[Path] = []
        for parent in level:
            nxt.extend(parent / n for n in _list_dirs_and_others(parent, ignore_prefixes)[0])
        level = nxt
    found: list[ProjectCandidate] = []
    for parent in level:
        for name in _list_dirs_and_others(parent, ignore_prefixes)[0]:
            path = parent / name
            found.append(
                ProjectCandidate(
                    rel_path=path.relative_to(root).as_posix(),
                    name=name,
                    has_history=(path / HISTORY_DIR).is_dir(),
                )
            )
    return sorted(found, key=lambda c: c.rel_path)


def find_stray_entries(root: Path, project_depth: int, ignore_prefixes: str = "_@#.") -> list[str]:
    """Non-ignored non-directory entries at the root or at intermediate levels.

    Those are neither containers nor projects (e.g. a loose ``notes.txt`` inside a year folder).
    Returns POSIX paths relative to ``root``, sorted.
    """
    if project_depth < 0:
        raise ValueError("project_depth must be >= 0")
    stray: list[str] = []
    level: list[Path] = [root]
    for _ in range(project_depth + 1):
        nxt: list[Path] = []
        for parent in level:
            dirs, others = _list_dirs_and_others(parent, ignore_prefixes)
            stray.extend((parent / n).relative_to(root).as_posix() for n in others)
            nxt.extend(parent / n for n in dirs)
        level = nxt
        if not level:
            break
    # The last iterated level holds the projects' parents' children = projects (dirs); files there
    # were already counted above, which is what we want. Levels beyond are not visited.
    return sorted(stray)
