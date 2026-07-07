"""Read-only list/browse queries behind the console's parity views.

These back the Airtable Interfaces we mirror in the operator console:
Appointments ("Today's Appointments"), Look up (household browse), Furniture
(goods requests by category), and Social Services (social-service requests by
type, including the mesh install pipeline). All queries are read-only and
paginated; open-request counts are batched to avoid N+1.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from bam.models import (
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    local_date,
)
from bam.request_types import BY_KEY, label_for
from bam.schemas import (
    AppointmentRow,
    HouseholdPage,
    HouseholdRow,
    RequestPage,
    RequestRow,
    ServicePage,
    ServiceRow,
)

MAX_LIMIT = 200


def _category_keys(category: str) -> list[str]:
    return [key for key, rt in BY_KEY.items() if rt.category == category]


def _category_of(type_key: str) -> str | None:
    rt = BY_KEY.get(type_key)
    return rt.category if rt else None


def _clamp(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_LIMIT)), max(0, offset)


def _time_sort_key(appointment_time: str | None) -> int:
    """Minutes-since-midnight for an "11:00 AM" display string, so the queue
    sorts chronologically (a raw string sort puts "11:00 AM" before "9:00 AM").
    Unset/unparseable times sort last."""
    if not appointment_time:
        return 24 * 60 + 1
    text = appointment_time.strip().upper()
    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            parsed = dt.datetime.strptime(text, fmt)
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            continue
    return 24 * 60  # unparseable but present: before "no time set"


def _open_counts(session: Session, household_ids: list[int]) -> dict[int, int]:
    """Open goods + social-service request counts, keyed by household id."""
    counts: dict[int, int] = {}
    if not household_ids:
        return counts
    for model in (Request, SocialServiceRequest):
        rows = session.exec(
            select(model.household_id, func.count())
            .where(
                col(model.household_id).in_(household_ids),
                model.status == RequestStatus.OPEN,
            )
            .group_by(col(model.household_id))
        ).all()
        for hid, count in rows:
            counts[hid] = counts.get(hid, 0) + count
    return counts


def appointments(session: Session, on_date: dt.date | None = None) -> list[AppointmentRow]:
    """Households booked for a day (default: today, local business date),
    ordered by appointment time then name — the check-in queue."""
    day = on_date or local_date()
    households = session.exec(
        select(Household).where(Household.appointment_date == day)
    ).all()
    # appointment_time is a display string ("11:00 AM"); sort chronologically.
    households.sort(key=lambda h: (_time_sort_key(h.appointment_time), (h.name or "").lower()))
    counts = _open_counts(session, [h.id for h in households])
    return [
        AppointmentRow(
            household_id=h.id,
            name=h.name,
            phone_number=h.phone_number,
            languages=h.languages or [],
            appointment_time=h.appointment_time,
            appointment_status=h.appointment_status,
            open_request_count=counts.get(h.id, 0),
        )
        for h in households
    ]


def list_households(
    session: Session, query: str | None = None, limit: int = 50, offset: int = 0
) -> HouseholdPage:
    """Browse/search households by name or phone (Airtable "Look up")."""
    limit, offset = _clamp(limit, offset)
    where = []
    if query and query.strip():
        like = f"%{query.strip()}%"
        where.append(or_(col(Household.name).ilike(like), col(Household.phone_number).ilike(like)))
    total = session.scalar(select(func.count()).select_from(Household).where(*where)) or 0
    households = session.exec(
        select(Household)
        .where(*where)
        .order_by(col(Household.name))
        .offset(offset)
        .limit(limit)
    ).all()
    counts = _open_counts(session, [h.id for h in households])
    items = [
        HouseholdRow(
            id=h.id,
            name=h.name,
            phone_number=h.phone_number,
            languages=h.languages or [],
            appointment_date=h.appointment_date,
            appointment_time=h.appointment_time,
            appointment_status=h.appointment_status,
            open_request_count=counts.get(h.id, 0),
        )
        for h in households
    ]
    return HouseholdPage(items=items, total=total, limit=limit, offset=offset)


def list_requests(
    session: Session,
    category: str | None = None,
    type: str | None = None,
    status: RequestStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> RequestPage:
    """Goods requests joined to households (Airtable "Requests"/"Furniture
    Requests"). Filter by catalog category (e.g. ``furniture``), exact type,
    and/or status."""
    limit, offset = _clamp(limit, offset)
    where = []
    if category:
        where.append(col(Request.type).in_(_category_keys(category)))
    if type:
        where.append(Request.type == type)
    if status is not None:
        where.append(Request.status == status)
    total = (
        session.scalar(
            select(func.count()).select_from(Request).where(*where)
        )
        or 0
    )
    rows = session.exec(
        select(Request, Household)
        .join(Household, col(Request.household_id) == col(Household.id))
        .where(*where)
        .order_by(col(Request.request_opened_at).desc())
        .offset(offset)
        .limit(limit)
    ).all()
    items = [
        RequestRow(
            id=r.id,
            type=r.type,
            label=label_for(r.type),
            category=_category_of(r.type),
            status=r.status,
            request_opened_at=r.request_opened_at,
            household_id=h.id,
            household_name=h.name,
            household_phone=h.phone_number,
            address=r.address,
            geocode=r.geocode,
            bin=r.bin,
            address_accuracy=r.address_accuracy,
            notes=r.notes,
        )
        for r, h in rows
    ]
    return RequestPage(items=items, total=total, limit=limit, offset=offset)


def list_social_service_requests(
    session: Session,
    type: str | None = None,
    status: RequestStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> ServicePage:
    """Social-service requests joined to households (Airtable "Social Service
    Requests"/"MESH requests"). ``type=mesh_internet`` selects the mesh
    pipeline, surfacing mesh_status/bin/address_accuracy."""
    limit, offset = _clamp(limit, offset)
    where = []
    if type:
        where.append(SocialServiceRequest.type == type)
    if status is not None:
        where.append(SocialServiceRequest.status == status)
    total = (
        session.scalar(
            select(func.count()).select_from(SocialServiceRequest).where(*where)
        )
        or 0
    )
    rows = session.exec(
        select(SocialServiceRequest, Household)
        .join(Household, col(SocialServiceRequest.household_id) == col(Household.id))
        .where(*where)
        .order_by(col(SocialServiceRequest.request_opened_at).desc())
        .offset(offset)
        .limit(limit)
    ).all()
    items = [
        ServiceRow(
            id=r.id,
            type=r.type,
            label=label_for(r.type),
            status=r.status,
            request_opened_at=r.request_opened_at,
            household_id=h.id,
            household_name=h.name,
            household_phone=h.phone_number,
            mesh_status=r.mesh_status,
            bin=r.bin,
            address_accuracy=r.address_accuracy,
            internet_access=r.internet_access or [],
            notes=r.notes,
        )
        for r, h in rows
    ]
    return ServicePage(items=items, total=total, limit=limit, offset=offset)
