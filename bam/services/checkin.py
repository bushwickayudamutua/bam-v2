"""Check-in services (spec section 6.3 + no-show sequence + A2/A3).

Covers the distribution-day flow: phone lookup, marking the household
checked in, fulfilling requests (which feeds the Fulfilled Request Count
metrics), and the end-of-event no-show pass that times out households after
their second missed appointment (spec interpretation rule 5).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlmodel import Session, select

from bam.config import settings
from bam.models import (
    AppointmentStatus,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    apply_status_change,
    utcnow,
)
from bam.schemas import (
    CheckinView,
    HouseholdOut,
    NoShowReport,
    RequestOut,
    SocialServiceRequestOut,
)
from bam.services.metrics import increment_fulfilled_count
from bam.validation import validate_phone


def lookup_by_phone(session: Session, phone: str) -> CheckinView | None:
    """Find a household by phone and return its open requests (6.3 step 2-3).

    The phone is normalized to E.164 for the lookup; if it cannot be
    normalized we fall back to an exact string match (households with
    ``invalid_phone_number`` keep their raw submitted value only when it
    parsed, so this catches legacy/edge rows).
    """
    validation = validate_phone(phone)
    lookup_value = validation.normalized if validation.normalized else phone
    household = session.exec(
        select(Household).where(Household.phone_number == lookup_value)
    ).first()
    if household is None:
        return None
    open_requests = session.exec(
        select(Request)
        .where(Request.household_id == household.id, Request.status == RequestStatus.OPEN)
        .order_by(Request.request_opened_at, Request.id)
    ).all()
    open_social = session.exec(
        select(SocialServiceRequest)
        .where(
            SocialServiceRequest.household_id == household.id,
            SocialServiceRequest.status == RequestStatus.OPEN,
        )
        .order_by(SocialServiceRequest.request_opened_at, SocialServiceRequest.id)
    ).all()
    return CheckinView(
        household=HouseholdOut.model_validate(household),
        open_requests=[RequestOut.model_validate(r) for r in open_requests],
        open_social_service_requests=[
            SocialServiceRequestOut.model_validate(s) for s in open_social
        ],
    )


def check_in(session: Session, household_id: int, now: datetime | None = None) -> Household:
    """Mark the household checked in (6.3 step 4 precursor).

    Resets ``missed_appointment_count`` per interpretation rule 5.
    """
    now = now or utcnow()
    household = session.get(Household, household_id)
    if household is None:
        raise ValueError(f"Unknown household id {household_id}")
    household.appointment_status = AppointmentStatus.CHECKED_IN
    household.last_attended = now.date()
    household.missed_appointment_count = 0
    household.updated_at = now
    session.add(household)
    session.commit()
    session.refresh(household)
    return household


def fulfill_requests(
    session: Session,
    request_ids: list[int] = (),
    social_service_request_ids: list[int] = (),
    now: datetime | None = None,
) -> list[Request | SocialServiceRequest]:
    """Mark requests Delivered (6.3 step 4) and record fulfilled counts.

    All ids are resolved before any mutation; unknown ids raise ``ValueError``.
    Returns the updated objects, goods requests first.
    """
    now = now or utcnow()
    requests: list[Request] = []
    missing: list[str] = []
    for request_id in request_ids:
        request = session.get(Request, request_id)
        if request is None:
            missing.append(f"request {request_id}")
        else:
            requests.append(request)
    social_requests: list[SocialServiceRequest] = []
    for social_id in social_service_request_ids:
        social = session.get(SocialServiceRequest, social_id)
        if social is None:
            missing.append(f"social service request {social_id}")
        else:
            social_requests.append(social)
    if missing:
        raise ValueError(f"Unknown ids: {', '.join(missing)}")

    updated: list[Request | SocialServiceRequest] = [*requests, *social_requests]
    for obj in updated:
        apply_status_change(obj, RequestStatus.DELIVERED, now=now)
        session.add(obj)
    for request in requests:
        increment_fulfilled_count(session, now.date(), request.type, commit=False)
    session.commit()
    for obj in updated:
        session.refresh(obj)
    return updated


def process_no_shows(
    session: Session,
    distro_date: date,
    now: datetime | None = None,
) -> NoShowReport:
    """End-of-distro no-show pass (6.3 no-show sequence, A2/A3).

    Every household still Booked for ``distro_date`` is marked Missed with the
    appointment cleared; once ``missed_appointment_count`` reaches
    ``settings.max_missed_appointments`` all its open goods and social service
    requests time out.
    """
    now = now or utcnow()
    households = session.exec(
        select(Household)
        .where(
            Household.appointment_date == distro_date,
            Household.appointment_status == AppointmentStatus.BOOKED,
        )
        .order_by(Household.id)
    ).all()
    report = NoShowReport()
    for household in households:
        household.appointment_status = AppointmentStatus.MISSED
        household.missed_appointment_count += 1
        household.appointment_date = None
        household.appointment_time = None
        household.updated_at = now
        session.add(household)
        report.missed_household_ids.append(household.id)
        if household.missed_appointment_count >= settings.max_missed_appointments:
            open_items: list[Request | SocialServiceRequest] = [
                *session.exec(
                    select(Request).where(
                        Request.household_id == household.id,
                        Request.status == RequestStatus.OPEN,
                    )
                ).all(),
                *session.exec(
                    select(SocialServiceRequest).where(
                        SocialServiceRequest.household_id == household.id,
                        SocialServiceRequest.status == RequestStatus.OPEN,
                    )
                ).all(),
            ]
            for item in open_items:
                apply_status_change(item, RequestStatus.TIMEOUT, now=now)
                session.add(item)
            report.timed_out_household_ids.append(household.id)
    session.commit()
    return report
