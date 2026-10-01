"""Interfaces the web layer needs from hito 2 (docs/arquitectura.md, "Hito 2").

The GUI is coded against these ``Protocol``s, not against ``events``, ``settings_ref`` and
``supervisor`` directly: the real classes satisfy them structurally and the tests pass fakes.
Attributes are declared as read-only properties so frozen dataclasses also match.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database, JobRow


class EventLike(Protocol):
    """``events.Event(kind, payload, ts)``."""

    @property
    def kind(self) -> str: ...

    @property
    def payload(self) -> Mapping[str, Any]: ...

    @property
    def ts(self) -> datetime | str: ...


class BusLike(Protocol):
    """``events.EventBus``: only the SSE side is used here."""

    def subscribe(self) -> asyncio.Queue[Any]: ...

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None: ...


class SettingsRefLike(Protocol):
    """``settings_ref.SettingsRef``: mutable holder so *Save* reloads without a restart."""

    def get(self) -> Settings: ...

    def replace(self, new: Settings) -> None: ...


class StatusLike(Protocol):
    """``supervisor.SupervisorStatus``."""

    @property
    def working_now(self) -> bool: ...

    @property
    def next_change(self) -> datetime | None: ...

    @property
    def gate_open(self) -> bool: ...

    @property
    def current_job(self) -> JobRow | None: ...

    @property
    def last_cycle_at(self) -> datetime | str | None: ...

    @property
    def archive_reachable(self) -> bool: ...

    @property
    def queued_jobs(self) -> int: ...


class SupervisorLike(Protocol):
    """``supervisor.Supervisor``. ``request_scan_now`` may be sync or a coroutine function."""

    def request_scan_now(self) -> object: ...

    def notify_job_queued(self) -> None: ...

    def request_cancel(self, project_id: int) -> bool: ...

    def status(self) -> StatusLike: ...


@dataclass(slots=True)
class WebContext:
    """Everything a route needs; stored in ``app.state.ctx``."""

    db: Database
    settings_ref: SettingsRefLike
    supervisor: SupervisorLike
    bus: BusLike

    @property
    def settings(self) -> Settings:
        return self.settings_ref.get()
