"""Helpers to run the reference implementation (``ascmhl``) as real CLI processes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
XSD_DIR = REPO_ROOT / "tests" / "xsd"

# The CLI checks GitHub for updates on every run (research spec §2.1) and has
# no switch to disable it. A dead proxy makes that request fail at once; the
# Updater swallows the error. TZ=UTC: dates are written with the current
# offset (research spec §2.7).
CLI_ENV = {
    **os.environ,
    "TZ": "UTC",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "HTTP_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "",
    "no_proxy": "",
}


def run_cli(tool: str, *args: str | Path) -> subprocess.CompletedProcess[str]:
    if shutil.which("uv"):
        cmd = ["uv", "run", "--project", str(REPO_ROOT), "--no-sync", tool]
    else:
        cmd = [str(Path(sys.executable).parent / tool)]
    return subprocess.run(
        [*cmd, *map(str, args)],
        env=CLI_ENV,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def assert_xsd_valid(manifest: Path) -> None:
    result = run_cli("ascmhl-debug", "xsd-schema-check", manifest, "-xsd", XSD_DIR / "ASCMHL.xsd")
    assert result.returncode == 0, result.stdout + result.stderr


def assert_chain_xsd_valid(chain: Path) -> None:
    # The combined XSD imports ASCMHL.xsd locally first, so the remote
    # schemaLocation inside ASCMHLDirectory.xsd is never fetched.
    xsd = XSD_DIR / "ASCMHLDirectory__combined.xsd"
    result = run_cli("ascmhl-debug", "xsd-schema-check", "-df", chain, "-xsd", xsd)
    assert result.returncode == 0, result.stdout + result.stderr
