"""Metrics services (spec section 5 + the Fulfilled Request Count table).

``FulfilledRequestCount`` is normalized to one row per (date, request type);
``increment_fulfilled_count`` upserts those rows as check-in marks requests
delivered. ``update_website_request_data`` is the hourly
``UpdateWebsiteRequestData`` cron job that publishes open request counts to
the website JSON.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, select

from bam.config import settings
from bam.models import (
    FulfilledRequestCount,
    Request,
    RequestStatus,
    SocialServiceRequest,
    utcnow,
)
from bam.request_types import label_for


def increment_fulfilled_count(
    session: Session,
    on_date: date,
    request_type: str,
    n: int = 1,
    commit: bool = True,
) -> FulfilledRequestCount:
    """Upsert the (date, request type) row, adding ``n`` to its count.

    ``commit=False`` lets a composing service (check-in fulfillment) commit
    once at the end of its own transaction.
    """
    row = session.exec(
        select(FulfilledRequestCount).where(
            FulfilledRequestCount.date == on_date,
            FulfilledRequestCount.request_type == request_type,
        )
    ).first()
    if row is None:
        row = FulfilledRequestCount(date=on_date, request_type=request_type, count=n)
    else:
        row.count += n
    session.add(row)
    if commit:
        session.commit()
        session.refresh(row)
    return row


def open_request_counts(session: Session, now: datetime | None = None) -> dict:
    """Open request counts per type, goods and social services combined.

    Shape: ``{"generated_at": iso-utc, "counts": [{"type", "label", "count"},
    ...]}`` sorted by count descending, then type key.
    """
    now = now or utcnow()
    totals: dict[str, int] = {}
    for model in (Request, SocialServiceRequest):
        rows = session.exec(
            select(model.type, func.count())
            .where(model.status == RequestStatus.OPEN)
            .group_by(model.type)
        ).all()
        for type_key, count in rows:
            totals[type_key] = totals.get(type_key, 0) + count
    counts = [
        {"type": key, "label": label_for(key), "count": n}
        for key, n in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {"generated_at": now.isoformat(), "counts": counts}


def update_website_request_data(
    session: Session,
    path: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Write open request counts as JSON to ``path`` (the hourly cron job)."""
    data = open_request_counts(session, now=now)
    target = Path(path or settings.website_data_path)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def fulfilled_counts(
    session: Session,
    start: date | None = None,
    end: date | None = None,
) -> list[FulfilledRequestCount]:
    """Fulfilled counts, optionally bounded by ``start``/``end`` (inclusive)."""
    stmt = select(FulfilledRequestCount)
    if start is not None:
        stmt = stmt.where(FulfilledRequestCount.date >= start)
    if end is not None:
        stmt = stmt.where(FulfilledRequestCount.date <= end)
    stmt = stmt.order_by(FulfilledRequestCount.date, FulfilledRequestCount.request_type)
    return list(session.exec(stmt).all())
