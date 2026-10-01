"""Minimal app used by ``serve`` when ``mhl_sentinel.web`` cannot be imported.

It only exposes ``GET /healthz`` with the contract semantics (docs/arquitectura.md, hito 3):
200 if the DB is writable and the archive answers, 503 otherwise; JSON
``{status, archive, db, version}``. The real GUI lives in ``web/``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from mhl_sentinel import __version__
from mhl_sentinel.db import Database
from mhl_sentinel.supervisor import Supervisor


def db_writable(db: Database) -> bool:
    try:
        with db.transaction():  # BEGIN IMMEDIATE takes the write lock
            pass
    except Exception:
        return False
    return True


def create_fallback_app(db: Database, supervisor: Supervisor) -> FastAPI:
    app = FastAPI(title="MHL Sentinel", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        archive = supervisor.status().archive_reachable
        writable = db_writable(db)
        ok = archive and writable
        body = {
            "status": "ok" if ok else "degraded",
            "archive": "ok" if archive else "unreachable",
            "db": "ok" if writable else "error",
            "version": __version__,
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    return app
