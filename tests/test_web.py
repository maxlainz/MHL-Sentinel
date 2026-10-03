"""web/: pages, buttons through ``sealer.request_*``, settings form, health and JSON (hito 3).

The hito 2 objects (supervisor, bus, settings holder) are fakes that satisfy the Protocols of
``web/deps.py``; the database is real (tmp).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from mhl_sentinel import sealer
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, JobRow, ReviewItem, SealedFile, VerifyResult
from mhl_sentinel.models import ChangeKind, FileStat, JobKind, JobState, ProjectState, Trigger
from mhl_sentinel.web import create_app, routes, views
from mhl_sentinel.web.routes import event_stream

NOW = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
GB = 1_000_000_000


@dataclass(frozen=True)
class FakeStatus:
    working_now: bool = False
    next_change: datetime | None = datetime(2026, 10, 2, 7, 0, tzinfo=UTC)
    gate_open: bool = True
    current_job: JobRow | None = None
    last_cycle_at: datetime | str | None = "2026-10-01T21:14:00.000000Z"
    archive_reachable: bool = True
    queued_jobs: int = 0


@dataclass
class FakeSupervisor:
    state: FakeStatus = field(default_factory=FakeStatus)
    scans: int = 0
    cancel_ok: bool = True
    cancels: list[int] = field(default_factory=list)

    def notify_job_queued(self) -> None:
        self.notified = getattr(self, "notified", 0) + 1

    def request_scan_now(self) -> None:
        self.scans += 1

    def request_cancel(self, project_id: int) -> bool:
        self.cancels.append(project_id)
        return self.cancel_ok

    def status(self) -> FakeStatus:
        return self.state


@dataclass(frozen=True)
class FakeEvent:
    kind: str
    payload: dict[str, Any]
    ts: str = "2026-10-01T22:00:00.000000Z"


class FakeBus:
    def __init__(self) -> None:
        self.queues: list[asyncio.Queue[Any]] = []
        self.unsubscribed = 0

    def subscribe(self) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue()
        self.queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        self.queues.remove(q)
        self.unsubscribed += 1


class FakeRef:
    def __init__(self, settings: Settings) -> None:
        self.value = settings
        self.replaced = 0

    def get(self) -> Settings:
        return self.value

    def replace(self, new: Settings) -> None:
        self.value = new
        self.replaced += 1


@dataclass
class Env:
    client: TestClient
    db: Database
    ref: FakeRef
    sup: FakeSupervisor
    ids: dict[str, int]
    archive: Path


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    archive, config = tmp_path / "archive", tmp_path / "config"
    archive.mkdir()
    (archive / "2025").mkdir()
    (archive / "loose-notes.txt").write_text("x")
    settings = Settings(
        archive_root=archive, config_dir=config, timezone="Europe/Madrid", exclude_globs=["*.md"]
    )
    db = Database(config / "state.db").open()
    ids: dict[str, int] = {}
    for name, state in (
        ("2025-01_CLIENTE-SELLADO", ProjectState.SEALED),
        ("2025-02_CLIENTE-REVISION", ProjectState.NEEDS_REVIEW),
        ("2025-03_CLIENTE-NUEVO", ProjectState.UNSEALED),
        ("2025-04_CLIENTE-IGNORADO", ProjectState.IGNORED),
    ):
        pid = db.upsert_project(f"2025/{name}", name, preexisting=True, now=NOW)
        db.set_state(pid, state)
        ids[state.value] = pid
    sealed, review = ids["sealed"], ids["needs_review"]
    db.update_project_fields(
        sealed, last_generation_no=2, last_sealed_at=NOW, last_verified_at=NOW,
        file_count=142, total_bytes=28_400_000_000,
    )  # fmt: skip
    db.update_project_fields(
        review, last_generation_no=1, last_sealed_at=NOW, file_count=3, total_bytes=3 * GB
    )
    db.set_state(review, ProjectState.NEEDS_REVIEW, review_reason="1 modified and 1 deleted ...")
    db.replace_sealed_files(
        review,
        [
            SealedFile("01_MASTERS/spot_30s_v2.mov", int(1.2 * GB), 1, "aa"),
            SealedFile("05_DELIVERABLES/spot_30s_old.mp4", GB, 1, "bb"),
        ],
    )
    db.replace_files(
        review,
        [
            FileStat("01_MASTERS/spot_30s_v2.mov", int(1.3 * GB), 2),
            FileStat("05_DELIVERABLES/spot_30s_v3.mp4", GB, 3),
        ],
    )
    db.replace_review_items(
        review,
        [
            ReviewItem(
                "01_MASTERS/spot_30s_v2.mov", ChangeKind.MODIFIED,
                int(1.2 * GB), int(1.3 * GB), 1, 1_790_000_000_000_000_000,
            ),
            ReviewItem("05_DELIVERABLES/spot_30s_old.mp4", ChangeKind.DELETED, GB, None, 1, None),
        ],
    )  # fmt: skip
    ref, sup = FakeRef(settings), FakeSupervisor()
    app = create_app(db, ref, sup, FakeBus())
    with TestClient(app) as client:
        yield Env(client, db, ref, sup, ids, archive)
    db.close()


HX = {"HX-Request": "true"}


def test_main_page_lists_projects_with_badges_and_counters(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Today" is fixed: fmt_when shows a bare HH:MM only for the current local day.
    monkeypatch.setattr(routes, "utcnow", lambda: datetime(2026, 10, 1, 21, 30, tzinfo=UTC))
    r = env.client.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'sse-connect="/events"' in html
    assert "sse:project.state" in html and "sse:cycle.finished" in html
    assert "Archive: <strong>OK</strong>" in html
    assert "last round 23:14" in html  # 21:14 UTC shown in Europe/Madrid
    flat = re.sub(r"\s+", " ", html)
    assert "1 project needs your decision." in flat  # the headline
    assert "3 projects watched, 1 ignored" in flat  # ignored ones are not counted
    assert 'Needs your decision <span class="count">1</span>' in flat
    assert 'Not sealed yet <span class="count">1</span>' in flat
    assert "Review changes" in html and 'action="/projects/' in html
    assert re.search(r'action="/projects/\d+/seal\?from=inbox"', html)  # one-click Seal
    assert 'id="activity"' in html and "Outside working hours" in html
    full_list = html.split('id="all-projects"', 1)[1]
    lights = re.findall(r'<li data-state="(\w+)"[^>]*><span class="light (\w+)"', full_list)
    assert lights == [
        ("needs_review", "red"),
        ("unsealed", "amber"),
        ("sealed", "green"),
        ("ignored", "grey"),
    ]
    assert "review: 1 modified, 1 deleted since the last seal" in html
    assert "sealed 2026-10-02 · verified 2026-10-02" in html  # 22:00 UTC = next day in Madrid
    assert "press Seal" in html
    assert "loose-notes.txt" in html  # D49: out-of-place entries
    assert str(env.archive) not in html  # D10: no absolute paths on the main screen


def test_fragments(env: Env) -> None:
    assert 'id="projects"' in env.client.get("/fragments/projects").text
    header = env.client.get("/fragments/header").text
    assert 'id="status-header"' in header
    assert re.search(r"the app works until (\w{3} )?(\d{4}-\d\d-\d\d )?09:00", header)
    assert "inbox-changed from:body" in header  # buttons refresh the headline
    activity = env.client.get("/fragments/activity").text
    assert 'id="activity"' in activity and "sse:job.progress" in activity
    env.sup.state = FakeStatus(working_now=True, archive_reachable=False)
    header = env.client.get("/fragments/header").text
    assert "The archive is not reachable." in header
    assert "Working hours: the app waits until" in header
    assert "Waiting for the archive" in env.client.get("/fragments/activity").text
    assert env.client.get("/").status_code == 200  # degrades, does not crash


def test_review_detail_shows_items_and_buttons(env: Env) -> None:
    r = env.client.get(f"/projects/{env.ids['needs_review']}")
    assert r.status_code == 200
    rows = re.findall(r'<tr data-change="(\w+)"><td>\w+</td><td class="path">([^<]+)</td>', r.text)
    assert rows == [
        ("modified", "01_MASTERS/spot_30s_v2.mov"),
        ("deleted", "05_DELIVERABLES/spot_30s_old.mp4"),
        ("added", "05_DELIVERABLES/spot_30s_v3.mp4"),
    ]
    assert "size 1.2 GB → 1.3 GB" in r.text
    assert "added files are fine" in r.text
    assert "Accept as new version" in r.text and "Postpone" in r.text
    assert ">Seal<" not in r.text


def test_header_shows_the_root_manifest_date(env: Env) -> None:
    assert "Root manifest: not yet" in env.client.get("/fragments/header").text
    env.db.set_kv("last_root_manifest_at", "2026-10-01T23:30:00.000000Z")
    header = env.client.get("/fragments/header").text
    assert "Root manifest: 2026-10-02 01:30" in header  # Europe/Madrid
    assert "sse:root.updated" in header
    assert "Root manifest: 2026-10-02 01:30" in env.client.get("/").text


def test_verification_review_shows_the_verify_results(env: Env) -> None:
    pid = env.ids["needs_review"]
    job_id = env.db.enqueue_job(JobKind.VERIFY, pid, Trigger.AUTO, 10, NOW)
    env.db.set_job_state(job_id, JobState.DONE, NOW)
    env.db.replace_verify_results(
        job_id,
        pid,
        [
            VerifyResult("01_MASTERS/spot_30s_v2.mov", "aa", "cc", "corrupt"),
            VerifyResult("02_GRADE/grade.drx", "dd", "dd", "ok"),
            VerifyResult("05_DELIVERABLES/spot_30s_old.mp4", "bb", None, "missing"),
            VerifyResult("05_DELIVERABLES/spot_30s_v3.mp4", None, "ee", "added"),
        ],
    )
    env.db.set_state(
        pid,
        ProjectState.NEEDS_REVIEW,
        review_reason="verification: 1 corrupt, 1 missing; files: 01_MASTERS/spot_30s_v2.mov, "
        "05_DELIVERABLES/spot_30s_old.mp4",
    )
    main = env.client.get("/").text
    assert "review: verification: 1 corrupt, 1 missing" in main
    r = env.client.get(f"/projects/{pid}")
    assert r.status_code == 200
    assert "Periodic verification of 2026-10-02 00:00" in r.text
    assert "1 file OK." in r.text
    rows = re.findall(
        r'<tr data-verify="(\w+)"><td>[^<]+</td><td class="path">([^<]+)</td>', r.text
    )
    assert rows == [
        ("corrupt", "01_MASTERS/spot_30s_v2.mov"),
        ("missing", "05_DELIVERABLES/spot_30s_old.mp4"),
        ("added", "05_DELIVERABLES/spot_30s_v3.mp4"),
    ]
    assert 'data-change="' not in r.text  # the scan-diff table is replaced
    assert "Accept as new version" in r.text and "Postpone" in r.text


def test_detail_unknown_project_is_404(env: Env) -> None:
    assert env.client.get("/projects/999").status_code == 404


def test_seal_button_queues_through_sealer(env: Env) -> None:
    pid = env.ids["unsealed"]
    r = env.client.post(f"/projects/{pid}/seal", headers=HX)
    assert r.status_code == 200 and "Seal requested" in r.text
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.QUEUED
    job = env.db.next_job(NOW)
    assert job is not None and job.kind is JobKind.SEAL and job.trigger is Trigger.MANUAL
    again = env.client.post(f"/projects/{pid}/seal", headers=HX)
    assert again.status_code == 409  # not unsealed any more


def test_cancel_button_withdraws_a_queued_seal(env: Env) -> None:
    pid = env.ids["unsealed"]
    assert "Cancel" not in env.client.get(f"/projects/{pid}").text
    r = env.client.post(f"/projects/{pid}/seal", headers=HX)
    assert r.status_code == 200 and "/cancel" in r.text and "Cancel" in r.text
    r = env.client.post(f"/projects/{pid}/cancel", headers=HX)
    assert r.status_code == 200 and "Cancelled" in r.text and "/cancel" not in r.text
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.UNSEALED
    assert env.db.next_job(NOW) is None
    assert env.client.post(f"/projects/{pid}/cancel", headers=HX).status_code == 409


def test_inbox_buttons_stay_on_the_inbox(env: Env) -> None:
    pid = env.ids["unsealed"]
    r = env.client.post(f"/projects/{pid}/seal?from=inbox", headers=HX)
    assert r.status_code == 200 and 'id="projects"' in r.text
    assert "2025-03_CLIENTE-NUEVO: Seal requested" in r.text
    assert r.headers["HX-Trigger"] == "inbox-changed"
    assert f'action="/projects/{pid}/cancel?from=inbox"' in r.text  # In progress, with Cancel
    activity = env.client.get("/fragments/activity").text
    assert "Seal 2025-03_CLIENTE-NUEVO" in activity  # next in the queue
    r = env.client.post(f"/projects/{pid}/cancel?from=inbox")  # no JS: back to the inbox
    assert r.status_code == 200 and r.url.path == "/"
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.UNSEALED
    r = env.client.post(f"/projects/{pid}/cancel?from=inbox", headers=HX)
    assert r.status_code == 409 and 'id="projects"' in r.text and "nothing to cancel" in r.text


def test_activity_log_lists_finished_jobs(env: Env) -> None:
    sealed = env.ids["sealed"]
    done = env.db.enqueue_job(JobKind.SEAL, sealed, Trigger.MANUAL, 130, NOW)
    env.db.update_job_progress(done, 142, 142, 28 * GB, 28 * GB)
    env.db.set_job_state(done, JobState.DONE, NOW)
    failed = env.db.enqueue_job(JobKind.VERIFY, sealed, Trigger.AUTO, 10, NOW)
    env.db.set_job_state(failed, JobState.FAILED, NOW, error="share went away")
    html = env.client.get("/fragments/activity").text
    assert "Sealed 2025-01_CLIENTE-SELLADO" in html and "142 files · 28.0 GB" in html
    assert "Could not verify 2025-01_CLIENTE-SELLADO" in html and "share went away" in html
    assert str(env.archive) not in html


def test_accept_and_postpone(env: Env) -> None:
    pid = env.ids["needs_review"]
    r = env.client.post(f"/projects/{pid}/postpone", headers=HX)
    assert r.status_code == 200 and "Postponed: nothing changed." in r.text
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.NEEDS_REVIEW
    r = env.client.post(f"/projects/{pid}/accept")  # plain form post: redirect to the detail
    assert r.status_code == 200 and r.url.path == f"/projects/{pid}"
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.QUEUED
    job = env.db.next_job(NOW)
    assert job is not None and job.kind is JobKind.ACCEPT_NEW_VERSION


def test_ignore_and_unignore(env: Env) -> None:
    pid = env.ids["sealed"]
    assert env.client.post(f"/projects/{pid}/ignore", headers=HX).status_code == 200
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.IGNORED
    r = env.client.post(f"/projects/{pid}/unignore", headers=HX)
    assert r.status_code == 200 and "Unignore" not in r.text
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.SEALED
    assert env.client.post(f"/projects/{pid}/explode").status_code == 404


def test_progress_bar_while_hashing(env: Env) -> None:
    pid = env.ids["unsealed"]
    job_id = env.db.enqueue_job(JobKind.SEAL, pid, Trigger.MANUAL, 130, NOW)
    env.db.set_job_state(job_id, JobState.RUNNING, NOW)
    env.db.update_job_progress(job_id, 1, 4, GB, 4 * GB)
    env.db.set_state(pid, ProjectState.HASHING)
    env.sup.state = FakeStatus(current_job=env.db.get_job(job_id))
    r = env.client.get(f"/fragments/projects/{pid}/progress")
    assert '<progress value="25" max="100">' in r.text
    assert "sse:job.progress" in r.text
    assert "1 of 4 files" in r.text
    assert "Sealing" in env.client.get("/fragments/header").text
    assert "reading files · 25%" in env.client.get("/").text


def test_cancel_a_running_seal_goes_through_the_supervisor(env: Env) -> None:
    """D57: while hashing a manual Seal the card offers Cancel; the supervisor aborts it."""
    pid = env.ids["unsealed"]
    job_id = env.db.enqueue_job(JobKind.SEAL, pid, Trigger.MANUAL, 130, NOW)
    env.db.set_job_state(job_id, JobState.RUNNING, NOW)
    env.db.set_state(pid, ProjectState.HASHING)
    env.sup.state = FakeStatus(current_job=env.db.get_job(job_id))
    page = env.client.get(f"/projects/{pid}").text
    assert f'action="/projects/{pid}/cancel"' in page and "at the next file" in page
    assert f'action="/projects/{pid}/cancel?from=inbox"' in env.client.get("/").text
    r = env.client.post(f"/projects/{pid}/cancel", headers=HX)
    assert r.status_code == 200 and "Cancelling: the app stops at the next file." in r.text
    assert env.sup.cancels == [pid]
    job = env.db.get_job(job_id)
    assert job is not None and job.state is JobState.RUNNING  # the hasher thread finishes it
    env.sup.cancel_ok = False  # e.g. the job ended in the meantime
    r = env.client.post(f"/projects/{pid}/cancel", headers=HX)
    assert r.status_code == 409 and "cannot be cancelled" in r.text


def test_no_cancel_while_verifying(env: Env) -> None:
    pid = env.ids["sealed"]
    job_id = env.db.enqueue_job(JobKind.VERIFY, pid, Trigger.AUTO, 10, NOW)
    env.db.set_job_state(job_id, JobState.RUNNING, NOW)
    env.db.set_state(pid, ProjectState.HASHING)
    env.sup.state = FakeStatus(current_job=env.db.get_job(job_id))
    page = env.client.get(f"/projects/{pid}").text
    assert f'action="/projects/{pid}/cancel"' not in page and "at the next file" not in page


def test_scan_now(env: Env) -> None:
    r = env.client.post("/scan-now", headers=HX)
    assert r.status_code == 200 and "Scan requested" in r.text
    assert env.sup.scans == 1


def test_settings_get_renders_defaults(env: Env) -> None:
    r = env.client.get("/settings")
    assert r.status_code == 200
    assert 'name="settle_hours" min="0" step="1" value="168"' in r.text
    assert 'value="09:00"' in r.text and 'value="19:00"' in r.text
    assert re.search(r'value="mon"\s+checked', r.text)
    assert re.search(r'value="sat"\s*>', r.text)
    assert "xxh128 (fixed)" in r.text and "disabled" in r.text


def _form(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "project_depth": "1",
        "ignore_prefixes": "_ @ # .",
        "exclude_globs": "*.md, *.txt",
        "days": ["mon", "tue", "wed", "thu", "fri"],
        "start": "09:00",
        "end": "19:00",
        "timezone": "Europe/Madrid",
        "settle_hours": "72",
        "verify_interval_days": "90",
        "scan_interval_minutes": "60",
        "log_level": "info",
    }
    data.update(over)
    return data


def test_settings_post_bad_time_shows_error(env: Env) -> None:
    r = env.client.post("/settings", data=_form(start="25:00"))
    assert r.status_code == 422
    assert 'data-error-for="start"' in r.text and "HH:MM" in r.text
    assert env.ref.replaced == 0
    assert not env.ref.value.config_file.exists()


def test_settings_post_valid_writes_yaml_and_replaces(env: Env) -> None:
    r = env.client.post("/settings", data=_form())
    assert r.status_code == 200 and "Saved." in r.text
    assert env.ref.replaced == 1
    new = env.ref.value
    assert new.settle_hours == 72 and new.exclude_globs == ["*.md", "*.txt"]
    assert new.ignore_prefixes == "_@#." and new.archive_root == env.archive
    data = yaml.safe_load(new.config_file.read_text())
    assert data["settle_hours"] == 72 and "archive_root" not in data
    assert "stay excluded" not in r.text  # nothing removed


def test_settings_warns_when_excluded_types_shrink(env: Env) -> None:
    r = env.client.post("/settings", data=_form(exclude_globs=""))
    assert r.status_code == 200
    assert "stay excluded" in r.text and env.ref.value.exclude_globs == []


def test_theme_defaults_to_auto(env: Env) -> None:
    assert env.ref.value.theme == "auto"
    page = env.client.get("/").text
    assert "data-theme=" not in page and 'content="light dark"' in page
    settings = env.client.get("/settings").text
    assert re.search(r'name="theme" value="auto"\s+checked', settings)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_theme_setting_is_saved_and_applied(env: Env, theme: str) -> None:
    r = env.client.post("/settings", data=_form(theme=theme))
    assert r.status_code == 200 and "Saved." in r.text
    assert f'data-theme="{theme}"' in r.text  # the reloaded page already uses it
    assert yaml.safe_load(env.ref.value.config_file.read_text())["theme"] == theme
    for path in ("/", f"/projects/{env.ids['sealed']}", "/settings"):
        assert f'<html lang="en" data-theme="{theme}">' in env.client.get(path).text


def test_theme_rejects_unknown_values(env: Env) -> None:
    r = env.client.post("/settings", data=_form(theme="purple"))
    assert r.status_code == 422 and 'data-error-for="theme"' in r.text
    assert env.ref.replaced == 0 and env.ref.value.theme == "auto"


def test_healthz(env: Env) -> None:
    r = env.client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["db"] == "ok"
    env.sup.state = FakeStatus(archive_reachable=False)
    r = env.client.get("/healthz")
    assert r.status_code == 503 and r.json()["archive"] == "unreachable"


def test_api_endpoints(env: Env) -> None:
    status = env.client.get("/api/status").json()
    assert status["archive_reachable"] is True
    assert status["counters"]["needs_review"] == 1
    assert status["next_change"].startswith("2026-10-02T07:00")
    projects = env.client.get("/api/projects").json()["projects"]
    assert {p["state"] for p in projects} == {"sealed", "needs_review", "unsealed", "ignored"}


def test_event_stream_forwards_and_unsubscribes() -> None:
    async def run() -> tuple[list[dict[str, str]], int]:
        bus = FakeBus()
        calls = 0

        async def disconnected() -> bool:
            nonlocal calls
            calls += 1
            return calls > 2

        stream = event_stream(bus, disconnected, poll=0.01)
        first = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        bus.queues[0].put_nowait(FakeEvent("project.state", {"id": 1, "state": "sealed"}))
        out = [await first]
        out += [m async for m in stream]
        return out, bus.unsubscribed

    messages, unsubscribed = asyncio.run(run())
    assert messages[0]["event"] == "project.state"
    assert '"state": "sealed"' in messages[0]["data"]
    assert unsubscribed == 1


def test_detail_reads_generations_from_the_history(env: Env) -> None:
    import xxhash

    from mhl_sentinel.mhlwriter import write_project_generation

    project = env.archive / "2025" / "2025-01_CLIENTE-SELLADO"
    (project / "01_MASTERS").mkdir(parents=True)
    (project / "01_MASTERS" / "a.mov").write_bytes(b"a" * 10)
    digest = xxhash.xxh128(b"a" * 10).hexdigest()
    write_project_generation(project, {"01_MASTERS/a.mov": {"xxh128": digest}})
    r = env.client.get(f"/projects/{env.ids['sealed']}")
    assert re.search(
        r'<li data-generation="1">\s*<span class="gen">Generation 1<small>current</small></span>'
        r'\s*<span class="info"><span class="created">\d{4}-\d\d-\d\d \d\d:\d\d</span> · '
        r'<span class="files">1</span> file',
        r.text,
    )
    assert "1 generation" in r.text
    (project / "ascmhl" / "ascmhl_chain.xml").write_text("broken")
    r = env.client.get(f"/projects/{env.ids['sealed']}")
    assert r.status_code == 200 and "could not be read" in r.text


# --- inbox (proposal D, D54) ---------------------------------------------------------------


def _headline(env: Env, archive_ok: bool = True) -> views.Headline:
    return views.headline(env.db.list_projects(), archive_ok)


def test_headline_tones_follow_every_state(env: Env) -> None:
    ids, db = env.ids, env.db
    assert _headline(env) == views.Headline("alert", "1 project needs your decision.")
    assert _headline(env, archive_ok=False).text == "The archive is not reachable."
    db.set_state(ids["needs_review"], ProjectState.SEALED)
    assert _headline(env) == views.Headline("wait", "1 project is not sealed yet.")
    db.set_state(ids["unsealed"], ProjectState.QUEUED)  # a Seal was pressed
    assert _headline(env) == views.Headline("wait", "1 project is being sealed.")
    header = env.client.get("/fragments/header").text
    assert "being sealed" in header and "Every project is sealed" not in header
    db.set_state(ids["unsealed"], ProjectState.HASHING)
    assert _headline(env).text == "1 project is being sealed."
    db.set_state(ids["unsealed"], ProjectState.CHANGED)
    assert _headline(env) == views.Headline("wait", "1 project has new files waiting to be added.")
    db.set_state(ids["unsealed"], ProjectState.SEALED)
    assert _headline(env) == views.Headline("calm", "All quiet. Every project is sealed.")
    assert ", all quiet" in env.client.get("/fragments/projects").text
    for key in ("sealed", "needs_review", "unsealed"):
        db.set_state(ids[key], ProjectState.IGNORED)
    assert _headline(env).text == "Every project is ignored."
    inbox = env.client.get("/fragments/projects").text
    assert "Every project is ignored." in inbox and "all quiet" not in inbox
    assert views.headline([], True).text == "No projects found yet."


def test_next_verification_estimate_and_overdue(env: Env) -> None:
    settings = env.ref.value
    projects = env.db.list_projects()  # sealed and verified at NOW (22:00 UTC, next day in Madrid)
    assert views.next_verification(projects, settings, NOW) == "2026-12-30"
    later = NOW + timedelta(days=120)
    assert views.next_verification(projects, settings, later) == views.OVERDUE
    unsealed_only = [p for p in projects if p.state is not ProjectState.SEALED]
    assert views.next_verification(unsealed_only, settings, NOW) == ""
    assert "Next periodic verification due around 2026-12-30" in env.client.get("/").text


def test_overdue_verification_wording(env: Env) -> None:
    env.db.update_project_fields(
        env.ids["sealed"], last_verified_at=NOW - timedelta(days=400), last_sealed_at=None
    )
    assert "Some periodic verifications are overdue" in env.client.get("/fragments/projects").text


def test_activity_marks_reviews_and_orders_by_finish(env: Env) -> None:
    db, sealed, unsealed = env.db, env.ids["sealed"], env.ids["unsealed"]
    verify = db.enqueue_job(JobKind.VERIFY, sealed, Trigger.AUTO, 10, NOW)
    db.replace_verify_results(
        verify, sealed, [VerifyResult("01_MASTERS/a.mov", "aa", "cc", "corrupt")]
    )
    db.set_job_state(verify, JobState.DONE, NOW + timedelta(minutes=30))
    seal = db.enqueue_job(JobKind.SEAL, unsealed, Trigger.MANUAL, 130, NOW)
    db.set_job_state(seal, JobState.DONE, NOW + timedelta(minutes=10))
    db.log(seal, "warning", sealer.REVIEW_LOG_PREFIX + "1 modified since the seal", NOW)
    cancelled = db.enqueue_job(JobKind.SEAL, unsealed, Trigger.MANUAL, 130, NOW)
    db.set_job_state(cancelled, JobState.CANCELLED, NOW + timedelta(minutes=5))
    ok = db.enqueue_job(JobKind.ROOT_MANIFEST, None, Trigger.AUTO, 5, NOW)
    db.set_job_state(ok, JobState.DONE, NOW + timedelta(minutes=20))
    past = views.activity(db, env.ref.value.tzinfo, NOW, None).past
    assert [(r.tone, r.text) for r in past] == [
        ("review", "Verification found problems in 2025-01_CLIENTE-SELLADO"),
        ("done", "Updated the archive manifest"),
        ("review", "2025-03_CLIENTE-NUEVO needs your decision"),
        ("cancelled", "Cancelled: Seal 2025-03_CLIENTE-NUEVO"),
    ]  # by finish time, not by id
    assert past[0].detail == "1 corrupt"
    assert past[2].detail == "nothing was written: 1 modified since the seal"
    html = env.client.get("/fragments/activity").text
    assert '<li class="review">' in html and "Sealed 2025-03" not in html


def test_inbox_folds_long_lists_and_orders_moving(env: Env) -> None:
    db = env.db
    for i in range(7):
        pid = db.upsert_project(f"2025/2025-09_CLIENTE-X{i}", f"2025-09_CLIENTE-X{i}", True, NOW)
        db.set_state(pid, ProjectState.UNSEALED)
    html = env.client.get("/fragments/projects").text
    assert "Show 2 more not sealed" in html  # 8 unsealed, 6 shown
    queued = db.upsert_project("2025/2025-10_A-QUEUED", "2025-10_A-QUEUED", True, NOW)
    db.set_state(queued, ProjectState.QUEUED)
    hashing = db.upsert_project("2025/2025-11_Z-HASHING", "2025-11_Z-HASHING", True, NOW)
    db.set_state(hashing, ProjectState.HASHING)
    box = views.inbox(db, db.list_projects(), env.ref.value, None, NOW)
    assert [r.view.state for r in box.moving] == ["hashing", "queued"]
    for p in db.list_projects()[:9]:  # one queued job per project
        db.enqueue_job(JobKind.VERIFY, p.id, Trigger.AUTO, 10, NOW)
    upcoming = views.activity(db, env.ref.value.tzinfo, NOW, None).upcoming
    assert len(upcoming) == 9 and upcoming[-1].text == "and 1 more"


def test_detail_sentence_per_state(env: Env) -> None:
    db, settings = env.db, env.ref.value

    def sentence(key: str) -> str:
        project = db.get_project(env.ids[key])
        assert project is not None
        return views.detail_sentence(db, project, settings)

    assert sentence("sealed") == (
        "Sealed on 2026-10-02. Nothing has changed since. Last verified on 2026-10-02."
    )
    assert sentence("needs_review").startswith("Files changed since the last seal (1 modified,")
    assert sentence("unsealed").startswith("No manifest yet. Press Seal")
    assert sentence("ignored") == "Ignored. The app leaves this folder alone."
    pid = env.ids["unsealed"]
    expected = {
        ProjectState.QUEUED: "Queued. The app works on it after working hours.",
        ProjectState.HASHING: "Reading every file to write the manifest.",
    }
    for state, text in expected.items():
        db.set_state(pid, state)
        assert sentence("unsealed") == text
    db.set_state(pid, ProjectState.CHANGED)
    assert sentence("unsealed").startswith("New files were added since the last seal")
    db.set_state(pid, ProjectState.ERROR)
    db.update_project_fields(pid, error="share went away")
    assert sentence("unsealed") == (
        "Something went wrong: share went away. The app retries next round."
    )


def test_project_card_refreshes_on_job_events(env: Env) -> None:
    card = env.client.get(f"/fragments/projects/{env.ids['unsealed']}").text
    assert "sse:job.started" in card and "sse:job.finished" in card


# --- missing projects, Retire / Retry, MHL download, Verify now (D58-D63) ---------------------


def _make_missing(env: Env, name: str = "2025-05_CLIENTE-BORRADO") -> int:
    pid = env.db.upsert_project(f"2025/{name}", name, preexisting=True, now=NOW)
    env.db.update_project_fields(
        pid, last_generation_no=1, last_sealed_at=NOW, last_verified_at=NOW,
        file_count=10, total_bytes=2 * GB, missing_since=NOW,
        state_before_missing=ProjectState.SEALED,
    )  # fmt: skip
    env.db.set_state(pid, ProjectState.MISSING)
    return pid


def _mirror(env: Env, rel: str) -> Path:
    folder = env.ref.get().config_dir / "history" / rel / "ascmhl"
    folder.mkdir(parents=True)
    (folder / "ascmhl_chain.xml").write_text("<chain/>")
    (folder / "0001_x_2026-10-01_220000Z.mhl").write_text("<hashlist/>")
    return folder


def test_inbox_shows_a_missing_project_as_a_decision(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "utcnow", lambda: datetime(2026, 10, 1, 23, 0, tzinfo=UTC))
    pid = _make_missing(env)
    html = env.client.get("/").text
    flat = re.sub(r"\s+", " ", html)
    assert 'Needs your decision <span class="count">2</span>' in flat
    assert "2 projects need your decision." in flat
    assert "missing: not on disk since 00:00" in flat  # 22:00 UTC is midnight in Madrid
    assert "sealed 2026-10-02" in flat and "10 files" in flat and "verified OK 2026-10-02" in flat
    assert f'data-dialog-open="retire-{pid}"' in html
    assert f'action="/projects/{pid}/retry?from=inbox"' in html
    assert "This deletes the app's record and the saved MHL history" in html
    assert "Forget and download MHL" in html and "The archive folder itself is not touched." in html
    assert re.search(r'data-state="missing"[^>]*><span class="light red"', html)
    card = env.client.get(f"/projects/{pid}").text
    assert "Retry" in card and "Forget permanently" in card and "Verify now" not in card
    assert "Ignore" not in card


def test_headline_only_missing_is_honest(env: Env) -> None:
    for state in ("needs_review", "unsealed", "ignored"):
        env.db.delete_project(env.ids[state])
    _make_missing(env)
    flat = re.sub(r"\s+", " ", env.client.get("/").text)
    assert "1 project is missing from the disk." in flat


def test_retry_both_paths(env: Env) -> None:
    pid = _make_missing(env)
    r = env.client.post(f"/projects/{pid}/retry", headers=HX)
    assert r.status_code == 200 and "Still not on disk" in r.text
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.MISSING
    folder = env.archive / "2025" / "2025-05_CLIENTE-BORRADO"
    folder.mkdir()
    (folder / "m.mov").write_bytes(b"x")  # D67: an empty folder is still missing
    r = env.client.post(f"/projects/{pid}/retry?from=inbox", headers=HX)
    assert r.status_code == 200 and "Back on disk" in r.text
    project = env.db.get_project(pid)
    assert project is not None and project.state is not ProjectState.MISSING
    assert env.client.post(f"/projects/{pid}/retry", headers=HX).status_code == 409


def test_retire_deletes_the_project_and_leaves_a_log_line(env: Env) -> None:
    pid = _make_missing(env)
    folder = _mirror(env, "2025/2025-05_CLIENTE-BORRADO")
    r = env.client.post(f"/projects/{pid}/retire?from=inbox", headers=HX)
    assert r.status_code == 200 and "2025-05_CLIENTE-BORRADO forgotten" in r.text
    assert env.db.get_project(pid) is None and not folder.exists()
    activity = env.client.get("/fragments/activity").text
    assert "Forgot 2025-05_CLIENTE-BORRADO" in activity
    assert "(history mirror deleted)" in activity
    assert env.client.post(f"/projects/{pid}/retire", headers=HX).status_code == 404


def test_retire_from_the_detail_page_redirects_home(env: Env) -> None:
    pid = _make_missing(env)
    r = env.client.post(f"/projects/{pid}/retire", headers=HX)
    assert r.status_code == 200 and r.headers["HX-Redirect"].startswith("/?retired=")
    pid = _make_missing(env, "2025-06_CLIENTE-OTRO")
    r = env.client.post(f"/projects/{pid}/retire")  # no JS
    assert r.status_code == 200 and r.url.path == "/"
    assert "2025-06_CLIENTE-OTRO forgotten" in r.text


def test_retire_refuses_a_project_that_is_not_missing(env: Env) -> None:
    pid = env.ids["sealed"]
    r = env.client.post(f"/projects/{pid}/retire", headers=HX)
    assert r.status_code == 409
    assert env.db.get_project(pid) is not None


def test_history_zip_download(env: Env) -> None:
    import io
    import zipfile

    pid = _make_missing(env)
    assert env.client.get(f"/projects/{pid}/history.zip").status_code == 404
    _mirror(env, "2025/2025-05_CLIENTE-BORRADO")
    r = env.client.get(f"/projects/{pid}/history.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="2025-05_CLIENTE-BORRADO-ascmhl.zip"' in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith("ascmhl_chain.xml") for n in names)
    assert env.client.get("/projects/999/history.zip").status_code == 404


def test_verify_now_off_hours_queues_directly(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "utcnow", lambda: NOW)  # 00:00 Madrid
    pid = env.ids["sealed"]
    card = env.client.get(f"/projects/{pid}").text
    assert "Verify now" in card and "during working hours" not in card
    r = env.client.post(f"/projects/{pid}/verify", headers=HX)
    assert r.status_code == 200 and "Verification queued ahead of the rest" in r.text
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.QUEUED
    job = env.db.queued_job(pid)
    assert job is not None and job.kind is JobKind.VERIFY and job.bypass_hours
    assert getattr(env.sup, "notified", 0) >= 1
    assert "/cancel" in r.text  # a queued manual verify is cancellable
    assert "Verify now " + "2025-01_CLIENTE-SELLADO" in env.client.get("/fragments/activity").text
    r = env.client.post(f"/projects/{pid}/cancel", headers=HX)
    assert r.status_code == 200
    project = env.db.get_project(pid)
    assert project is not None and project.state is ProjectState.SEALED


def test_verify_now_in_working_hours_warns_first(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "utcnow", lambda: datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    pid = env.ids["sealed"]
    card = re.sub(r"\s+", " ", env.client.get(f"/projects/{pid}").text)
    assert "Verify now reads 28.4 GB from the NAS during working hours" in card
    assert "can slow everyone down. Continue?" in card
    assert f'data-dialog-open="verify-{pid}"' in card
    r = env.client.post(f"/projects/{pid}/verify", headers=HX)
    assert r.status_code == 200 and "Verification started." in r.text


def test_verify_now_only_for_sealed(env: Env) -> None:
    pid = env.ids["unsealed"]
    assert "Verify now" not in env.client.get(f"/projects/{pid}").text
    assert env.client.post(f"/projects/{pid}/verify", headers=HX).status_code == 409


def test_api_projects_carries_missing_since(env: Env) -> None:
    pid = _make_missing(env)
    body = env.client.get("/api/projects").json()
    row = next(p for p in body["projects"] if p["id"] == pid)
    assert row["state"] == "missing" and row["state_before_missing"] == "sealed"
    assert row["missing_since"]
    assert env.client.get("/api/status").json()["counters"]["missing"] == 1


def test_a_finished_verify_now_reads_verified(env: Env) -> None:
    """The activity log names a running or queued Verify now as such, but a finished one as
    "Verified", like any verification."""
    pid = env.ids["sealed"]
    job_id = env.db.enqueue_job(JobKind.VERIFY, pid, Trigger.MANUAL, 110, NOW, bypass_hours=True)
    env.db.set_job_state(job_id, JobState.DONE, NOW)
    text = re.sub(r"\s+", " ", env.client.get("/fragments/activity").text)
    assert "Verified 2025-01_CLIENTE-SELLADO" in text
    assert "Verify now 2025-01_CLIENTE-SELLADO" not in text


def test_retire_works_without_javascript(env: Env) -> None:
    pid = _make_missing(env)
    page = env.client.get("/").text
    assert f'<noscript><a class="btn small quiet" href="/projects/{pid}/history.zip"' in page
    assert f'action="/projects/{pid}/retire?from=inbox"><button type="submit"' in page
