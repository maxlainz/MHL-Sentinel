"""EventBus: thread-safe publish into bounded asyncio queues (hito 2)."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from mhl_sentinel.clock import utcnow
from mhl_sentinel.db import Database
from mhl_sentinel.events import Event, EventBus
from mhl_sentinel.models import JobKind, Trigger


def test_publish_from_another_thread_reaches_subscribers() -> None:
    asyncio.run(_test_publish_from_another_thread_reaches_subscribers())


async def _test_publish_from_another_thread_reaches_subscribers() -> None:
    bus = EventBus()
    bus.bind(asyncio.get_running_loop())
    q1, q2 = bus.subscribe(), bus.subscribe()

    def worker() -> None:
        for i in range(3):
            bus.publish("job.progress", {"files_done": i})

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    got = [await asyncio.wait_for(q1.get(), 1) for _ in range(3)]
    assert [e.payload["files_done"] for e in got] == [0, 1, 2]
    assert all(
        isinstance(e, Event) and e.kind == "job.progress" and e.ts.endswith("Z") for e in got
    )
    assert (await asyncio.wait_for(q2.get(), 1)).payload == {"files_done": 0}


def test_publish_on_the_loop_thread_is_immediate_and_unsubscribe_stops() -> None:
    asyncio.run(_test_publish_on_the_loop_thread_is_immediate_and_unsubscribe_stops())


async def _test_publish_on_the_loop_thread_is_immediate_and_unsubscribe_stops() -> None:
    bus = EventBus()
    q = bus.subscribe()  # binds to the running loop
    assert bus.loop is asyncio.get_running_loop()
    bus.publish("log", {"level": "info", "msg": "hello"})
    assert q.qsize() == 1
    bus.unsubscribe(q)
    bus.publish("log", {"level": "info", "msg": "again"})
    await asyncio.sleep(0)
    assert q.qsize() == 1
    assert bus.subscriber_count == 0


def test_bounded_queue_drops_the_oldest() -> None:
    asyncio.run(_test_bounded_queue_drops_the_oldest())


async def _test_bounded_queue_drops_the_oldest() -> None:
    bus = EventBus(maxsize=3)
    q = bus.subscribe()
    for i in range(5):
        bus.publish("cycle.finished", {"n": i})
    assert [q.get_nowait().payload["n"] for _ in range(q.qsize())] == [2, 3, 4]


def test_publish_without_loop_or_after_close_does_not_raise() -> None:
    bus = EventBus()
    event = bus.publish("archive.ok", {"root": "/archive"})
    assert event.kind == "archive.ok"
    loop = asyncio.new_event_loop()
    bus.bind(loop)
    loop.close()
    bus.publish("archive.ok", {})


def test_job_id_also_writes_job_log(tmp_path: Path) -> None:
    with Database(tmp_path / "state.db") as db:
        job = db.enqueue_job(JobKind.SEAL, None, Trigger.MANUAL, 130, utcnow())
        bus = EventBus(db)
        bus.publish("log", {"level": "warning", "msg": "slow read"}, job_id=job)
        bus.publish("job.started", {"id": job}, job_id=job)
        entries = db.get_job_log(job)
    assert [(e.level, e.msg) for e in entries] == [
        ("warning", "slow read"),
        ("info", f'job.started {{"id": {job}}}'),
    ]


def test_maxsize_must_be_positive() -> None:
    with pytest.raises(ValueError):
        EventBus(maxsize=0)


def test_job_log_failure_does_not_break_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with Database(tmp_path / "state.db") as db:
        job = db.enqueue_job(JobKind.SEAL, None, Trigger.MANUAL, 130, utcnow())

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("disk full")

        monkeypatch.setattr(db, "log", boom)
        bus = EventBus(db)
        with caplog.at_level("ERROR", logger="mhl_sentinel.events"):
            event = bus.publish("job.started", {"id": job}, job_id=job)
        assert event.kind == "job.started"
        assert "could not write job_log" in caplog.text
        assert db.get_job_log(job) == []
