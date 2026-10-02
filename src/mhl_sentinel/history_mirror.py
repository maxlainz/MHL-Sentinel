"""Mirror of every project's ``ascmhl/`` history in ``/config`` (D59).

``<config>/history/<rel_path>/ascmhl/`` holds a copy of the project's history, refreshed after
every generation the app writes and, at scan time, whenever the chain on disk differs from the
mirrored one (so histories written by other tools are mirrored too). A history superseded by
"Accept as new version" moves to ``<config>/history/<rel_path>/ascmhl_superseded/<stamp>/`` like
the original (D17, D50).

What it is for: a backup of the history if the project folder vanishes (D58), the source of the
history download of a missing project (D60, :func:`zip_history`) and the fingerprint that
recognises a moved or renamed folder (D62: same ``ascmhl_chain.xml`` bytes). It is never used to
verify files: only the files on the archive are evidence.

Rules: new files are copied; ``ascmhl_chain.xml`` is overwritten when it differs (and copied
last, so a mirror never lists a manifest it does not hold); nothing is ever deleted from the
mirror except by :func:`remove_mirror` (Retire, D60). Every copy goes to a temporary file that is
renamed into place, so a SIGKILL never leaves half a manifest. The scan thread and the hasher
thread may both sync the same project: every operation holds one module lock.

A mirror holding a file the history on disk no longer has belongs to an earlier history (the
history was replaced by another tool, or setting the mirror aside on Accept failed): it is set
aside as ``ascmhl_superseded/<stamp>/`` before the copy, so the mirror never mixes two histories
(``ascmhl`` would load the stray manifests as generations).
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import threading
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from mhl_sentinel.config import Settings
from mhl_sentinel.db import ProjectRow

log = logging.getLogger(__name__)

MIRROR_ROOT = "history"
HISTORY_DIR = "ascmhl"
SUPERSEDED_DIR = "ascmhl_superseded"
CHAIN_FILE = "ascmhl_chain.xml"
TEMP_SUFFIX = ".mirror.tmp"

_LOCK = threading.RLock()  # scan thread and hasher thread (module docstring)


def _project_base(settings: Settings, rel_path: str) -> Path:
    rel = PurePosixPath(rel_path)
    if rel.is_absolute() or not rel.parts or any(p in ("", ".", "..") for p in rel.parts):
        raise ValueError(f"not a project path relative to the archive root: {rel_path!r}")
    return settings.config_dir / MIRROR_ROOT / Path(*rel.parts)


def mirror_dir(settings: Settings, rel_path: str) -> Path:
    """``<config>/history/<rel_path>/ascmhl`` (it may not exist)."""
    return _project_base(settings, rel_path) / HISTORY_DIR


def _read_chain(folder: Path) -> bytes | None:
    try:
        return (folder / CHAIN_FILE).read_bytes()
    except OSError:
        return None


def chain_bytes(history_dir: Path) -> bytes | None:
    """Bytes of ``<history_dir>/ascmhl_chain.xml``, or ``None`` if it cannot be read."""
    return _read_chain(history_dir)


def needs_sync(project_root: Path, mirror: Path) -> bool:
    """True if the project has a chain and the mirror has none or a different one (D59)."""
    on_disk = _read_chain(project_root / HISTORY_DIR)
    return on_disk is not None and on_disk != _read_chain(mirror)


def _copy_atomic(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}{TEMP_SUFFIX}")
    try:
        with src.open("rb") as fin, tmp.open("wb") as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _remove_stale_temps(folder: Path) -> None:
    for entry in folder.rglob(f".*{TEMP_SUFFIX}"):
        entry.unlink(missing_ok=True)


def _visible_files(folder: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in folder.rglob("*"):
        rel = path.relative_to(folder)
        if any(part.startswith(".") for part in rel.parts) or not path.is_file():
            continue
        out[rel.as_posix()] = path
    return out


def _set_aside_stale(mirror: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = mirror.parent / SUPERSEDED_DIR / stamp
    n = 1
    while target.exists():
        target = mirror.parent / SUPERSEDED_DIR / f"{stamp}-{n}"
        n += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(mirror), str(target))
    log.warning("history mirror of another history set aside as %s", target)
    return target


def sync_mirror(project_root: Path, mirror: Path) -> bool:
    """Copy ``<project_root>/ascmhl/`` into ``mirror`` (see the module docstring). Hidden files
    (the writer's ``.<name>.tmp``, macOS ``._*``) are skipped. Returns True if anything was
    copied. Raises ``OSError`` if the history cannot be read or the mirror written."""
    with _LOCK:
        return _sync_locked(project_root, mirror)


def _sync_locked(project_root: Path, mirror: Path) -> bool:
    source = project_root / HISTORY_DIR
    if not source.is_dir():
        return False
    files = _visible_files(source)
    if mirror.is_dir() and set(_visible_files(mirror)) - set(files):
        _set_aside_stale(mirror)
    mirror.mkdir(parents=True, exist_ok=True)
    _remove_stale_temps(mirror)
    copied = False
    chain: Path | None = None
    for rel_posix, src in sorted(files.items()):
        rel = PurePosixPath(rel_posix)
        if rel_posix == CHAIN_FILE:
            chain = src
            continue
        dst = mirror / Path(*rel.parts)
        if dst.exists():
            continue  # manifests are immutable; only the chain changes
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_atomic(src, dst)
        copied = True
    if chain is not None:
        dst = mirror / CHAIN_FILE
        if _read_chain(mirror) != chain.read_bytes():
            _copy_atomic(chain, dst)
            copied = True
    return copied


def supersede_mirror(settings: Settings, rel_path: str, stamp: str) -> Path | None:
    """Accept as new version (D17, D50): move the mirrored history to
    ``ascmhl_superseded/<stamp>/`` next to it, with the stamp ``retire_history`` used on disk.
    Returns the new location, or ``None`` if there was no mirror."""
    with _LOCK:
        return _supersede_locked(settings, rel_path, stamp)


def _supersede_locked(settings: Settings, rel_path: str, stamp: str) -> Path | None:
    mirror = mirror_dir(settings, rel_path)
    if not mirror.exists():
        return None
    target = mirror.parent / SUPERSEDED_DIR / stamp
    n = 1
    while target.exists():
        target = mirror.parent / SUPERSEDED_DIR / f"{stamp}-{n}"
        n += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(mirror), str(target))
    return target


def _prune_empty_parents(folder: Path, stop: Path) -> None:
    while folder != stop and stop in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def remove_mirror(settings: Settings, rel_path: str) -> bool:
    """Retire (D60): delete the project's mirror (history and superseded histories). Only those
    two folders are removed, then the empty parents: a mirror of another project nested below
    (``project_depth`` changed) is left alone. Returns True if something was removed."""
    base = _project_base(settings, rel_path)
    removed = False
    with _LOCK:
        for name in (HISTORY_DIR, SUPERSEDED_DIR):
            path = base / name
            if path.exists():
                shutil.rmtree(path)
                removed = True
        _prune_empty_parents(base, settings.config_dir / MIRROR_ROOT)
    return removed


def move_mirror(settings: Settings, old_rel: str, new_rel: str) -> bool:
    """D62: the project folder was moved or renamed; its mirror follows. A stale mirror at the
    new path (no tracked project owns it) is replaced. Returns True if something moved."""
    with _LOCK:
        return _move_locked(settings, old_rel, new_rel)


def _move_locked(settings: Settings, old_rel: str, new_rel: str) -> bool:
    old_base, new_base = _project_base(settings, old_rel), _project_base(settings, new_rel)
    moved = False
    for name in (HISTORY_DIR, SUPERSEDED_DIR):
        src, dst = old_base / name, new_base / name
        if not src.exists():
            continue
        if dst.exists():
            log.warning("stale history mirror replaced at %s/%s", new_rel, name)
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved = True
    _prune_empty_parents(old_base, settings.config_dir / MIRROR_ROOT)
    return moved


def _add_tree(zf: zipfile.ZipFile, folder: Path, prefix: str) -> int:
    count = 0
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if not path.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        zf.write(path, f"{prefix}/{rel.as_posix()}")
        count += 1
    return count


def zip_history(settings: Settings, project: ProjectRow) -> bytes | None:
    """The project's history as an in-memory zip (D60: download before Retire).

    From the mirror (``<name>/ascmhl/...`` plus ``<name>/ascmhl_superseded/...``), or, if there is
    no mirror, from the ``ascmhl/`` folder on the archive. ``None`` if neither exists."""
    base = _project_base(settings, project.rel_path)
    sources: list[tuple[Path, str]] = []
    if (base / HISTORY_DIR).is_dir():
        sources.append((base / HISTORY_DIR, f"{project.name}/{HISTORY_DIR}"))
        if (base / SUPERSEDED_DIR).is_dir():
            sources.append((base / SUPERSEDED_DIR, f"{project.name}/{SUPERSEDED_DIR}"))
    else:
        on_disk = settings.archive_root / project.rel_path / HISTORY_DIR
        try:
            present = on_disk.is_dir()
        except OSError:
            present = False
        if present:
            sources.append((on_disk, f"{project.name}/{HISTORY_DIR}"))
    if not sources:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        count = sum(_add_tree(zf, folder, prefix) for folder, prefix in sources)
    return buffer.getvalue() if count else None
