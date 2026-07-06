"""Metrics routes (spec 5 + the Fulfilled Request Count table)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlmodel import Session

from bam.db import get_session
from bam.request_types import label_for
from bam.schemas import FulfilledCountOut
from bam.services.metrics import fulfilled_counts, open_request_counts

router = APIRouter()


@router.get("/metrics/open-requests")
def get_open_requests(session: Session = Depends(get_session)) -> dict:
    """Open request counts per type, goods and social services combined."""
    return open_request_counts(session)


@router.get("/metrics/fulfilled", response_model=list[FulfilledCountOut])
def get_fulfilled(
    start: dt.date | None = None,
    end: dt.date | None = None,
    session: Session = Depends(get_session),
) -> list[FulfilledCountOut]:
    """Fulfilled counts per (date, type) — spec 2's "track fulfilled vs
    outstanding requests" read surface; dates inclusive."""
    rows = fulfilled_counts(session, start=start, end=end)
    return [
        FulfilledCountOut(
            date=row.date, type=row.request_type,
            label=label_for(row.request_type), count=row.count,
        )
        for row in rows
    ]
