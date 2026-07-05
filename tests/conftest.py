"""Shared pytest fixtures (CONTRACT.md "Tests").

Every test gets a fresh in-memory SQLite engine wired through
``bam.db.set_engine`` so services, the FastAPI app, and factories all hit the
same isolated database. ``FIXED_NOW`` / ``days_ago`` support the frozen-time
pattern used by expiration, outreach, and privacy tests.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

import bam.db
from bam.models import Household, Request
from bam.sms.console import ConsoleSMSProvider
from bam.validation import hash_phone

FIXED_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def days_ago(n: int) -> datetime:
    """A timezone-aware datetime ``n`` days before ``FIXED_NOW``."""
    return FIXED_NOW - timedelta(days=n)


_phone_counter = itertools.count(100)


@pytest.fixture(autouse=True)
def engine() -> Iterator[Engine]:
    """Fresh in-memory engine per test, installed via ``bam.db.set_engine``."""
    previous = bam.db._engine
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    bam.db.set_engine(test_engine)
    bam.db.init_db(test_engine)
    yield test_engine
    bam.db.set_engine(previous)
    test_engine.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(engine: Engine) -> Iterator[Any]:
    """TestClient against the app, created after ``set_engine`` so
    ``bam.db.get_session`` resolves to the test database."""
    from fastapi.testclient import TestClient

    from bam.api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def sms() -> ConsoleSMSProvider:
    return ConsoleSMSProvider()


class RecordingSleeper:
    """Fake ``time.sleep`` recording each requested pause (spec 6.2 rate limit)."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture()
def no_sleep() -> RecordingSleeper:
    return RecordingSleeper()


@pytest.fixture()
def make_household():
    """Factory: ``make_household(session, **overrides)`` -> committed Household.

    Assigns a unique valid US phone number (+1718555XXXX) and matching
    ``phone_hash`` unless overridden.
    """

    def _make(session: Session, **overrides: Any) -> Household:
        n = next(_phone_counter)
        phone = overrides.pop("phone_number", f"+1718555{n:04d}")
        defaults: dict[str, Any] = {
            "name": f"Household {n}",
            "phone_number": phone,
            "phone_hash": hash_phone(phone) if phone else None,
            "languages": ["en"],
        }
        defaults.update(overrides)
        household = Household(**defaults)
        session.add(household)
        session.commit()
        session.refresh(household)
        return household

    return _make


@pytest.fixture()
def make_request():
    """Factory: ``make_request(session, household, type="soap", **overrides)``
    -> committed Request linked to ``household``."""

    def _make(
        session: Session,
        household: Household,
        type: str = "soap",
        **overrides: Any,
    ) -> Request:
        request = Request(household_id=household.id, type=type, **overrides)
        session.add(request)
        session.commit()
        session.refresh(request)
        return request

    return _make
