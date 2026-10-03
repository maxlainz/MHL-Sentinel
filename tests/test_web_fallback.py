"""Fallback app used by ``serve`` when the GUI cannot be imported: only ``/healthz``."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mhl_sentinel import __version__
from mhl_sentinel.config import Settings
from mhl_sentinel.db import Database
from mhl_sentinel.events import EventBus
from mhl_sentinel.settings_ref import SettingsRef
from mhl_sentinel.supervisor import Supervisor
from mhl_sentinel.web_fallback import create_fallback_app, db_writable


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    with Database(tmp_path / "config" / "state.db") as database:
        yield database


@pytest.fixture
def supervisor(db: Database, tmp_path: Path) -> Supervisor:
    settings = Settings(archive_root=tmp_path / "archive", config_dir=tmp_path / "config")
    return Supervisor(db, SettingsRef(settings), EventBus(db))


def test_db_writable_true_on_open_database(db: Database) -> None:
    assert db_writable(db) is True


def test_db_writable_false_when_database_is_closed(tmp_path: Path) -> None:
    assert db_writable(Database(tmp_path / "never-opened.db")) is False


def test_healthz_ok(db: Database, supervisor: Supervisor) -> None:
    supervisor._archive_reachable = True
    response = TestClient(create_fallback_app(db, supervisor)).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "archive": "ok",
        "db": "ok",
        "version": __version__,
    }


def test_healthz_503_when_archive_unreachable(db: Database, supervisor: Supervisor) -> None:
    supervisor._archive_reachable = False
    response = TestClient(create_fallback_app(db, supervisor)).get("/healthz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "archive": "unreachable",
        "db": "ok",
        "version": __version__,
    }


def test_healthz_503_when_archive_never_checked(db: Database, supervisor: Supervisor) -> None:
    assert supervisor._archive_reachable is None
    response = TestClient(create_fallback_app(db, supervisor)).get("/healthz")
    assert response.status_code == 503
    assert response.json()["archive"] == "unreachable"


def test_healthz_503_when_db_not_writable(
    db: Database, supervisor: Supervisor, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor._archive_reachable = True

    def locked() -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "transaction", locked)
    response = TestClient(create_fallback_app(db, supervisor)).get("/healthz")
    assert response.status_code == 503
    body = response.json()
    assert (body["status"], body["archive"], body["db"]) == ("degraded", "ok", "error")


def test_only_healthz_is_exposed(db: Database, supervisor: Supervisor) -> None:
    client = TestClient(create_fallback_app(db, supervisor))
    assert client.get("/").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
