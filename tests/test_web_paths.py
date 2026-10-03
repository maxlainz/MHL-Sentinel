"""Less-travelled paths of ``web/``: presentation helpers, the settings form and the routes'
error branches. The fixtures are those of ``test_web.py``."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import test_web
from mhl_sentinel import sealer
from mhl_sentinel.config import Settings
from mhl_sentinel.db import JobRow, ReviewItem, SealedFile
from mhl_sentinel.models import ChangeKind, FileStat, JobKind, JobState, ProjectState, Trigger
from mhl_sentinel.web import routes, settings_form, views
from test_web import HX, NOW, Env, FakeBus, FakeStatus

env = test_web.env  # the fixture of test_web.py, shared
MADRID = ZoneInfo("Europe/Madrid")
GB = 1_000_000_000


def project(env: Env, key: str) -> Any:
    found = env.db.get_project(env.ids[key])
    assert found is not None
    return found


# --- views: dates, sizes, jobs ----------------------------------------------------------------


def test_human_size_none_and_units() -> None:
    assert views.human_size(None) == "-"
    assert views.human_size(999) == "999 B"
    assert views.human_size(1_500) == "1.5 KB"
    assert views.human_size(2 * 1000**5) == "2.0 PB"
    assert views.human_size(3000 * 1000**5) == "3000.0 PB"  # no unit above PB


def test_date_helpers_accept_strings_naive_datetimes_and_garbage() -> None:
    assert views.local("not a date", MADRID) is None
    assert views.local("", MADRID) is None
    assert views.fmt_date(None, MADRID) == ""
    assert views.fmt_datetime(None, MADRID) == ""
    naive = datetime(2026, 10, 1, 22, 30)  # read as UTC, shown at +02:00
    assert views.fmt_datetime(naive, MADRID) == "2026-10-02 00:30"
    assert views.fmt_date(naive, MADRID) == "2026-10-02"
    assert views.fmt_mtime_ns(None, MADRID) == ""
    assert views.fmt_mtime_ns(1_790_000_000_000_000_000, MADRID) == "2026-09-21"


def test_fmt_when_today_this_week_and_later() -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert views.fmt_when(None, MADRID, now) == ""
    assert views.fmt_when(datetime(2026, 10, 1, 12, 2, tzinfo=UTC), MADRID, now) == "14:02"
    assert views.fmt_when(datetime(2026, 10, 3, 7, 0, tzinfo=UTC), MADRID, now) == "Sat 09:00"
    assert (
        views.fmt_when(datetime(2026, 11, 3, 7, 0, tzinfo=UTC), MADRID, now) == "2026-11-03 08:00"
    )


def make_job(**over: Any) -> JobRow:
    base: dict[str, Any] = {
        "id": 1, "kind": JobKind.SEAL, "project_id": 1, "trigger": Trigger.AUTO,
        "state": JobState.RUNNING, "priority": 10, "created_at": "2026-10-01T22:00:00.000000Z",
        "started_at": None, "finished_at": None, "files_total": 0, "files_done": 0,
        "bytes_total": 0, "bytes_done": 0, "error": None, "bypass_hours": False,
    }  # fmt: skip
    return JobRow(**{**base, **over})


def test_percent_prefers_bytes_then_files_then_zero() -> None:
    assert views.percent(None) is None
    assert views.percent(make_job(bytes_total=200, bytes_done=50, files_total=4)) == 25
    assert views.percent(make_job(files_total=4, files_done=1)) == 25
    assert views.percent(make_job(files_total=4, files_done=9)) == 100
    assert views.percent(make_job()) == 0


def test_job_label_names_a_manual_verify_and_unknown_kinds() -> None:
    assert views.job_label(make_job(kind=JobKind.VERIFY, trigger=Trigger.MANUAL)) == "Verify now"
    assert views.job_label(make_job(kind=JobKind.VERIFY)) == "Verifying"


def test_running_job_for_looks_past_other_projects(env: Env) -> None:
    db, sealed, unsealed = env.db, env.ids["sealed"], env.ids["unsealed"]
    other = db.enqueue_job(JobKind.VERIFY, unsealed, Trigger.AUTO, 10, NOW)
    assert views.running_job_for(db, sealed, None) is None  # only another project's job
    mine_queued = db.enqueue_job(JobKind.VERIFY, sealed, Trigger.AUTO, 10, NOW)
    found = views.running_job_for(db, sealed, None)
    assert found is not None and found.id == mine_queued
    db.set_job_state(mine_queued, JobState.RUNNING, NOW)
    found = views.running_job_for(db, sealed, None)
    assert found is not None and found.state is JobState.RUNNING
    current = db.get_job(other)
    assert views.running_job_for(db, sealed, current) is not None  # current is another project's


# --- views: review rows, summaries, status ----------------------------------------------------


def test_review_rows_details(env: Env) -> None:
    db, pid = env.db, env.ids["needs_review"]
    db.replace_review_items(
        pid,
        [
            ReviewItem("same_size.mov", ChangeKind.MODIFIED, 5, 5, 1, None),
            ReviewItem("grew.mov", ChangeKind.MODIFIED, 5, 9, 1, 1_790_000_000_000_000_000),
        ],
    )
    db.replace_sealed_files(pid, [SealedFile("same_size.mov", 5, 1, "aa")])
    db.replace_files(pid, [FileStat("same_size.mov", 5, 2), FileStat("brand_new.mov", 1, 2)])
    rows = views.review_rows(db, project(env, "needs_review"), MADRID)
    assert [(r.change, r.rel_path, r.detail) for r in rows] == [
        ("modified", "grew.mov", "size 5 B → 9 B, 2026-09-21"),
        ("modified", "same_size.mov", ""),
        ("added", "brand_new.mov", "added files are fine"),
    ]


def test_review_rows_without_sealed_files_lists_no_additions(env: Env) -> None:
    db, pid = env.db, env.ids["unsealed"]
    db.replace_files(pid, [FileStat("new.mov", 1, 1)])
    assert views.review_rows(db, project(env, "unsealed"), MADRID) == []


def test_review_summary_fallbacks(env: Env) -> None:
    db, pid = env.db, env.ids["needs_review"]
    db.clear_review_items(pid)
    db.set_state(pid, ProjectState.NEEDS_REVIEW, review_reason="size changed: a.mov")
    assert views.review_summary(db, project(env, "needs_review")) == "size changed"
    db.set_state(pid, ProjectState.NEEDS_REVIEW, review_reason=None)
    assert views.review_summary(db, project(env, "needs_review")) == (
        "something changed since the last seal"
    )
    db.set_state(pid, ProjectState.NEEDS_REVIEW, review_reason="verification: 1 corrupt; x")
    assert views.review_summary(db, project(env, "needs_review")) == "verification: 1 corrupt"


def test_status_text_per_state(env: Env) -> None:
    db, s = env.db, env.ref.value
    pid = env.ids["unsealed"]

    def text() -> str:
        return views.status_text(db, project(env, "unsealed"), s, None)

    assert text() == "no manifest yet · press Seal to create it"  # preexisting
    db.update_project_fields(pid, preexisting=0)
    assert text() == f"no manifest yet · seals itself after {s.settle_hours} h without changes"
    db.set_state(pid, ProjectState.CHANGED)
    assert text().startswith("new files since the last seal")
    db.set_state(pid, ProjectState.QUEUED)
    assert text() == "queued · runs after working hours"
    db.set_state(pid, ProjectState.IGNORED)
    assert text() == "ignored"
    db.set_state(pid, ProjectState.ERROR)
    assert text() == "problem: unknown error · retried on the next round"
    db.update_project_fields(pid, error="share went away")
    assert text() == "problem: share went away · retried on the next round"
    db.set_state(pid, ProjectState.HASHING)
    assert text() == "reading files"
    busy = make_job(bytes_total=4, bytes_done=1)
    assert views.status_text(db, project(env, "unsealed"), s, busy) == "reading files · 25%"
    db.set_state(pid, ProjectState.MISSING)
    assert text() == "missing: not on disk since the last round"
    sealed = views.status_text(db, project(env, "sealed"), s, None)
    assert sealed == "sealed 2026-10-02 · verified 2026-10-02"
    db.update_project_fields(env.ids["sealed"], last_verified_at=None)
    assert views.status_text(db, project(env, "sealed"), s, None) == "sealed 2026-10-02"


def test_counters_and_inbox_row_meta(env: Env) -> None:
    db = env.db
    db.set_state(env.ids["unsealed"], ProjectState.ERROR)
    c = views.counters(db.list_projects(), 3)
    assert (c.projects, c.needs_review, c.errors, c.queued, c.unsealed) == (3, 1, 1, 3, 0)
    # file_count of one: singular
    db.update_project_fields(env.ids["sealed"], file_count=1, total_bytes=None)
    box = views.inbox(db, db.list_projects(), env.ref.value, None, NOW)
    assert [r.meta for r in box.quiet] == ["1 file"]


def test_next_verification_without_now_is_never_overdue(env: Env) -> None:
    projects = env.db.list_projects()
    assert views.next_verification(projects, env.ref.value) == "2026-12-30"


def test_detail_sentence_variants(env: Env) -> None:
    db, s, pid = env.db, env.ref.value, env.ids["unsealed"]
    db.update_project_fields(env.ids["sealed"], last_verified_at=None)
    assert views.detail_sentence(db, project(env, "sealed"), s) == (
        "Sealed on 2026-10-02. Nothing has changed since."
    )
    db.update_project_fields(pid, preexisting=0)
    sentence = views.detail_sentence(db, project(env, "unsealed"), s)
    assert sentence.endswith(f"it seals itself after {s.settle_hours} h without changes.")
    rid = env.ids["needs_review"]
    db.set_state(rid, ProjectState.NEEDS_REVIEW, review_reason="verification: 2 corrupt; a")
    assert views.detail_sentence(db, project(env, "needs_review"), s) == (
        "The periodic verification found problems: 2 corrupt. Decide what to do."
    )
    job_id = db.enqueue_job(JobKind.SEAL, pid, Trigger.MANUAL, 130, NOW)
    db.set_job_state(job_id, JobState.RUNNING, NOW)
    db.set_state(pid, ProjectState.HASHING)
    assert "Cancel stops it at the next file" in views.detail_sentence(
        db, project(env, "unsealed"), s
    )
    db.set_state(pid, ProjectState.MISSING)
    assert "has not been on disk since the last round" in views.detail_sentence(
        db, project(env, "unsealed"), s
    )


# --- views: headline, history, activity -------------------------------------------------------


def test_headline_remaining_tones(env: Env) -> None:
    db = env.db
    for key in ("needs_review", "unsealed", "ignored"):
        db.delete_project(env.ids[key])
    only_sealed = db.list_projects()
    assert views.headline([], True) == views.Headline("calm", "No projects found yet.")
    assert views.headline(only_sealed, False) == views.Headline(
        "alert", "The archive is not reachable."
    )
    db.set_state(env.ids["sealed"], ProjectState.IGNORED)
    assert views.headline(db.list_projects(), True).text == "Every project is ignored."
    db.set_state(env.ids["sealed"], ProjectState.CHANGED)
    assert views.headline(db.list_projects(), True).text == (
        "1 project has new files waiting to be added."
    )
    db.set_state(env.ids["sealed"], ProjectState.HASHING)
    assert views.headline(db.list_projects(), True).text == "1 project is being sealed."
    db.set_state(env.ids["sealed"], ProjectState.MISSING)
    assert views.headline(db.list_projects(), True).text == "1 project is missing from the disk."


def test_load_history_without_history_and_with_a_broken_one(tmp_path: Path) -> None:
    assert views.load_history(tmp_path, MADRID) == views.History()
    (tmp_path / "ascmhl").mkdir()
    (tmp_path / "ascmhl" / "ascmhl_chain.xml").write_text("<not-a-chain")
    result = views.load_history(tmp_path, MADRID)
    assert result.generations == [] and result.error is not None
    assert result.error.startswith("the manifest history could not be read (")


def test_activity_failed_cancelled_retired_and_running_jobs(env: Env) -> None:
    db, sealed, unsealed = env.db, env.ids["sealed"], env.ids["unsealed"]
    tz = MADRID
    failed = db.enqueue_job(JobKind.SEAL, unsealed, Trigger.AUTO, 10, NOW)
    db.set_job_state(failed, JobState.FAILED, NOW + timedelta(minutes=1), error="disk went away")
    done = db.enqueue_job(JobKind.APPEND, sealed, Trigger.AUTO, 10, NOW)
    db.update_job_progress(done, 2, 2, 5 * GB, 5 * GB)
    db.set_job_state(done, JobState.DONE, NOW + timedelta(minutes=2))
    retire = db.record_finished_job(JobKind.RETIRE, None, Trigger.MANUAL, JobState.DONE, NOW)
    db.log(retire, "info", sealer.RETIRE_LOG_TEMPLATE.format(rel_path="2025/2025-05_X"), NOW)
    bare_retire = db.record_finished_job(
        JobKind.RETIRE, None, Trigger.MANUAL, JobState.DONE, NOW + timedelta(minutes=4)
    )
    assert bare_retire > retire  # no log line: the row still shows, with an empty name
    orphan = db.enqueue_job(JobKind.ROOT_MANIFEST, None, Trigger.AUTO, 5, NOW)
    db.set_job_state(orphan, JobState.FAILED, NOW + timedelta(minutes=3), error=None)
    second = db.enqueue_job(JobKind.SEAL, unsealed, Trigger.AUTO, 10, NOW)
    db.set_job_state(second, JobState.RUNNING, NOW)  # an older running job is not listed twice
    running = db.enqueue_job(JobKind.VERIFY, sealed, Trigger.MANUAL, 110, NOW, bypass_hours=True)
    db.set_job_state(running, JobState.RUNNING, NOW)
    db.update_job_progress(running, 1, 4, GB, 4 * GB)
    act = views.activity(db, tz, NOW + timedelta(minutes=10), None)
    assert [(r.tone, r.text) for r in act.upcoming] == [
        ("running", "Verify now 2025-01_CLIENTE-SELLADO")
    ]
    assert act.upcoming[0].detail == "1 of 4 files · 1.0 GB of 4.0 GB" and act.upcoming[0].pct == 25
    texts = {r.text: r for r in act.past}
    assert texts["Could not seal 2025-03_CLIENTE-NUEVO"].detail == "disk went away"
    assert texts["Added new files to 2025-01_CLIENTE-SELLADO"].detail == "2 files · 5.0 GB"
    assert texts["Retired 2025-05_X"].detail == "retired 2025/2025-05_X (history mirror deleted)"
    assert texts["Retired"].detail == ""
    assert texts["Could not update the archive manifest"].detail == ""


def test_activity_current_job_comes_first(env: Env) -> None:
    db, sealed = env.db, env.ids["sealed"]
    job_id = db.enqueue_job(JobKind.SEAL, sealed, Trigger.MANUAL, 130, NOW)
    current = db.get_job(job_id)
    assert current is not None
    act = views.activity(db, MADRID, NOW, current)
    # the DB state (queued) wins over the supervisor's copy, and the job is listed only once
    assert [(r.tone, r.text) for r in act.upcoming] == [("queued", "Seal 2025-01_CLIENTE-SELLADO")]


# --- settings form ----------------------------------------------------------------------------


def test_field_for_error_locations() -> None:
    assert settings_form._field_for(()) == "form"
    assert settings_form._field_for(("working_hours", "days")) == "days"
    assert settings_form._field_for(("working_hours",)) == "end"
    assert settings_form._field_for(("working_hours", "other")) == "end"
    assert settings_form._field_for(("settle_hours",)) == "settle_hours"


def test_build_settings_reports_a_model_level_error(env: Env) -> None:
    values = settings_form.values_from_settings(env.ref.value)
    values["start"] = values["end"] = "08:00"
    new, errors = settings_form.build_settings(values, env.ref.value)
    assert new is None and set(errors) == {"end"}


def test_glob_warnings_only_when_something_sealed_and_removed(env: Env) -> None:
    old = env.ref.value
    new = old.model_copy(update={"exclude_globs": []})
    assert settings_form.glob_warnings(old, new, any_sealed=False) == []
    assert settings_form.glob_warnings(old, old, any_sealed=True) == []
    assert "*.md" in settings_form.glob_warnings(old, new, any_sealed=True)[0]


def test_env_overrides_detects_plain_and_nested_variables() -> None:
    env_vars = {"MHLS_THEME": "dark", "mhls_working_hours__start": "07:00", "OTHER": "x"}
    assert settings_form.env_overrides(env_vars) == {"theme", "days", "start", "end"}
    assert settings_form.env_overrides({}) == set()


def test_parse_globs_dedupes_and_splits_on_commas_and_lines() -> None:
    assert settings_form.parse_globs("*.md, *.txt\n*.md\n ,") == ["*.md", "*.txt"]


# --- routes -----------------------------------------------------------------------------------


def test_stray_cache_survives_an_unreadable_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object) -> list[str]:
        raise OSError("stale handle")

    monkeypatch.setattr(routes, "find_stray_entries", boom)
    cache = routes.StrayCache()
    assert cache.get(tmp_path, 1, "_", True) == []
    assert cache.get(tmp_path, 1, "_", False) == []  # unreachable: the cached empty list


def test_api_status_includes_the_running_job(env: Env) -> None:
    job_id = env.db.enqueue_job(JobKind.SEAL, env.ids["unsealed"], Trigger.MANUAL, 130, NOW)
    env.db.set_job_state(job_id, JobState.RUNNING, NOW)
    env.sup.state = FakeStatus(current_job=env.db.get_job(job_id), last_cycle_at=NOW)
    body = env.client.get("/api/status").json()
    job = body["current_job"]
    assert (job["kind"], job["trigger"], job["state"]) == ("seal", "manual", "running")
    assert body["last_cycle_at"] == NOW.isoformat()
    assert body["next_change"] == "2026-10-02T07:00:00+00:00"


def test_header_of_a_job_without_project(env: Env) -> None:
    job_id = env.db.enqueue_job(JobKind.ROOT_MANIFEST, None, Trigger.AUTO, 5, NOW)
    env.db.set_job_state(job_id, JobState.RUNNING, NOW)
    env.sup.state = FakeStatus(current_job=env.db.get_job(job_id))
    assert "Updating the archive manifest" in env.client.get("/fragments/header").text


def test_detail_while_the_archive_is_unreachable(env: Env) -> None:
    env.sup.state = FakeStatus(archive_reachable=False)
    page = env.client.get(f"/projects/{env.ids['sealed']}").text
    assert "The archive is not reachable right now." in page


def test_retry_back_on_disk_without_a_verification(env: Env) -> None:
    name = "2025-05_CLIENTE-REVISADO"
    pid = env.db.upsert_project(f"2025/{name}", name, preexisting=True, now=NOW)
    env.db.update_project_fields(
        pid, last_generation_no=1, missing_since=NOW, state_before_missing=ProjectState.UNSEALED
    )
    env.db.set_state(pid, ProjectState.MISSING)
    (env.archive / "2025" / name).mkdir()
    r = env.client.post(f"/projects/{pid}/retry", headers=HX)
    assert r.status_code == 200 and "Back on disk." in r.text
    assert "verification queued" not in r.text


def test_retry_in_working_hours_says_when_the_verification_runs(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "utcnow", lambda: datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    name = "2025-05_CLIENTE-SELLADO2"
    pid = env.db.upsert_project(f"2025/{name}", name, preexisting=True, now=NOW)
    env.db.update_project_fields(
        pid, last_generation_no=1, missing_since=NOW, state_before_missing=ProjectState.SEALED
    )
    env.db.set_state(pid, ProjectState.MISSING)
    (env.archive / "2025" / name).mkdir()
    r = env.client.post(f"/projects/{pid}/retry", headers=HX)
    assert "verification queued, it runs after working hours." in r.text
    assert getattr(env.sup, "notified", 0) >= 1


def test_retire_conflicts_and_plain_redirect(env: Env) -> None:
    pid = env.ids["sealed"]  # not missing
    r = env.client.post(f"/projects/{pid}/retire", headers=HX)
    assert r.status_code == 409 and "only a missing project can be retired" in r.text
    name = "2025-05_CLIENTE-BORRADO"
    gone = env.db.upsert_project(f"2025/{name}", name, preexisting=True, now=NOW)
    env.db.update_project_fields(gone, missing_since=NOW, state_before_missing=ProjectState.SEALED)
    env.db.set_state(gone, ProjectState.MISSING)
    r = env.client.post(f"/projects/{gone}/retire", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/?retired={name}"
    assert f"{name} retired" in env.client.get(f"/?retired={name}").text


def test_scan_now_awaits_an_async_supervisor_and_redirects_plain_posts(env: Env) -> None:
    calls: list[str] = []

    async def request_scan_now() -> None:
        calls.append("awaited")

    env.sup.request_scan_now = request_scan_now  # type: ignore[assignment,method-assign]
    r = env.client.post("/scan-now", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert calls == ["awaited"]


def test_settings_post_save_failure_is_a_500_with_the_reason(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_new: Settings) -> None:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(routes, "_save", boom)
    data = settings_form.values_from_settings(env.ref.value)
    r = env.client.post("/settings", data=data)
    assert r.status_code == 500 and "could not save: Read-only file system" in r.text
    assert env.ref.replaced == 0


def test_healthz_reports_a_database_that_cannot_write(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(env.db, "transaction", broken)
    r = env.client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["db"] == "error" and r.json()["archive"] == "ok"
    assert r.json()["status"] == "error"


def test_events_endpoint_streams_the_bus_with_a_ping() -> None:
    bus = FakeBus()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ctx=SimpleNamespace(bus=bus)))
    )

    async def never() -> bool:
        return False

    request.is_disconnected = never

    async def run() -> tuple[Any, AsyncIterator[dict[str, str]]]:
        response = await routes.events(request)  # type: ignore[arg-type]
        return response, response.body_iterator  # type: ignore[return-value]

    response, stream = asyncio.run(run())
    assert response.ping_interval == routes.SSE_PING_SECONDS
    assert hasattr(stream, "__aiter__")
    assert bus.queues == []  # nothing subscribed until a client iterates


def test_activity_skips_other_log_lines_and_jobs_that_never_finished(env: Env) -> None:
    db, sealed = env.db, env.ids["sealed"]
    seal = db.enqueue_job(JobKind.SEAL, sealed, Trigger.AUTO, 10, NOW)
    db.log(seal, "info", "hashing started", NOW)  # not the review marker: skipped
    db.set_job_state(seal, JobState.DONE, NOW + timedelta(minutes=1))
    retire = db.record_finished_job(JobKind.RETIRE, None, Trigger.MANUAL, JobState.DONE, NOW)
    db.log(retire, "info", "something else first", NOW)
    db.log(retire, "info", sealer.RETIRE_LOG_TEMPLATE.format(rel_path="2025/2025-05_X"), NOW)
    ghost = db.enqueue_job(JobKind.VERIFY, sealed, Trigger.AUTO, 10, NOW)
    db.set_job_state(ghost, JobState.DONE, NOW)
    with db.transaction() as conn:  # done without a finish time (damaged row): not listed
        conn.execute("UPDATE jobs SET finished_at = NULL WHERE id = ?", (ghost,))
    texts = [r.text for r in views.activity(db, MADRID, NOW, None).past]
    assert "Sealed 2025-01_CLIENTE-SELLADO" in texts and "Retired 2025-05_X" in texts
    assert len(texts) == 2
