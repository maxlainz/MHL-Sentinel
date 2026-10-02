"""References-only history at the archive root (D29, D43, issue #3; hito 4).

``<archive_root>/ascmhl/`` holds one generation per refresh whose manifest only lists
``<references>`` (path + C4) to the latest manifest of every project history the app tracks: "these
projects have an intact history at date X". No media file is read (vault note
`Historial ASC MHL anidado`). ``<roothash>`` is left out (D43, issue #3).

When to refresh (:func:`root_manifest_needed`):

- ``settings_kv["root_manifest_stale"] == "1"``: the sealer sets it after every generation
  (seal, append, accept, verify);
- the root history does not exist yet;
- the set of referenced projects changed since the last refresh (a project vanished, was ignored,
  or got its first history): H12, a reference to a missing child stops the resolution of the
  remaining references in the reference implementation, so the root is regenerated without it.

Which projects (:func:`referenced_projects`): every project with a history written by the app
(``last_generation_no``) that is neither ignored nor missing (D58) and whose ``ascmhl/`` chain is
on disk. Projects in
review or with new files keep their reference: their history is intact, only the files moved on.

Rebuild: ``ascmhl`` 1.2 resolves the references of *every* generation of a history when it loads
it and asserts that each referenced manifest still exists in its child history. After "Accept as
new version" (the project's old manifests move to ``ascmhl_superseded/``) or when a project folder
vanishes, the older root generations would keep the reference from loading the root at all. When
any generation of the root references a manifest that is no longer on disk, the root history is
retired to ``<root>/ascmhl_superseded/<stamp>/`` (D50, never deleted) and a fresh one starts with
generation 1.

Ignore patterns of the root manifest: the defaults, ``exclude_globs``, the latest patterns of each
referenced project, and one ``<prefix>*`` pattern per ignore prefix (D19). ``ascmhl verify`` at
the root matches patterns against absolute paths, so anchored patterns (``/_*``) never match; the
prefix patterns are therefore unanchored and also hide such names inside projects from a
root-level verify (the per-project verify still reads them).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from lxml import etree

from mhl_sentinel import __version__
from mhl_sentinel.clock import to_iso
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, ProjectRow
from mhl_sentinel.mhlwriter import (
    DEFAULT_IGNORE_PATTERNS,
    MHL_NAMESPACE,
    retire_history,
    write_root_references_generation,
)
from mhl_sentinel.models import ProjectState

log = logging.getLogger(__name__)

HISTORY_DIR = "ascmhl"
CHAIN_FILE = "ascmhl_chain.xml"
ROOT_MANIFEST_STALE_KEY = "root_manifest_stale"
LAST_ROOT_MANIFEST_KEY = "last_root_manifest_at"
ROOT_PROJECTS_KEY = "root_manifest_projects"  # JSON list of the rel_paths last referenced
ROOT_UPDATED_EVENT = "root.updated"

Publish = Callable[[str, dict[str, Any]], object]

_GLOB_SPECIAL = frozenset("\\*?[]!#")


def _has_history(folder: Path) -> bool:
    return (folder / HISTORY_DIR / CHAIN_FILE).is_file()


def referenced_projects(db: Database, settings: Settings) -> list[ProjectRow]:
    """Projects whose latest manifest the root references (see the module docstring)."""
    root = settings.archive_root
    return [
        p
        for p in db.list_projects()
        if p.last_generation_no is not None
        and p.state not in (ProjectState.IGNORED, ProjectState.MISSING)  # D58
        and _has_history(root / p.rel_path)
    ]


def prefix_patterns(settings: Settings) -> list[str]:
    """``_*``, ``@*``, ``\\#*``, ``.*`` for the default prefixes (D19), gitignore-escaped.

    A prefix that starts a component of the archive root's own absolute path is skipped: the
    reference matches patterns against absolute paths and would ignore the whole archive.
    """
    parts = settings.archive_root.resolve().parts[1:]
    out: list[str] = []
    for char in dict.fromkeys(settings.ignore_prefixes):
        if any(part.startswith(char) for part in parts):
            log.warning("ignore prefix %r is in the archive root path: not used at the root", char)
            continue
        out.append(("\\" + char if char in _GLOB_SPECIAL else char) + "*")
    return out


def root_ignore_patterns(settings: Settings) -> list[str]:
    patterns = list(DEFAULT_IGNORE_PATTERNS)
    for extra in [*settings.exclude_globs, *prefix_patterns(settings)]:
        if extra not in patterns:
            patterns.append(extra)
    return patterns


def root_manifest_needed(db: Database, settings: Settings) -> bool:
    """True if a refresh would write a new root generation (module docstring)."""
    projects = referenced_projects(db, settings)
    if not projects:
        return False
    if db.get_kv(ROOT_MANIFEST_STALE_KEY) == "1":
        return True
    if not _has_history(settings.archive_root):
        return True
    recorded = db.get_kv(ROOT_PROJECTS_KEY)
    return recorded is None or json.loads(recorded) != [p.rel_path for p in projects]


def dangling_references(archive_root: Path) -> list[str]:
    """Reference paths (relative to the root) of every root generation listed in the chain whose
    manifest file is gone. Read with lxml: loading the root through ``ascmhl`` is exactly what
    fails in that case."""
    asc = archive_root / HISTORY_DIR
    chain = asc / CHAIN_FILE
    if not chain.is_file():
        return []
    ns = {"m": MHL_NAMESPACE}
    chain_ns = {"d": "urn:ASC:MHL:DIRECTORY:v2.0"}
    out: list[str] = []
    for name in etree.parse(str(chain)).findall("d:hashlist/d:path", chain_ns):
        manifest = asc / str(name.text)
        if not manifest.is_file():
            continue  # the reference reports a broken chain by itself
        for ref in etree.parse(str(manifest)).findall(
            "m:references/m:hashlistreference/m:path", ns
        ):
            rel = str(ref.text)
            if not (archive_root / rel).is_file():
                out.append(rel)
    return out


def last_root_manifest_at(db: Database) -> str | None:
    return db.get_kv(LAST_ROOT_MANIFEST_KEY)


def refresh_root_manifest(
    db: Database,
    settings: Settings,
    *,
    now: datetime,
    publish: Publish | None = None,
    force: bool = False,
) -> Path | None:
    """Write a new references-only root generation if needed (or ``force``).

    Returns the new manifest, or ``None`` if nothing was written (nothing stale, or no project
    with a history yet). Clears the stale flag, records ``last_root_manifest_at`` and the
    referenced set, and publishes ``root.updated`` through ``publish`` if given. Raises what the
    writer raises (``MHLWriteError``, a broken child history...); the flag then stays set.
    """
    if not force and not root_manifest_needed(db, settings):
        return None
    projects = referenced_projects(db, settings)
    if not projects:
        return None
    root = settings.archive_root
    dangling = dangling_references(root)
    rebuilt = None
    if dangling:
        rebuilt = retire_history(root, now)
        log.warning(
            "root history references %d manifest(s) that are gone (%s): retired to %s",
            len(dangling),
            ", ".join(dangling[:3]),
            os.path.relpath(rebuilt, root) if rebuilt else "-",
        )
    manifest = write_root_references_generation(
        root,
        [root / p.rel_path for p in projects],
        ignore_patterns=root_ignore_patterns(settings),
        tool_version=__version__,
        include_child_patterns=True,
    )
    rel_paths = [p.rel_path for p in projects]
    db.set_kv(ROOT_MANIFEST_STALE_KEY, "0")
    db.set_kv(LAST_ROOT_MANIFEST_KEY, to_iso(now))
    db.set_kv(ROOT_PROJECTS_KEY, json.dumps(rel_paths))
    log.info("root manifest %s written (%d projects)", manifest.name, len(projects))
    if publish is not None:
        publish(
            ROOT_UPDATED_EVENT,
            {
                "manifest": manifest.name,
                "projects": len(projects),
                "at": to_iso(now),
                "rebuilt": rebuilt is not None,
            },
        )
    return manifest
