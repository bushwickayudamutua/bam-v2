"""Job-trigger routes for the spec section 5 scheduled functions."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from bam.db import get_session
from bam.schemas import ExpirationReport, ScrubReport
from bam.services.expiration import expire_stale_requests
from bam.services.metrics import update_website_request_data
from bam.services.privacy import scrub_expired_pii

router = APIRouter()


@router.post("/jobs/expire", response_model=ExpirationReport)
def run_expire(session: Session = Depends(get_session)) -> ExpirationReport:
    """Time out stale open requests (spec 2, 4, 6.1 step 7)."""
    return expire_stale_requests(session)


@router.post("/jobs/website-data")
def run_website_data(session: Session = Depends(get_session)) -> dict:
    """Write and return the website request-count JSON (``UpdateWebsiteRequestData``)."""
    return update_website_request_data(session)


@router.post("/jobs/scrub-pii", response_model=ScrubReport)
def run_scrub_pii(session: Session = Depends(get_session)) -> ScrubReport:
    """Scrub PII whose retention window has expired (privacy goal)."""
    return scrub_expired_pii(session)
