"""Fulfillment analytics (parity with analyze_fulfilled_requests).

Production replays S3 snapshots to compute fulfilled-vs-open trends over
time. We compute the same shape directly from the live data plus the
Fulfilled Request Count history: totals per type, per-day fulfilled counts,
and the current open backlog.
"""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, func, select

from bam.models import (
    FulfilledRequestCount,
    Request,
    RequestStatus,
    SocialServiceRequest,
)
from bam.request_types import label_for
from bam.schemas import AnalyticsReport


def analyze_fulfilled_requests(
    session: Session,
    start: date | None = None,
    end: date | None = None,
) -> AnalyticsReport:
    """Fulfilled totals per type + per day, and the current open backlog."""
    stmt = select(FulfilledRequestCount)
    if start is not None:
        stmt = stmt.where(FulfilledRequestCount.date >= start)
    if end is not None:
        stmt = stmt.where(FulfilledRequestCount.date <= end)
    rows = session.exec(stmt).all()

    fulfilled_by_type: dict[str, int] = {}
    fulfilled_by_date: dict[str, int] = {}
    for row in rows:
        fulfilled_by_type[row.request_type] = (
            fulfilled_by_type.get(row.request_type, 0) + row.count
        )
        key = row.date.isoformat()
        fulfilled_by_date[key] = fulfilled_by_date.get(key, 0) + row.count

    open_by_type: dict[str, int] = {}
    for model in (Request, SocialServiceRequest):
        for type_key, count in session.exec(
            select(model.type, func.count())
            .where(model.status == RequestStatus.OPEN)
            .group_by(model.type)
        ).all():
            open_by_type[type_key] = open_by_type.get(type_key, 0) + count

    return AnalyticsReport(
        total_fulfilled=sum(fulfilled_by_type.values()),
        total_open=sum(open_by_type.values()),
        fulfilled_by_type=[
            {"type": t, "label": label_for(t), "count": c}
            for t, c in sorted(fulfilled_by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        fulfilled_by_date=[
            {"date": d, "count": c} for d, c in sorted(fulfilled_by_date.items())
        ],
        open_by_type=[
            {"type": t, "label": label_for(t), "count": c}
            for t, c in sorted(open_by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    )
