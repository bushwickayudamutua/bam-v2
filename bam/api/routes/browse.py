"""Browse/list routes — parity with the Airtable Interfaces.

Read-only, paginated lists behind the console's Appointments, Look up,
Furniture, and Social Services views.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from bam.db import get_session
from bam.models import RequestStatus
from bam.schemas import (
    AppointmentRow,
    HouseholdPage,
    RequestPage,
    ServicePage,
)
from bam.services import browse

router = APIRouter()


@router.get("/appointments", response_model=list[AppointmentRow])
def list_appointments(
    date: dt.date | None = Query(default=None, description="Defaults to today (local)."),
    session: Session = Depends(get_session),
) -> list[AppointmentRow]:
    """Households booked for a day, ordered as a check-in queue."""
    return browse.appointments(session, date)


@router.get("/households", response_model=HouseholdPage)
def browse_households(
    query: str | None = Query(default=None, description="Match name or phone."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> HouseholdPage:
    """Browse/search all households (Airtable "Look up")."""
    return browse.list_households(session, query, limit, offset)


@router.get("/requests", response_model=RequestPage)
def browse_requests(
    category: str | None = Query(default=None, description="Catalog category, e.g. furniture."),
    type: str | None = Query(default=None, description="Exact request type key."),
    status: RequestStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> RequestPage:
    """Goods requests by category/type/status (Airtable "Requests"/"Furniture
    Requests")."""
    return browse.list_requests(session, category, type, status, limit, offset)


@router.get("/social-service-requests", response_model=ServicePage)
def browse_social_service_requests(
    type: str | None = Query(default=None, description="Exact type key, e.g. mesh_internet."),
    status: RequestStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ServicePage:
    """Social-service requests by type/status (Airtable "Social Service
    Requests"/"MESH requests")."""
    return browse.list_social_service_requests(session, type, status, limit, offset)
