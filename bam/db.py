"""Database engine and session helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import inspect, text
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

    engine = engine or get_engine()
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Lightweight forward migration: ``ADD COLUMN`` for any model column
    absent from an existing table.

    ``create_all`` never alters existing tables, so a database created by an
    earlier version is missing columns added since (e.g. the geocoding /
    mesh-status fields). All new columns are nullable, so a plain
    ``ALTER TABLE ADD COLUMN`` is safe and idempotent. SQLite and Postgres
    both support this.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    dialect = engine.dialect.name
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}')
                )


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session."""
    with Session(get_engine()) as session:
        yield session
