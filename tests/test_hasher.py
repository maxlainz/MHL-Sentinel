"""Hasher (D28, D34): pause/resume, stop, change detection, cache."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

import pytest
import xxhash

from mhl_sentinel.hasher import FileChanged, Stopped, hash_file, hash_project
from mhl_sentinel.models import FileStat

DATA = bytes(range(256)) * 40  # 10240 bytes


def make(tmp_path: Path, name: str = "a.bin", data: bytes = DATA) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def open_gate() -> threading.Event:
    g = threading.Event()
    g.set()
    return g


def test_digests(tmp_path: Path) -> None:
    p = make(tmp_path)
    fmts = ["xxh128", "xxh64", "xxh3", "md5", "sha1"]
    h = hash_file(p, fmts, gate=open_gate(), stop=threading.Event(), chunk_size=1000)
    assert h.size == len(DATA)
    assert h.digests == {
        "xxh128": xxhash.xxh128(DATA).hexdigest(),
        "xxh64": xxhash.xxh64(DATA).hexdigest(),
        "xxh3": xxhash.xxh3_64(DATA).hexdigest(),
        "md5": hashlib.md5(DATA).hexdigest(),
        "sha1": hashlib.sha1(DATA).hexdigest(),
    }


def test_stop(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    with pytest.raises(Stopped):
        hash_file(make(tmp_path), ["md5"], gate=open_gate(), stop=stop)


def test_pause_and_resume(tmp_path: Path) -> None:
    p = make(tmp_path)
    gate = threading.Event()  # closed
    out: dict[str, Any] = {}

    def run() -> None:
        out["h"] = hash_file(p, ["xxh128"], gate=gate, stop=threading.Event(), chunk_size=100)

    t = threading.Thread(target=run)
    t.start()
    t.join(0.3)
    assert t.is_alive()  # paused: gate closed
    gate.set()
    t.join(5)
    assert not t.is_alive()
    assert out["h"].digests["xxh128"] == xxhash.xxh128(DATA).hexdigest()


def test_pause_mid_file_restarts(tmp_path: Path) -> None:
    p = make(tmp_path)
    gate = threading.Event()
    gate.set()
    reads = 0

    class Closing(threading.Event):
        pass

    calls = {"n": 0}

    class FlakyGate:
        def is_set(self) -> bool:
            calls["n"] += 1
            return calls["n"] != 4  # close once in the middle of the file

        def wait(self, timeout: float | None = None) -> bool:
            return True

    h = hash_file(p, ["md5"], gate=FlakyGate(), stop=threading.Event(), chunk_size=1000)
    assert h.digests["md5"] == hashlib.md5(DATA).hexdigest()
    assert reads == 0 and calls["n"] > 11  # the file was re-read after the pause


def test_stop_while_paused(tmp_path: Path) -> None:
    p = make(tmp_path)
    gate = threading.Event()
    stop = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            hash_file(p, ["md5"], gate=gate, stop=stop)
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    stop.set()
    t.join(5)
    assert not t.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], Stopped)


class AppendingGate:
    """Modifies the file between chunks, as a copy still in progress would."""

    def __init__(self, path: Path, times: int, alternate: bool = False) -> None:
        self.path, self.times, self.alternate, self.calls = path, times, alternate, 0

    def is_set(self) -> bool:
        self.calls += 1
        if self.times > 0 and not (self.alternate and self.calls % 2 == 0):
            self.times -= 1
            with self.path.open("ab") as fh:
                fh.write(b"more")
        return True

    def wait(self, timeout: float | None = None) -> bool:
        return True


def test_file_changed(tmp_path: Path) -> None:
    p = make(tmp_path)
    with pytest.raises(FileChanged):
        hash_file(p, ["md5"], gate=AppendingGate(p, 1), stop=threading.Event(), chunk_size=1000)


class CountingCache:
    def __init__(self) -> None:
        self.store: dict[tuple[str, int, int], dict[str, str]] = {}

    def get(self, rel_path: str, size: int, mtime_ns: int) -> dict[str, str]:
        return dict(self.store.get((rel_path, size, mtime_ns), {}))

    def put(self, rel_path: str, size: int, mtime_ns: int, fmt: str, digest: str) -> None:
        self.store.setdefault((rel_path, size, mtime_ns), {})[fmt] = digest


def stat_of(root: Path, rel: str) -> FileStat:
    st = (root / rel).stat()
    return FileStat(rel, st.st_size, st.st_mtime_ns)


def test_hash_project_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make(tmp_path, "a.bin")
    make(tmp_path, "b.bin", b"other" * 100)
    files = [stat_of(tmp_path, "a.bin"), stat_of(tmp_path, "b.bin")]
    cache = CountingCache()
    gate, stop = open_gate(), threading.Event()
    progress: list[tuple[int, int, int, int]] = []

    opened: list[str] = []
    real_open = Path.open

    def counting_open(self: Path, *a: Any, **k: Any) -> Any:
        opened.append(self.name)
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", counting_open)

    r1 = hash_project(
        tmp_path,
        files,
        lambda _p: ["xxh128"],
        cache,
        gate=gate,
        stop=stop,
        progress=lambda *a: progress.append(a),
    )
    assert opened == ["a.bin", "b.bin"]
    assert r1["a.bin"] == {"xxh128": xxhash.xxh128(DATA).hexdigest()}
    assert progress[-1] == (2, 2, files[0].size + files[1].size, files[0].size + files[1].size)

    opened.clear()
    r2 = hash_project(tmp_path, files, lambda _p: ["xxh128"], cache, gate=gate, stop=stop)
    assert opened == [] and r2 == r1  # full cache hit: nothing read

    # a new format: only the missing one is computed, in one read; extras filtered out
    r3 = hash_project(tmp_path, files, lambda _p: ["xxh128", "md5"], cache, gate=gate, stop=stop)
    assert opened == ["a.bin", "b.bin"]
    assert r3["a.bin"]["md5"] == hashlib.md5(DATA).hexdigest()
    r4 = hash_project(tmp_path, files, lambda _p: ["md5"], cache, gate=gate, stop=stop)
    assert set(r4["a.bin"]) == {"md5"}


def test_hash_project_retries_then_raises(tmp_path: Path) -> None:
    p = make(tmp_path)
    files = [stat_of(tmp_path, "a.bin")]
    cache = CountingCache()
    # Always modifying: every attempt fails, the 3rd re-raises.
    gate = AppendingGate(p, 1000, alternate=True)  # one change per read attempt
    with pytest.raises(FileChanged):
        hash_project(tmp_path, files, lambda _p: ["md5"], cache, gate=gate, stop=threading.Event())
    assert gate.times == 1000 - 3  # exactly 3 attempts


def test_wait_open_stops_when_the_gate_never_opens(tmp_path: Path) -> None:
    from mhl_sentinel.hasher import _wait_open

    stop = threading.Event()
    stop.set()
    with pytest.raises(Stopped):
        _wait_open(threading.Event(), stop)  # closed gate + stop: abort, do not wait 1 s
    gate = threading.Event()
    gate.set()
    _wait_open(gate, threading.Event())  # open gate returns at once


def test_unsupported_format_is_rejected_before_reading(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported hash formats"):
        hash_file(
            tmp_path / "missing.bin", ["md5", "crc32"], gate=open_gate(), stop=threading.Event()
        )
