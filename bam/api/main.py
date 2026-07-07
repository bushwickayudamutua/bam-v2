"""FastAPI application factory wiring all routers (contract: bam/api).

``create_app`` also calls ``init_db`` eagerly so the app works with plain
``TestClient(create_app())`` (no lifespan context); the lifespan hook covers
normal server startup, where the engine may be configured later.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from bam.api.routes import (
    automations,
    browse,
    catalog,
    checkin,
    distros,
    intake,
    jobs,
    metrics,
    outreach,
)
from bam.db import init_db


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    """Build the BAM API with all routers registered."""
    app = FastAPI(title="BAM Mutual Aid System V2", lifespan=_lifespan)
    init_db()
    app.include_router(intake.router)
    app.include_router(outreach.router)
    app.include_router(checkin.router)
    app.include_router(distros.router)
    app.include_router(jobs.router)
    app.include_router(metrics.router)
    app.include_router(catalog.router)
    app.include_router(automations.router)
    app.include_router(browse.router)

    # Serve the operator console. Mounted LAST so it never shadows API routes;
    # "/" redirects into the mounted app.
    web_dir = Path(__file__).resolve().parent.parent / "web"

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse("/app/")

    app.mount("/app", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app


app = create_app()
