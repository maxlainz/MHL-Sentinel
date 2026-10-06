"""D75: the "What's archived" panel and the per-generation change counts. The fixtures are those
of ``test_web.py``."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import xxhash

import test_web
from mhl_sentinel.db import SealedFile
from mhl_sentinel.mhlwriter import write_project_generation
from mhl_sentinel.web import views
from test_web import NOW, Env

env = test_web.env
MADRID = ZoneInfo("Europe/Madrid")


def seal_rows(env: Env, rows: list[SealedFile]) -> Any:
    pid = env.ids["sealed"]
    env.db.replace_sealed_files(pid, rows)
    found = env.db.get_project(pid)
    assert found is not None
    return found


def panel(env: Env, rows: list[SealedFile]) -> views.ArchivePanel:
    project = seal_rows(env, rows)
    result = views.archive_panel(env.db, project, env.ref.value, NOW)
    assert result is not None
    return result


def test_panel_groups_folders_root_files_and_skips_ascmhl(env: Env) -> None:
    result = panel(
        env,
        [
            SealedFile("01_MASTERS/a.mov", 3_000, 1, "a" * 32),
            SealedFile("01_MASTERS/sub/b.MOV", 1_000, 1, "b" * 32),
            SealedFile("02_AUDIO/c.wav", 2_000, 1, None),
            SealedFile("notes", 10, 1, "c" * 8),
            SealedFile("ascmhl/0001_x.mhl", 99_999, 1, None),
        ],
    )
    assert result.summary.startswith("4 files · 6.0 KB · sealed 2026-10-02")
    assert [(f.name, f.files, f.size, f.pct) for f in result.folders] == [
        (views.ROOT_FOLDER, 1, "10 B", 0),
        ("01_MASTERS", 2, "4.0 KB", 100),
        ("02_AUDIO", 1, "2.0 KB", 50),
    ]
    assert "ascmhl" not in " ".join(f.path for f in result.files)
    assert result.types == ".mov 2 · .wav 1 · no extension 1"
    by_path = {f.path: f for f in result.files}
    assert by_path["01_MASTERS/a.mov"].hash_short == "aaaa…aaaa"
    assert by_path["01_MASTERS/a.mov"].hash_full == "a" * 32
    assert by_path["02_AUDIO/c.wav"].hash_short == "-"
    assert by_path["notes"].hash_short == "cccccccc"


def test_panel_types_show_top_eight_and_count_the_others(env: Env) -> None:
    rows = [SealedFile(f"f/{n}.e{n}", 0, 1, None) for n in range(10)]
    rows.append(SealedFile("f/extra.e0", 0, 1, None))
    result = panel(env, rows)
    assert result.types.startswith(".e0 2 · .e1 1")
    assert result.types.endswith(" · 2 others")
    assert [f.pct for f in result.folders] == [0]  # all sizes zero: no division by zero
    assert result.summary.startswith("11 files · 0 B")


def test_panel_verification_lines(env: Env) -> None:
    rows = [SealedFile("a.mov", 1, 1, None)]
    result = panel(env, rows)
    assert result.verified == "Last verified 2026-10-02 · next check around 2026-12-30"
    env.db.update_project_fields(env.ids["sealed"], last_verified_at=None)
    project = seal_rows(env, rows)
    settings = env.ref.value
    first = views.archive_panel(env.db, project, settings, NOW)
    assert first is not None
    assert first.verified == "Not verified yet · first check around 2026-12-30"
    late = views.archive_panel(env.db, project, settings, NOW + timedelta(days=200))
    assert late is not None
    assert late.verified == "Not verified yet · first check is due now"
    env.db.update_project_fields(env.ids["sealed"], last_verified_at=NOW - timedelta(days=200))
    project = seal_rows(env, rows)
    old = views.archive_panel(env.db, project, settings, NOW)
    assert old is not None
    assert old.verified.endswith("next check is due now")


def test_next_check_without_any_date(env: Env) -> None:
    env.db.update_project_fields(env.ids["sealed"], last_verified_at=None, last_sealed_at=None)
    project = seal_rows(env, [SealedFile("a.mov", 1, 1, None)])
    assert views.archive_next_check(project, env.ref.value, NOW) == ""
    result = views.archive_panel(env.db, project, env.ref.value, NOW)
    assert result is not None
    assert result.verified == "Not verified yet"
    assert " sealed " not in result.summary


def test_panel_is_omitted_without_sealed_files(env: Env) -> None:
    project = seal_rows(env, [])
    assert views.archive_panel(env.db, project, env.ref.value, NOW) is None
    only_ascmhl = seal_rows(env, [SealedFile("ascmhl/0001.mhl", 5, 1, None)])
    assert views.archive_panel(env.db, only_ascmhl, env.ref.value, NOW) is None
    assert "What's archived" not in env.client.get(f"/projects/{env.ids['sealed']}").text


def test_detail_page_renders_the_panel(env: Env) -> None:
    seal_rows(
        env,
        [
            SealedFile("01_MASTERS/a.mov", 3_000, 1, "3f9a" + "0" * 24 + "c21e"),
            SealedFile("b.txt", 5, 1, None),
        ],
    )
    html = env.client.get(f"/projects/{env.ids['sealed']}").text
    assert "What's archived" in html
    assert "2 files · 3.0 KB" in html
    assert "(project root)" in html and "01_MASTERS" in html
    assert 'class="bar"' in html and "width: 100%" in html
    assert "Types: .mov 1 · .txt 1" in html
    assert "Show all 2 files" in html
    assert 'data-filter="archived-files"' in html
    assert 'title="3f9a' in html and "3f9a…c21e" in html
    assert html.index("What's archived") < html.index("Manifest history")


def xxh(path: Path) -> dict[str, str]:
    return {"xxh128": xxhash.xxh128(path.read_bytes()).hexdigest()}


def test_generations_say_what_each_one_brings(env: Env) -> None:
    project = env.archive / "2025" / "2025-01_CLIENTE-SELLADO"
    project.mkdir(parents=True)
    for name in ("a.mov", "b.mov"):
        (project / name).write_bytes(name.encode() * 10)
    write_project_generation(project, {n: xxh(project / n) for n in ("a.mov", "b.mov")})
    (project / "c.mov").write_bytes(b"c" * 10)
    write_project_generation(project, {"c.mov": xxh(project / "c.mov")}, partial=True)  # append
    write_project_generation(project, {n: xxh(project / n) for n in ("a.mov", "b.mov", "c.mov")})
    (project / "d.mov").write_bytes(b"d" * 10)
    write_project_generation(
        project, {n: xxh(project / n) for n in ("a.mov", "b.mov", "c.mov", "d.mov")}
    )

    gens = views.load_history(project, MADRID).generations
    assert [(g.number, g.files, g.changes) for g in gens] == [
        (1, 2, ""),
        (2, 1, "+1 new"),  # an append generation holds only the new file
        (3, 3, "no changes"),
        (4, 4, "+1 new"),
    ]
    html = env.client.get(f"/projects/{env.ids['sealed']}").text
    assert '<span class="changes-note">+1 new</span>' in html
    assert "no changes" in html


def entry(path: str | None, hashes: dict[str, str], *, directory: bool = False) -> Any:
    entries = [SimpleNamespace(hash_format=f, hash_string=h) for f, h in hashes.items()]
    return SimpleNamespace(path=path, is_directory=directory, hash_entries=entries)


def generation(number: int, *media: Any) -> Any:
    return SimpleNamespace(
        generation_number=number,
        creator_info=SimpleNamespace(creation_date=None, tool=SimpleNamespace(name="ascmhl")),
        media_hashes=list(media),
    )


def test_generation_diff_counts_modified_and_removed(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The app's writer never produces these (a modification is a review case, D9; a missing file
    # aborts the write), but a manifest written by another tool can.
    root = tmp_path / "p"
    (root / "ascmhl").mkdir(parents=True)
    root_dir = entry(".", {}, directory=True)
    hash_lists = [
        generation(1, entry("a", {"xxh128": "1"}), entry("b", {"xxh128": "2"}), root_dir),
        generation(
            2, entry("a", {"xxh128": "9", "md5": "x"}), entry("c", {"xxh128": "3"})
        ),  # append
        generation(
            3,
            entry("a", {"xxh128": "9"}),
            entry("b", {"xxh128": "5"}),
            entry("d", {"xxh128": "4"}),
            root_dir,
        ),
        generation(4, entry("a", {"xxh128": "9"}), root_dir),
    ]
    fake = SimpleNamespace(hash_lists=hash_lists)
    monkeypatch.setattr("ascmhl.history.MHLHistory.load_from_path", lambda _p: fake)
    gens = views.load_history(root, MADRID).generations
    assert [g.changes for g in gens] == [
        "",
        "+1 new · 1 modified",  # a changed, c new; b untouched by a partial generation
        "+1 new · 1 modified · 1 removed",  # d new, b changed, c gone from the full listing
        "2 removed",  # b and d dropped
    ]
