"""Database engine and session helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from bam.config import settings

_engine: Engine | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        connect_args = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(settings.database_url, connect_args=connect_args)
    return _engine


def set_engine(engine: Engine) -> None:
    """Override the engine (used by tests with in-memory SQLite)."""
    global _engine
    _engine = engine


def init_db(engine: Engine | None = None) -> None:
    import bam.models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine or get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session."""
    with Session(get_engine()) as session:
        yield session
