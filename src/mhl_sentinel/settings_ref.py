"""Mutable holder of the current :class:`Settings` (hito 2/3).

Shared by the supervisor and the web app: *Save* in Settings replaces the object here and the
supervisor reads it on its next tick, without a restart. ``Settings`` objects are treated as
immutable; replace them, never mutate them in place.
"""

from __future__ import annotations

import threading

from mhl_sentinel.config import Settings


class SettingsRef:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def replace(self, new: Settings) -> None:
        """Install ``new``; the supervisor picks it up on its next tick."""
        with self._lock:
            self._settings = new
