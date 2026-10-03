"""``serve`` wiring: logging, fallback app, supervisor lifespan, graceful signals."""

from __future__ import annotations

import importlib
import logging
import signal
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database
from mhl_sentinel.events import EventBus
from mhl_sentinel.server import (
    GRACEFUL_HTTP_SECONDS,
    GracefulServer,
    attach_supervisor,
    build_app,
    configure_logging,
    make_server,
)
from mhl_sentinel.settings_ref import SettingsRef
from mhl_sentinel.supervisor import Supervisor


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).propagate = True


@pytest.fixture
def parts(tmp_path: Path) -> Iterator[tuple[Database, SettingsRef, Supervisor, EventBus]]:
    settings = Settings(archive_root=tmp_path / "archive", config_dir=tmp_path / "config")
    with Database(settings.db_path) as db:
        ref = SettingsRef(settings)
        bus = EventBus(db)
        yield db, ref, Supervisor(db, ref, bus), bus


class FakeSupervisor:
    """Records the order in which the lifespan starts and stops it."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start(self) -> None:
        self.events.append("supervisor.start")

    async def stop(self) -> None:
        self.events.append("supervisor.stop")


def app_with_lifespan(events: list[str], *, fail: bool = False) -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[dict[str, str]]:
        events.append("app.startup")
        if fail:
            raise RuntimeError("startup failed")
        try:
            yield {"k": "v"}
        finally:
            events.append("app.shutdown")

    app = FastAPI(lifespan=lifespan)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "ok"}

    return app


# -- configure_logging ---------------------------------------------------------------------------


@pytest.mark.usefixtures("restore_logging")
def test_configure_logging_replaces_handlers_and_sets_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = logging.getLogger()
    root.addHandler(logging.NullHandler())
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn").addHandler(logging.NullHandler())

    configure_logging("warning")

    assert len(root.handlers) == 1
    (handler,) = root.handlers
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stdout
    assert root.level == logging.WARNING
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        assert uv.handlers == [] and uv.propagate is True

    logging.getLogger("mhl_sentinel.test").warning("hello %s", "world")
    logging.getLogger("mhl_sentinel.test").info("hidden")
    out = capsys.readouterr().out
    assert "WARNING" in out and "mhl_sentinel.test: hello world" in out
    assert "hidden" not in out


@pytest.mark.usefixtures("restore_logging")
def test_configure_logging_accepts_any_case() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


# -- build_app -----------------------------------------------------------------------------------


def test_build_app_uses_the_gui_factory_when_importable(
    parts: tuple[Database, SettingsRef, Supervisor, EventBus], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, ref, supervisor, bus = parts
    calls: list[tuple[Any, ...]] = []
    sentinel = object()
    fake = ModuleType("mhl_sentinel.web.app")

    def create_app(*args: Any) -> object:
        calls.append(args)
        return sentinel

    fake.create_app = create_app  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mhl_sentinel.web.app", fake)
    assert build_app(db, ref, supervisor, bus) is sentinel
    assert calls == [(db, ref, supervisor, bus)]


def test_build_app_falls_back_to_healthz_only_when_gui_is_missing(
    parts: tuple[Database, SettingsRef, Supervisor, EventBus],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db, ref, supervisor, bus = parts

    def broken(name: str) -> ModuleType:
        raise ImportError(f"no module {name}")

    monkeypatch.setattr(importlib, "import_module", broken)
    with caplog.at_level(logging.WARNING, logger="mhl_sentinel.server"):
        app = build_app(db, ref, supervisor, bus)
    assert "web GUI unavailable (ImportError" in caplog.text
    client = TestClient(app)
    assert client.get("/healthz").status_code == 503  # archive not checked yet
    assert client.get("/").status_code == 404


def test_build_app_falls_back_on_half_written_module(
    parts: tuple[Database, SettingsRef, Supervisor, EventBus], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, ref, supervisor, bus = parts
    monkeypatch.setitem(sys.modules, "mhl_sentinel.web.app", ModuleType("mhl_sentinel.web.app"))
    app = build_app(db, ref, supervisor, bus)  # module without create_app → AttributeError
    assert TestClient(app).get("/").status_code == 404


def test_build_app_real_gui_serves_healthz(
    parts: tuple[Database, SettingsRef, Supervisor, EventBus],
) -> None:
    db, ref, supervisor, bus = parts
    app = build_app(db, ref, supervisor, bus)
    assert TestClient(app).get("/healthz").status_code in (200, 503)


# -- attach_supervisor ---------------------------------------------------------------------------


def test_attach_supervisor_starts_first_and_stops_last() -> None:
    events: list[str] = []
    app = attach_supervisor(app_with_lifespan(events), cast(Supervisor, FakeSupervisor(events)))
    with TestClient(app) as client:
        assert client.get("/ping").json() == {"pong": "ok"}
        assert events == ["supervisor.start", "app.startup"]
    assert events == ["supervisor.start", "app.startup", "app.shutdown", "supervisor.stop"]


def test_attach_supervisor_stops_supervisor_when_app_startup_fails() -> None:
    events: list[str] = []
    app = attach_supervisor(
        app_with_lifespan(events, fail=True), cast(Supervisor, FakeSupervisor(events))
    )
    with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
        raise AssertionError("startup must fail before the body runs")
    assert events == ["supervisor.start", "app.startup", "supervisor.stop"]


# -- GracefulServer ------------------------------------------------------------------------------


def make_graceful(on_stop: Any = None) -> GracefulServer:
    import uvicorn

    return GracefulServer(uvicorn.Config(FastAPI(), log_config=None), on_stop)


def test_on_signal_requests_exit_and_calls_on_stop() -> None:
    stops: list[int] = []
    srv = make_graceful(lambda: stops.append(1))
    srv._on_signal(signal.SIGTERM, None)
    assert srv.should_exit is True and srv.force_exit is False
    assert stops == [1]
    srv._on_signal(signal.SIGTERM, None)  # a second SIGTERM does not force
    assert srv.force_exit is False and stops == [1, 1]


def test_second_sigint_forces_exit() -> None:
    srv = make_graceful()
    srv._on_signal(signal.SIGINT, None)
    assert srv.should_exit is True and srv.force_exit is False
    srv._on_signal(signal.SIGINT, None)
    assert srv.force_exit is True


def test_capture_signals_installs_and_restores_handlers_in_main_thread() -> None:
    srv = make_graceful()
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    with srv.capture_signals():
        for sig in (signal.SIGINT, signal.SIGTERM):
            installed = signal.getsignal(sig)
            assert getattr(installed, "__func__", None) is GracefulServer._on_signal
    assert {s: signal.getsignal(s) for s in before} == before


def test_capture_signals_restores_handlers_after_error() -> None:
    srv = make_graceful()
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(RuntimeError, match="boom"), srv.capture_signals():
        raise RuntimeError("boom")
    assert signal.getsignal(signal.SIGTERM) == before


def test_capture_signals_is_a_noop_outside_the_main_thread() -> None:
    srv = make_graceful()
    before = signal.getsignal(signal.SIGTERM)
    seen: list[object] = []

    def run() -> None:
        with srv.capture_signals():
            seen.append(signal.getsignal(signal.SIGTERM))

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(5)
    assert seen == [before]


def test_real_sigterm_in_context_triggers_clean_shutdown() -> None:
    stops: list[int] = []
    srv = make_graceful(lambda: stops.append(1))
    with srv.capture_signals():
        signal.raise_signal(signal.SIGTERM)
    assert srv.should_exit is True and stops == [1]


# -- make_server ---------------------------------------------------------------------------------


def test_make_server_applies_settings(tmp_path: Path) -> None:
    settings = Settings(archive_root=tmp_path, config_dir=tmp_path, port=8123, log_level="warning")
    stops: list[int] = []
    srv = make_server(FastAPI(), settings, host="127.0.0.1", on_stop=lambda: stops.append(1))
    assert isinstance(srv, GracefulServer)
    cfg = srv.config
    assert (cfg.host, cfg.port) == ("127.0.0.1", 8123)
    assert cfg.timeout_graceful_shutdown == GRACEFUL_HTTP_SECONDS
    assert cfg.log_config is None and cfg.lifespan == "on"
    assert cfg.log_level == "warning"
    assert srv.on_stop is not None
    srv.on_stop()
    assert stops == [1]


def test_make_server_defaults(tmp_path: Path) -> None:
    srv = make_server(FastAPI(), Settings(archive_root=tmp_path, config_dir=tmp_path))
    assert srv.config.host == "0.0.0.0"
    assert srv.on_stop is None
