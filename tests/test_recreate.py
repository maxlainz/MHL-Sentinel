"""Abrupt container recreation (auto-updates that pull ``latest`` and recreate the container).

``serve`` is SIGKILLed while a seal is hashing; a new ``serve`` on the same ``/config`` (state.db)
and archive must requeue the job, finish it with exactly one generation that the reference
verifies, leave no temp files, and stop cleanly on SIGTERM within the 10 s a runtime allows.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from helpers_ascmhl import run_cli

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_fixtures.py"
BIG = "2024/2024-02_CLIENTE-F_MUCHOS"


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_fixtures_recreate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def build_archive(archive: Path, n_files: int) -> Path:
    generator().build(archive, 1, "small")
    big = archive / BIG
    for i in range(n_files):
        folder = big / "02_OCF" / f"A{i // 100:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"clip_{i:05d}.bin").write_bytes(i.to_bytes(4, "big") * 8192)  # 32 KiB
    return big


class Serve:
    """One ``mhl-sentinel serve`` process = one container lifetime."""

    def __init__(self, tmp_path: Path, archive: Path, port: int, name: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.log_path = tmp_path / f"{name}.log"
        env = {
            **os.environ,
            "TZ": "UTC",
            "MHLS_ARCHIVE_ROOT": str(archive),
            "MHLS_CONFIG_DIR": str(tmp_path / "config"),
            "MHLS_PORT": str(port),
            "MHLS_WORKING_HOURS__DAYS": "[]",
            "MHLS_LOG_LEVEL": "info",
        }
        self._log = self.log_path.open("w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mhl_sentinel.cli", "serve", "--host", "127.0.0.1"],
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )

    def get(self, path: str) -> dict[str, Any]:
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            body: dict[str, Any] = json.loads(resp.read())
            return body

    def post(self, path: str) -> int:
        req = urllib.request.Request(self.base + path, data=b"", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            return exc.code

    def wait_for(self, pred: Callable[[], bool], timeout: float, poll: float = 0.05) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert self.proc.poll() is None, self.output()
            try:
                if pred():
                    return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(poll)
        raise AssertionError(f"condition not reached in {timeout} s\n{self.output()}")

    def project(self, rel_path: str) -> dict[str, Any] | None:
        projects: list[dict[str, Any]] = self.get("/api/projects")["projects"]
        return next((p for p in projects if p["rel_path"] == rel_path), None)

    def output(self) -> str:
        self._log.flush()
        return self.log_path.read_text()

    def finish(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self._log.close()


def kill_while_hashing(tmp_path: Path, archive: Path, big: Path) -> bool:
    """Seal ``BIG`` and SIGKILL as soon as the job runs. False if it finished before the kill."""
    first = Serve(tmp_path, archive, free_port(), "first")
    try:
        first.wait_for(lambda: first.project(BIG) is not None, timeout=30)
        project = first.project(BIG)
        assert project is not None
        assert first.post(f"/projects/{project['id']}/seal") in (200, 303)

        def running() -> bool:
            job = first.get("/api/status")["current_job"]
            return job is not None and job["state"] == "running" and job["files_done"] > 0

        first.wait_for(running, timeout=30, poll=0.01)
        first.proc.send_signal(signal.SIGKILL)
        first.proc.wait(timeout=10)
    finally:
        first.finish()
    return not list((big / "ascmhl").glob("*.mhl"))


def test_sigkill_mid_seal_then_recreated_container_finishes_it(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    for n_files in (300, 3000):  # more files if the seal finished before the SIGKILL
        big = build_archive(archive, n_files)
        if kill_while_hashing(tmp_path, archive, big):
            break
        (tmp_path / "config" / "state.db").unlink()
        for extra in ("state.db-wal", "state.db-shm"):
            (tmp_path / "config" / extra).unlink(missing_ok=True)
    else:
        raise AssertionError("the seal always finished before the SIGKILL")
    assert not (big / "ascmhl").exists() or not any((big / "ascmhl").iterdir())

    t_start = time.monotonic()
    second = Serve(tmp_path, archive, free_port(), "second")
    try:
        second.wait_for(lambda: second.project(BIG) is not None, timeout=30)

        def sealed() -> bool:
            project = second.project(BIG)
            status = second.get("/api/status")
            return (
                project is not None
                and project["state"] == "sealed"
                and status["current_job"] is None
                and status["queued_jobs"] == 0
            )

        second.wait_for(sealed, timeout=120)
        recovered_in = time.monotonic() - t_start
        project = second.project(BIG)
        assert project is not None and project["last_generation_no"] == 1
        assert project["file_count"] == n_files
        assert "1 interrupted job(s) back in the queue" in second.output()

        t0 = time.monotonic()
        second.proc.send_signal(signal.SIGTERM)
        second.proc.wait(timeout=10)
        stopped_in = time.monotonic() - t0
        assert second.proc.returncode == 0, second.output()
        assert "supervisor stopped" in second.output()
    finally:
        second.finish()
    print(f"\nfiles={n_files} recovered_in={recovered_in:.1f}s sigterm_exit={stopped_in:.2f}s")

    asc = big / "ascmhl"
    assert len(list(asc.glob("*.mhl"))) == 1
    assert not [p.name for p in asc.iterdir() if p.name.endswith((".tmp", ".orphan"))]
    verify = run_cli("ascmhl-debug", "verify", big)
    assert verify.returncode == 0, verify.stdout + verify.stderr
