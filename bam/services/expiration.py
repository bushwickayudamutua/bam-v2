"""Auto-expiration of stale requests (spec sections 2, 4, and 6.1 step 7).

Open goods requests expire after their type's window (14 days standard,
30 days for pots & pans); open social service requests always use the
14-day window. Households with a currently Booked appointment are skipped —
someone already scheduled should not lose their request (contract rule 3).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from bam.models import (
    AppointmentStatus,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    apply_status_change,
    local_date,
    utcnow,
)
from bam.request_types import default_expiry_days, expiry_days_for
from bam.schemas import ExpirationReport


def _as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat naive datetimes as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def expire_stale_requests(
    session: Session, now: datetime | None = None
) -> ExpirationReport:
    """Time out every Open request whose expiry window has elapsed.

    The window is measured from ``request_opened_at``: a request expires when
    ``now - request_opened_at`` exceeds ``expiry_days_for(type)`` days (goods)
    or ``DEFAULT_EXPIRY_DAYS`` (social services). Status changes go through
    ``apply_status_change`` so ``processing_date`` stays correct.
    """
    now = now or utcnow()
    report = ExpirationReport()

    # Only a booking for today or later exempts a household: a dangling
    # Booked status from a distro whose no-show pass never ran must not make
    # its requests immortal (and thereby block PII anonymization).
    booked_household_ids = set(
        session.exec(
            select(Household.id).where(
                Household.appointment_status == AppointmentStatus.BOOKED,
                Household.appointment_date >= local_date(now),
            )
        ).all()
    )

    for request in session.exec(
        select(Request).where(Request.status == RequestStatus.OPEN)
    ).all():
        if request.household_id in booked_household_ids:
            continue
        window = timedelta(days=expiry_days_for(request.type))
        if now - _as_utc(request.request_opened_at) > window:
            apply_status_change(request, RequestStatus.TIMEOUT, now=now)
            session.add(request)
            report.timed_out_request_ids.append(request.id)

    social_window = timedelta(days=default_expiry_days())
    for social_request in session.exec(
        select(SocialServiceRequest).where(
            SocialServiceRequest.status == RequestStatus.OPEN
        )
    ).all():
        if social_request.household_id in booked_household_ids:
            continue
        if now - _as_utc(social_request.request_opened_at) > social_window:
            apply_status_change(social_request, RequestStatus.TIMEOUT, now=now)
            session.add(social_request)
            report.timed_out_social_service_request_ids.append(social_request.id)

    session.commit()
    return report
