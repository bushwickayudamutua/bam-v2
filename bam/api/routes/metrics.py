"""Metrics routes (spec 5 + the Fulfilled Request Count table)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from bam.db import get_session
from bam.services.metrics import open_request_counts

router = APIRouter()


@router.get("/metrics/open-requests")
def get_open_requests(session: Session = Depends(get_session)) -> dict:
    """Open request counts per type, goods and social services combined."""
    return open_request_counts(session)
