"""web/: pages, buttons through ``sealer.request_*``, settings form, health and JSON (hito 3).

The hito 2 objects (supervisor, bus, settings holder) are fakes that satisfy the Protocols of
``web/deps.py``; the database is real (tmp).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, JobRow, ReviewItem, SealedFile, VerifyResult
from mhl_sentinel.models import ChangeKind, FileStat, JobKind, JobState, ProjectState, Trigger
from mhl_sentinel.web import create_app
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

    def notify_job_queued(self) -> None:
        self.notified = getattr(self, "notified", 0) + 1

    def request_scan_now(self) -> None:
        self.scans += 1

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


def test_main_page_lists_projects_with_badges_and_counters(env: Env) -> None:
    r = env.client.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'sse-connect="/events"' in html
    assert "sse:project.state" in html and "sse:cycle.finished" in html
    assert "Archive: <strong>OK</strong>" in html
    assert "last round 23:14" in html  # 21:14 UTC shown in Europe/Madrid
    counters = re.sub(r"\s+", " ", html)
    assert "<strong>3</strong> projects" in counters  # ignored ones are not counted
    assert "<strong>1</strong> need review" in counters
    assert "<strong>1</strong> without manifest" in counters
    lights = re.findall(r'<li data-state="(\w+)">\s*<span class="light (\w+)"', html)
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
    assert re.search(r"idle window open until (\w{3} )?(\d{4}-\d\d-\d\d )?09:00", header)


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
    assert re.search(r"<tr><td>1</td><td>\d{4}-\d\d-\d\d \d\d:\d\d</td><td>1</td></tr>", r.text)
    assert "1 generation" in r.text
    (project / "ascmhl" / "ascmhl_chain.xml").write_text("broken")
    r = env.client.get(f"/projects/{env.ids['sealed']}")
    assert r.status_code == 200 and "could not be read" in r.text
