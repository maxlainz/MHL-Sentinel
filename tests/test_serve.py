"""``mhl-sentinel serve``: one process, uvicorn + supervisor, clean SIGTERM (hito 2).

Works with the real GUI (``web/app.py`` providing ``/healthz``) or with the fallback app.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from mhl_sentinel.cli import main
from mhl_sentinel.db import Database


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def make_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    clip = archive / "2024" / "2024-01_CLIENTE-A_CAMPANA" / "01_MASTERS" / "master.mov"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"master" * 1000)
    return archive


def test_serve_once_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = make_archive(tmp_path)
    monkeypatch.setenv("MHLS_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("MHLS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MHLS_WORKING_HOURS__DAYS", "[]")
    result = CliRunner().invoke(main, ["serve", "--once-tick"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "gate_open=True" in result.output and "archive_reachable=True" in result.output
    with Database(tmp_path / "config" / "state.db") as db:
        assert [p.rel_path for p in db.list_projects()] == ["2024/2024-01_CLIENTE-A_CAMPANA"]


def get_json(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return int(resp.status), json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_serve_healthz_and_clean_sigterm(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    port = free_port()
    env = {
        **os.environ,
        "TZ": "UTC",
        "MHLS_ARCHIVE_ROOT": str(archive),
        "MHLS_CONFIG_DIR": str(tmp_path / "config"),
        "MHLS_PORT": str(port),
        "MHLS_WORKING_HOURS__DAYS": "[]",
        "MHLS_LOG_LEVEL": "info",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "mhl_sentinel.cli", "serve", "--host", "127.0.0.1"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        status: int = 0
        body: dict[str, object] = {}
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                status, body = get_json(f"http://127.0.0.1:{port}/healthz")
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                status = 0
            if status == 200:
                break
            time.sleep(0.1)
        assert status == 200, (status, body)
        assert "status" in body
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
        assert time.monotonic() - t0 < 15
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0, out
    assert "supervisor stopped" in out
