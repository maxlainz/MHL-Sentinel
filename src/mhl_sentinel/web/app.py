"""FastAPI application factory (docs/arquitectura.md, "Hito 3"). No login (D35)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from mhl_sentinel import __version__
from mhl_sentinel.db import Database
from mhl_sentinel.web.deps import BusLike, SettingsRefLike, SupervisorLike, WebContext
from mhl_sentinel.web.routes import StrayCache, router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    db: Database,
    settings_ref: SettingsRefLike,
    supervisor: SupervisorLike,
    bus: BusLike,
) -> FastAPI:
    """Build the GUI over already-running hito 2 objects.

    The caller owns their lifecycle (``cli serve`` starts and stops the supervisor in the
    uvicorn ``lifespan``); this factory only wires routes, templates and static files.
    """
    app = FastAPI(
        title="MHL Sentinel",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.ctx = WebContext(db=db, settings_ref=settings_ref, supervisor=supervisor, bus=bus)
    app.state.strays = StrayCache()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)
    return app
