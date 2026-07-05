"""FastAPI application factory wiring all routers (contract: bam/api).

``create_app`` also calls ``init_db`` eagerly so the app works with plain
``TestClient(create_app())`` (no lifespan context); the lifespan hook covers
normal server startup, where the engine may be configured later.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from bam.api.routes import checkin, distros, intake, jobs, metrics, outreach
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
    return app


app = create_app()
