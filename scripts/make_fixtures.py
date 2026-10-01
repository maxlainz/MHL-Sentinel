"""Genera un archivo sintético y determinista para los tests (D18, D19, D39).

Solo nomenclatura de plantilla; ningún nombre real (norma repo-publico.md).
Uso: uv run python scripts/make_fixtures.py [--out PATH] [--seed N] [--size small|medium]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import xxhash

KB: Final = 1024
MB: Final = 1024 * 1024
BASE_TIME: Final = datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC)

README_TEXT: Final = (
    "# {name}\n\nProyecto sintético de plantilla para tests de MHL Sentinel.\nSin datos reales.\n"
)


@dataclass
class Builder:
    root: Path
    rng: random.Random
    scale: int
    counter: int = 0
    files: int = 0
    total_bytes: int = 0
    projects: int = 0
    written: list[Path] = field(default_factory=list)

    def _stamp(self, path: Path) -> None:
        ts = (BASE_TIME + timedelta(minutes=self.counter)).timestamp()
        self.counter += 1
        os.utime(path, (ts, ts))

    def write(self, rel: str, data: bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._stamp(path)
        self.files += 1
        self.total_bytes += len(data)
        self.written.append(path)
        return path

    def random_file(self, rel: str, size: int | None = None) -> bytes:
        if size is None:
            size = self.rng.randint(1 * KB, 2 * MB // 4) * self.scale
        data = self.rng.randbytes(size)
        self.write(rel, data)
        return data

    def readme(self, project_rel: str) -> None:
        name = project_rel.rsplit("/", 1)[-1]
        self.write(f"{project_rel}/00_README.md", README_TEXT.format(name=name).encode())


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def mhl1_xml(entries: list[tuple[str, bytes]], algo: str, when: datetime) -> bytes:
    """MHL 1.0 mínimo: <hashlist version="1.0"> con creatorinfo y un <hash> por fichero."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<hashlist version="1.0">',
        "  <creatorinfo>",
        "    <name>Template User</name>",
        "    <username>template</username>",
        "    <hostname>template-host</hostname>",
        "    <tool>synthetic-offload 1.0</tool>",
        f"    <startdate>{iso(when)}</startdate>",
        f"    <finishdate>{iso(when + timedelta(minutes=5))}</finishdate>",
        "  </creatorinfo>",
    ]
    for name, data in entries:
        digest = (
            xxhash.xxh64(data).hexdigest()
            if algo == "xxhash64be"
            else hashlib.md5(data).hexdigest()
        )
        lines += [
            "  <hash>",
            f"    <file>{name}</file>",
            f"    <size>{len(data)}</size>",
            f"    <lastmodificationdate>{iso(when)}</lastmodificationdate>",
            f"    <{algo}>{digest}</{algo}>",
            f"    <hashdate>{iso(when + timedelta(minutes=1))}</hashdate>",
            "  </hash>",
        ]
    lines.append("</hashlist>")
    return ("\n".join(lines) + "\n").encode()


def build(out: Path, seed: int, size: str) -> tuple[int, int, int]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    b = Builder(root=out, rng=random.Random(seed), scale=10 if size == "medium" else 1)

    # 2024-03: spot típico
    p = "2024/2024-03_CLIENTE-A_CAMPANA-UNO"
    b.projects += 1
    b.readme(p)
    for i in range(1, 4):
        b.random_file(f"{p}/01_MASTERS/CLIENTE-A_CAMPANA-UNO_v{i}.mov")
    for i in range(1, 3):
        b.random_file(f"{p}/03_GRADE/grade_{i:02d}.drx")
    for i in range(1, 3):
        b.random_file(f"{p}/05_DELIVERABLES/deliverable_{i:02d}.mp4")
    b.write(f"{p}/.DS_Store", b"\x00\x00\x00\x01Bud1")
    b.random_file(f"{p}/._junk.mov", 4 * KB)

    # 2024-11: MHL 1.x heredados (D39)
    p = "2024/2024-11_CLIENTE-B_CAMPANA-DOS"
    b.projects += 1
    b.readme(p)
    when = datetime(2024, 11, 2, 10, 15, 0, tzinfo=UTC)
    ocf_a = [
        (f"A001C{i:03d}_241102_R2EC.mov", b.rng.randbytes(b.rng.randint(KB, 256 * KB) * b.scale))
        for i in range(1, 5)
    ]
    for name, data in ocf_a:
        b.write(f"{p}/02_OCF/A001R2EC/{name}", data)
    b.write(
        f"{p}/02_OCF/A001R2EC/A001R2EC_2024-11-02_101500.mhl",
        mhl1_xml(ocf_a, "xxhash64be", when),
    )
    ocf_b = [
        (f"B001C{i:03d}_241103_R2EC.mov", b.rng.randbytes(b.rng.randint(KB, 128 * KB) * b.scale))
        for i in range(1, 3)
    ]
    for name, data in ocf_b:
        b.write(f"{p}/02_OCF/B001/{name}", data)
    b.write(f"{p}/02_OCF/B001/B001_2024-11-03_090000.mhl", mhl1_xml(ocf_b, "md5", when))
    for i in range(1, 3):
        b.random_file(f"{p}/01_MASTERS/CLIENTE-B_CAMPANA-DOS_v{i}.mov")

    # 2025-01: largometraje con secuencia de frames
    p = "2025/2025-01_CLIENTE-C_LARGO"
    b.projects += 1
    b.readme(p)
    for i in range(1, 40 * b.scale + 1):
        b.random_file(f"{p}/04_VFX/seq_0001/frame_{i:04d}.exr", b.rng.randint(KB, 4 * KB))
    b.random_file(f"{p}/01_MASTERS/CLIENTE-C_LARGO_master.mov", 2 * MB * b.scale)

    # 2025-06: caso límite, solo README
    p = "2025/2025-06_CLIENTE-D_CAMPANA-TRES"
    b.projects += 1
    b.readme(p)

    # Proyecto directamente bajo la raíz (profundidad 1)
    p = "SIN-CATEGORIA_CLIENTE-E"
    b.projects += 1
    b.readme(p)
    b.random_file(f"{p}/01_MASTERS/CLIENTE-E_master.mov")

    # Ignorados por D19 (_, @, #, .)
    b.random_file("_RESOURCES/logo_template.png")
    b.random_file("_RESOURCES/music_template.wav")
    b.random_file("@Recycle/deleted_template.mov")
    b.random_file("#snapshot/snap_template.bin")
    b.write(".DS_Store", b"\x00\x00\x00\x01Bud1")

    return b.projects, b.files, b.total_bytes


def default_out() -> Path:
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "archive"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=default_out())
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--size", choices=("small", "medium"), default="small")
    args = ap.parse_args(argv)
    projects, files, nbytes = build(args.out, args.seed, args.size)
    print(f"fixtures: {projects} projects, {files} files, {nbytes} bytes -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
