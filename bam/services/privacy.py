"""PII scrubbing (spec goal: hash sensitive data; background 8: PII is not
anonymized after fulfillment in V1 — V2 fixes that).

Three passes, all guarded by ``now`` so tests can freeze time:

1. Closed (Delivered/Timeout) goods and social service requests whose
   ``processing_date`` has passed lose their address fields and notes.
2. Households with no open requests of either kind and ``updated_at`` older
   than the retention window are anonymized: the phone hash is preserved (so
   a re-request from the same phone reconnects to its history) while the raw
   phone, name, email, and notes are nulled.
3. Processed form submissions older than the retention window lose their raw
   PII (contract spec-interpretation decision 1: rows are kept, PII cleared).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, col, select

from bam.config import settings
from bam.models import (
    FormSubmission,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    utcnow,
)
from bam.schemas import ScrubReport
from bam.validation import hash_phone

_CLOSED_STATUSES = (RequestStatus.DELIVERED, RequestStatus.TIMEOUT)
_REQUEST_PII_FIELDS = ("street_address", "city_state", "zip_code", "geocode", "address", "notes")
_SOCIAL_REQUEST_PII_FIELDS = ("street_address", "city_state", "zip_code", "address", "notes")
_SUBMISSION_PII_FIELDS = (
    "name",
    "phone_number",
    "email",
    "notes",
    "street_address",
    "city_state",
    "zip_code",
)


def _as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat naive datetimes as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _null_fields(obj: object, fields: tuple[str, ...]) -> bool:
    """Null the given fields; return True if anything actually changed."""
    changed = False
    for field in fields:
        if getattr(obj, field) is not None:
            setattr(obj, field, None)
            changed = True
    return changed


def scrub_expired_pii(
    session: Session,
    now: datetime | None = None,
    retention_days: int | None = None,
) -> ScrubReport:
    """Scrub PII whose retention window has expired; return the counts."""
    now = now or utcnow()
    if retention_days is None:
        retention_days = settings.pii_retention_days
    cutoff = now - timedelta(days=retention_days)
    report = ScrubReport()

    # Pass 1: closed requests past their processing date lose address + notes.
    for request in session.exec(
        select(Request).where(
            col(Request.status).in_(_CLOSED_STATUSES),
            col(Request.processing_date) < now.date(),
        )
    ).all():
        if _null_fields(request, _REQUEST_PII_FIELDS):
            request.updated_at = now
            session.add(request)
            report.requests_scrubbed += 1

    for social_request in session.exec(
        select(SocialServiceRequest).where(
            col(SocialServiceRequest.status).in_(_CLOSED_STATUSES),
            col(SocialServiceRequest.processing_date) < now.date(),
        )
    ).all():
        changed = _null_fields(social_request, _SOCIAL_REQUEST_PII_FIELDS)
        if social_request.internet_access:
            social_request.internet_access = []
            changed = True
        if changed:
            social_request.updated_at = now
            session.add(social_request)
            report.social_service_requests_scrubbed += 1

    # Pass 2: anonymize inactive households, keeping only the phone hash.
    open_household_ids = set(
        session.exec(
            select(Request.household_id).where(Request.status == RequestStatus.OPEN)
        ).all()
    ) | set(
        session.exec(
            select(SocialServiceRequest.household_id).where(
                SocialServiceRequest.status == RequestStatus.OPEN
            )
        ).all()
    )
    for household in session.exec(
        select(Household).where(col(Household.anonymized_at).is_(None))
    ).all():
        if household.id in open_household_ids:
            continue
        if _as_utc(household.updated_at) >= cutoff:
            continue
        if household.phone_number and not household.phone_hash:
            # Also hashes raw invalid-phone strings so reconnection stays possible.
            household.phone_hash = hash_phone(household.phone_number)
        _null_fields(
            household, ("phone_number", "name", "email", "email_error", "notes")
        )
        household.anonymized_at = now
        household.updated_at = now
        session.add(household)
        report.households_anonymized += 1

    # Pass 3: processed form submissions older than retention lose raw PII.
    for submission in session.exec(
        select(FormSubmission).where(
            col(FormSubmission.processed_at).is_not(None),
            col(FormSubmission.scrubbed_at).is_(None),
        )
    ).all():
        if _as_utc(submission.created_at) >= cutoff:
            continue
        _null_fields(submission, _SUBMISSION_PII_FIELDS)
        submission.scrubbed_at = now
        session.add(submission)
        report.submissions_scrubbed += 1

    session.commit()
    return report
