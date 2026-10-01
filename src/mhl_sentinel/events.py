"""Event bus: the single point where the supervisor tells the GUI (SSE) what happens (hito 2).

Contract (docs/arquitectura.md, "Hito 2"): ``publish(kind, payload, *, job_id=None)`` may be
called from any thread (the hasher thread above all); ``subscribe()`` / ``unsubscribe(q)`` are
called from the event loop (one queue per SSE client). Delivery always happens on the loop thread
through ``loop.call_soon_threadsafe``. Queues are bounded: a slow client loses its oldest events,
never blocks the hasher. Later this is also the hook for notifications (Apprise, D22).

Kinds: ``cycle.started|finished``, ``project.state`` (``{id, rel_path, state}``),
``job.started|progress|finished|failed``, ``archive.unreachable|ok``, ``log``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from mhl_sentinel.clock import utcnow, utcnow_iso
from mhl_sentinel.db import Database

log = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 1000


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=utcnow_iso)  # ISO-8601 UTC


class EventBus:
    """Thread-safe publish into asyncio queues owned by one event loop."""

    def __init__(self, db: Database | None = None, *, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._db = db
        self._maxsize = maxsize
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._lock = threading.Lock()

    # -- loop binding ------------------------------------------------------------------------

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the loop that owns the subscriber queues (call it at startup)."""
        with self._lock:
            self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    # -- subscribers (event loop thread) ----------------------------------------------------

    def subscribe(self) -> asyncio.Queue[Event]:
        """New bounded queue for one consumer. Binds the bus to the running loop if unbound."""
        if self._loop is None:
            self.bind(asyncio.get_running_loop())
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # -- publish (any thread) ----------------------------------------------------------------

    def publish(self, kind: str, payload: dict[str, Any], *, job_id: int | None = None) -> Event:
        """Send ``Event(kind, payload)`` to every subscriber; with ``job_id`` also to ``job_log``.

        Never raises because of a subscriber or a closed loop: the caller is the hasher thread.
        """
        event = Event(kind, dict(payload))
        if self._db is not None and job_id is not None:
            level = str(payload.get("level", "info"))
            msg = payload.get("msg")
            text = str(msg) if msg is not None else f"{kind} {json.dumps(payload, default=str)}"
            try:
                self._db.log(job_id, level, text, utcnow())
            except Exception:  # the bus must not break the publisher
                log.exception("could not write job_log for job %d", job_id)
        log.debug("event %s %s", kind, payload)
        loop = self._loop
        if loop is None or loop.is_closed():
            return event
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(event)
        else:
            with contextlib.suppress(RuntimeError):  # loop closed after the check
                loop.call_soon_threadsafe(self._deliver, event)
        return event

    def _deliver(self, event: Event) -> None:
        with self._lock:
            queues = list(self._subscribers)
        for queue in queues:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()  # drop the oldest
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
