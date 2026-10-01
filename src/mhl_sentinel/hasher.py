"""Sequential, pausable file hasher (D28, D34).

One reader at a time (D34). A ``Gate`` models the working-hours window: when it
is closed the current file is closed and, once reopened, re-read from the start
(the simplest honest resume). ``stop`` aborts. A file whose size or mtime
changed while being read raises ``FileChanged`` (it was not quiescent).
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import xxhash

from mhl_sentinel.models import FileStat

MAX_RETRIES = 3


class Gate(Protocol):
    """Open (set) = reading allowed. A ``threading.Event`` satisfies it."""

    def wait(self, timeout: float | None = None) -> bool: ...

    def is_set(self) -> bool: ...


class FileChanged(Exception):
    """The file changed (size or mtime) while it was being hashed."""


class Stopped(Exception):
    """Hashing was cancelled through the stop event."""


@dataclass(frozen=True, slots=True)
class HashedFile:
    path: Path
    size: int
    mtime_ns: int
    digests: dict[str, str]


class HashCache(Protocol):
    def get(self, rel_path: str, size: int, mtime_ns: int) -> dict[str, str]: ...

    def put(self, rel_path: str, size: int, mtime_ns: int, fmt: str, digest: str) -> None: ...


_FACTORIES: dict[str, Callable[[], Any]] = {
    "xxh128": xxhash.xxh128,
    "xxh64": xxhash.xxh64,
    "xxh3": xxhash.xxh3_64,
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
}


def _wait_open(gate: Gate, stop: threading.Event) -> None:
    while not gate.is_set():
        if stop.is_set():
            raise Stopped
        gate.wait(1.0)


def hash_file(
    path: Path,
    formats: Collection[str],
    *,
    gate: Gate,
    stop: threading.Event,
    chunk_size: int = 8 * 1024 * 1024,
) -> HashedFile:
    unknown = [f for f in formats if f not in _FACTORIES]
    if unknown:
        raise ValueError(f"unsupported hash formats: {unknown}")
    while True:
        before = path.stat()
        hashers = {fmt: _FACTORIES[fmt]() for fmt in formats}
        paused = False
        with path.open("rb") as fh:
            while True:
                if stop.is_set():
                    raise Stopped
                if not gate.is_set():
                    paused = True
                    break
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                for h in hashers.values():
                    h.update(chunk)
        if paused:
            _wait_open(gate, stop)
            continue
        after = path.stat()
        if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            raise FileChanged(str(path))
        return HashedFile(
            path=path,
            size=before.st_size,
            mtime_ns=before.st_mtime_ns,
            digests={fmt: h.hexdigest().lower() for fmt, h in hashers.items()},
        )


def hash_project(
    project_root: Path,
    files: Sequence[FileStat],
    formats_for: Callable[[str], Collection[str]],
    cache: HashCache,
    *,
    gate: Gate,
    stop: threading.Event,
    progress: Callable[[int, int, int, int], None] | None = None,
) -> dict[str, dict[str, str]]:
    """Hash every file for the formats ``formats_for(rel_path)`` wants, reusing the cache."""
    result: dict[str, dict[str, str]] = {}
    files_total = len(files)
    bytes_total = sum(f.size for f in files)
    bytes_done = 0
    for index, fs in enumerate(files):
        wanted = set(formats_for(fs.rel_path))
        cached = cache.get(fs.rel_path, fs.size, fs.mtime_ns)
        digests = {fmt: d for fmt, d in cached.items() if fmt in wanted}
        missing = wanted - digests.keys()
        if missing:
            hashed = _hash_with_retries(project_root / fs.rel_path, missing, gate, stop)
            for fmt, digest in hashed.digests.items():
                cache.put(
                    hashed.path.relative_to(project_root).as_posix(),
                    hashed.size,
                    hashed.mtime_ns,
                    fmt,
                    digest,
                )
            digests.update(hashed.digests)
        result[fs.rel_path] = digests
        bytes_done += fs.size
        if progress is not None:
            progress(index + 1, files_total, bytes_done, bytes_total)
    return result


def _hash_with_retries(
    path: Path, formats: Collection[str], gate: Gate, stop: threading.Event
) -> HashedFile:
    for attempt in range(MAX_RETRIES):
        try:
            return hash_file(path, formats, gate=gate, stop=stop)
        except FileChanged:
            if attempt == MAX_RETRIES - 1:
                raise
    raise AssertionError("unreachable")  # pragma: no cover
