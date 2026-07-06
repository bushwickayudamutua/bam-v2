"""Check-in routes (spec 6.3): lookup, check-in, and fulfillment."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from bam.api.routes import value_error_to_http
from bam.db import get_session
from bam.models import Household, Request
from bam.schemas import (
    CheckinView,
    HouseholdMatch,
    HouseholdOut,
    RequestOut,
    SocialServiceRequestOut,
)
from bam.services import checkin

router = APIRouter()


class FulfillIn(BaseModel):
    request_ids: list[int] = []
    social_service_request_ids: list[int] = []


class FulfillOut(BaseModel):
    requests: list[RequestOut] = []
    social_service_requests: list[SocialServiceRequestOut] = []


@router.get("/households/lookup", response_model=CheckinView)
def lookup_household(phone: str, session: Session = Depends(get_session)) -> CheckinView:
    """Find a household by phone with its open requests (spec 6.3 steps 2-3)."""
    view = checkin.lookup_by_phone(session, phone)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No household with phone {phone!r}")
    return view


@router.get("/households/search", response_model=list[HouseholdMatch])
def search_households(
    name: str, session: Session = Depends(get_session)
) -> list[HouseholdMatch]:
    """Name search for check-in when a recipient arrives without their phone
    (spec journey step 5: "check in via phone number/name")."""
    return checkin.search_by_name(session, name)


@router.get("/households/{household_id}", response_model=CheckinView)
def household_view(
    household_id: int, session: Session = Depends(get_session)
) -> CheckinView:
    """CheckinView by id — the second half of a name-search check-in."""
    try:
        return checkin.view_for_household(session, household_id)
    except ValueError as exc:
        raise value_error_to_http(exc) from exc


@router.post("/households/{household_id}/checkin", response_model=HouseholdOut)
def check_in_household(
    household_id: int, session: Session = Depends(get_session)
) -> Household:
    """Mark the household checked in (spec 6.3 step 4 precursor)."""
    try:
        return checkin.check_in(session, household_id)
    except ValueError as exc:
        raise value_error_to_http(exc) from exc


@router.post("/requests/fulfill", response_model=FulfillOut)
def fulfill_requests(
    payload: FulfillIn, session: Session = Depends(get_session)
) -> FulfillOut:
    """Mark requests Delivered and record fulfilled counts (spec 6.3 step 4)."""
    try:
        updated = checkin.fulfill_requests(
            session,
            request_ids=payload.request_ids,
            social_service_request_ids=payload.social_service_request_ids,
        )
    except ValueError as exc:
        raise value_error_to_http(exc) from exc
    return FulfillOut(
        requests=[RequestOut.model_validate(obj) for obj in updated if isinstance(obj, Request)],
        social_service_requests=[
            SocialServiceRequestOut.model_validate(obj)
            for obj in updated
            if not isinstance(obj, Request)
        ],
    )
