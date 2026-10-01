"""Wiring for ``mhl-sentinel serve`` (hito 2): one process, uvicorn + supervisor.

``build_app`` imports ``mhl_sentinel.web.app.create_app`` lazily and falls back to
:mod:`mhl_sentinel.web_fallback` (only ``/healthz``) if the GUI cannot be imported. The app's
lifespan is wrapped so that the supervisor starts before the first request and stops (current
block finished, job back to ``queued``) before the process exits.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import signal
import sys
import threading
from collections.abc import AsyncIterator, Callable, Generator
from types import FrameType
from typing import Any

import uvicorn

from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database
from mhl_sentinel.events import EventBus
from mhl_sentinel.settings_ref import SettingsRef
from mhl_sentinel.supervisor import Supervisor
from mhl_sentinel.web_fallback import create_fallback_app

log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
# Open SSE streams must not hold the shutdown. The supervisor starts stopping at the signal
# (``GracefulServer.on_stop``), in parallel with this, so the whole stop fits in the 10 s a
# container runtime gives between SIGTERM and SIGKILL.
GRACEFUL_HTTP_SECONDS = 3


def configure_logging(level: str) -> None:
    """Text logs to stdout (the container log), level from ``MHLS_LOG_LEVEL``."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


def build_app(
    db: Database, settings_ref: SettingsRef, supervisor: Supervisor, bus: EventBus
) -> Any:
    """``web.create_app(...)`` if importable, else the ``/healthz``-only fallback."""
    try:
        module = importlib.import_module("mhl_sentinel.web.app")
        create_app: Callable[..., Any] = module.create_app
    except Exception as exc:  # ImportError, or a half-written module
        log.warning("web GUI unavailable (%s: %s); serving /healthz only", type(exc).__name__, exc)
        return create_fallback_app(db, supervisor)
    return create_app(db, settings_ref, supervisor, bus)


def attach_supervisor(app: Any, supervisor: Supervisor) -> Any:
    """Wrap the app's lifespan: start the supervisor first, stop it last."""
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(asgi_app: Any) -> AsyncIterator[Any]:
        await supervisor.start()
        try:
            async with inner(asgi_app) as state:
                yield state
        finally:
            log.info("stopping supervisor")
            await supervisor.stop()
            log.info("supervisor stopped")

    app.router.lifespan_context = lifespan
    return app


class GracefulServer(uvicorn.Server):
    """``uvicorn.Server`` that turns SIGTERM/SIGINT into a clean shutdown and exit code 0.

    Stock uvicorn re-raises the captured signal after shutdown, which would end the process
    with 143/130; the container would read a clean stop as a crash. ``on_stop`` (signal-safe)
    runs at the first signal so the supervisor stops while uvicorn drains connections.
    """

    def __init__(self, config: uvicorn.Config, on_stop: Callable[[], None] | None = None) -> None:
        super().__init__(config)
        self.on_stop = on_stop

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        signals = (signal.SIGINT, signal.SIGTERM)
        previous = {sig: signal.signal(sig, self._on_signal) for sig in signals}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def _on_signal(self, sig: int, frame: FrameType | None) -> None:
        log.info("received %s: shutting down", signal.Signals(sig).name)
        if self.should_exit and sig == signal.SIGINT:
            self.force_exit = True
        self.should_exit = True
        if self.on_stop is not None:
            self.on_stop()


def make_server(
    app: Any,
    settings: Settings,
    *,
    host: str = "0.0.0.0",
    on_stop: Callable[[], None] | None = None,
) -> GracefulServer:
    config = uvicorn.Config(
        app,
        host=host,
        port=settings.port,
        log_config=None,
        log_level=settings.log_level,
        timeout_graceful_shutdown=GRACEFUL_HTTP_SECONDS,
        lifespan="on",
    )
    return GracefulServer(config, on_stop)
