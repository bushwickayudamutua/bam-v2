"""Intake processing (spec section 6.1).

Turns raw form submissions into ``Household``, ``Request`` and
``SocialServiceRequest`` rows: phone/email validation, household matching
(including anonymized-household reconnection via ``phone_hash``),
request-type normalization with open-request dedup, and furniture delivery
addresses. Per interpretation decision 1, submissions are kept and linked to
the household instead of deleted (spec 6.1 step 6); the privacy scrub clears
their PII later.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, col, select

from bam.errors import NotFoundError
from bam.models import (
    FormSubmission,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    utcnow,
)
from bam.request_types import BY_KEY, is_social_service, normalize_type
from bam.schemas import FormSubmissionIn, IntakeResult
from bam.validation import hash_phone, validate_email_address, validate_phone

BED_DETAIL_TYPES = ("bed", "furniture")


def submit_form(
    session: Session, payload: FormSubmissionIn, now: datetime | None = None
) -> FormSubmission:
    """Store the raw submission (spec 6.1 steps 1-2)."""
    now = now or utcnow()
    submission = FormSubmission(
        name=payload.name,
        phone_number=payload.phone_number,
        email=payload.email,
        languages=list(payload.languages),
        request_types=list(payload.request_types),
        furniture_items=list(payload.furniture_items),
        bed_details=list(payload.bed_details),
        furniture_acknowledgement=payload.furniture_acknowledgement,
        kitchen_items=list(payload.kitchen_items),
        social_service_requests=list(payload.social_service_requests),
        internet_access=list(payload.internet_access),
        roof_accessible=payload.roof_accessible,
        notes=payload.notes,
        street_address=payload.street_address,
        city_state=payload.city_state,
        zip_code=payload.zip_code,
        created_at=now,
    )
    session.add(submission)
    session.commit()
    session.refresh(submission)
    return submission


def process_submission(
    session: Session, submission_id: int, now: datetime | None = None
) -> IntakeResult:
    """Process one stored submission (spec 6.1 steps 3-7).

    Creates or updates the household, links the submission, creates one Open
    ``Request`` per normalized goods type and one Open ``SocialServiceRequest``
    per normalized social-service type (skipping types the household already
    has Open — interpretation decision 2), and stamps ``processed_at``.
    """
    now = now or utcnow()
    submission = session.get(FormSubmission, submission_id)
    if submission is None:
        raise NotFoundError(f"FormSubmission {submission_id} not found")
    if submission.processed_at is not None and submission.household_id is not None:
        # Idempotency guard: re-processing (an operator re-run, a retried
        # request) must not create duplicate households or requests.
        return IntakeResult(
            submission_id=submission_id,
            household_id=submission.household_id,
            created_household=False,
            phone_valid=validate_phone(submission.phone_number).valid,
            already_processed=True,
        )

    phone = validate_phone(submission.phone_number)
    email = validate_email_address(submission.email)
    raw_phone = (submission.phone_number or "").strip()

    household: Household | None = None
    if phone.valid and phone.normalized:
        household = session.exec(
            select(Household).where(Household.phone_number == phone.normalized)
        ).first()
        if household is None:
            # Anonymized household reconnection: the scrub kept phone_hash so
            # a re-request from the same phone restores the history.
            household = session.exec(
                select(Household).where(Household.phone_hash == hash_phone(phone.normalized))
            ).first()
            if household is not None:
                household.phone_number = phone.normalized
                household.anonymized_at = None
    else:
        # Invalid phone: the household stores no phone. Dedup matches the
        # hash of the raw string (set at creation, preserved by the PII
        # scrub so reconnection survives anonymization), then falls back to
        # an exact raw-string match against prior submissions' phone
        # (CONTRACT intake bullet 2).
        if raw_phone:
            household = session.exec(
                select(Household).where(Household.phone_hash == hash_phone(raw_phone))
            ).first()
            if household is not None:
                household.anonymized_at = None
        if household is None:
            prior = session.exec(
                select(FormSubmission)
                .where(
                    FormSubmission.phone_number == submission.phone_number,
                    col(FormSubmission.household_id).is_not(None),
                    FormSubmission.id != submission.id,
                )
                .order_by(col(FormSubmission.id))
            ).first()
            if prior is not None:
                household = session.get(Household, prior.household_id)

    created_household = household is None
    if household is None:
        # Invalid phones store no phone_number but do get a hash of the raw
        # string so dedup and post-scrub reconnection stay possible.
        hash_source = phone.normalized if phone.valid else (raw_phone or None)
        household = Household(
            name=submission.name,
            phone_number=phone.normalized if phone.valid else None,
            phone_hash=hash_phone(hash_source) if hash_source else None,
            invalid_phone_number=not phone.valid,
            intl_phone_number=phone.international,
            email=email.normalized,
            email_error=email.error,
            languages=list(submission.languages),
            created_at=now,
            updated_at=now,
        )
    else:
        if submission.name:
            household.name = submission.name
        if submission.email:
            # A typo in a re-request must not erase the last known-good
            # email; record the error and keep the old address.
            household.email_error = email.error
            if email.normalized:
                household.email = email.normalized
        merged = list(household.languages)
        for language in submission.languages:
            if language not in merged:
                merged.append(language)
        household.languages = merged
        household.invalid_phone_number = not phone.valid
        household.intl_phone_number = phone.international
        if phone.normalized:
            household.phone_hash = hash_phone(phone.normalized)
        household.updated_at = now
    session.add(household)
    session.flush()

    submission.household_id = household.id

    result = IntakeResult(
        submission_id=submission_id,
        household_id=household.id,
        created_household=created_household,
        phone_valid=phone.valid,
    )

    address_parts = [submission.street_address, submission.city_state, submission.zip_code]
    address = ", ".join(part for part in address_parts if part) or None

    goods_keys: list[str] = []
    for value in [
        *submission.request_types,
        *submission.kitchen_items,
        *submission.furniture_items,
    ]:
        key = normalize_type(value)
        # Social-service types belong in the Social Service Requests table;
        # one appearing in a goods field is malformed input and is reported
        # rather than silently landing in the wrong table (spec 6.1 diagram).
        if key is None or is_social_service(key):
            _append_unique(result.unknown_types, value)
        elif key not in goods_keys:
            goods_keys.append(key)

    for key in goods_keys:
        if _has_open(session, Request, household.id, key):
            _append_unique(result.skipped_duplicate_types, key)
            continue
        request = Request(
            type=key,
            household_id=household.id,
            status=RequestStatus.OPEN,
            request_opened_at=now,
            status_last_updated_at=now,
            created_at=now,
            updated_at=now,
        )
        if BY_KEY[key].category == "furniture":
            request.street_address = submission.street_address
            request.city_state = submission.city_state
            request.zip_code = submission.zip_code
            request.address = address
            # Keep item-level detail (Sofa, Dresser, ...) with the request so
            # the furniture team sees what was asked for, not just the type.
            if key == "furniture" and submission.furniture_items:
                request.notes = _append_note(
                    request.notes, "; ".join(submission.furniture_items)
                )
        if key in BED_DETAIL_TYPES and submission.bed_details:
            request.notes = _append_note(request.notes, "; ".join(submission.bed_details))
        session.add(request)
        session.flush()
        result.created_request_ids.append(request.id)

    seen_social: list[str] = []
    for value in submission.social_service_requests:
        key = normalize_type(value)
        if key is None or not is_social_service(key):
            _append_unique(result.unknown_types, value)
            continue
        if key in seen_social:
            continue
        seen_social.append(key)
        if _has_open(session, SocialServiceRequest, household.id, key):
            _append_unique(result.skipped_duplicate_types, key)
            continue
        social_request = SocialServiceRequest(
            type=key,
            household_id=household.id,
            status=RequestStatus.OPEN,
            internet_access=list(submission.internet_access),
            roof_accessible=submission.roof_accessible,
            street_address=submission.street_address,
            city_state=submission.city_state,
            zip_code=submission.zip_code,
            address=address,
            request_opened_at=now,
            status_last_updated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(social_request)
        session.flush()
        result.created_social_service_request_ids.append(social_request.id)

    submission.processed_at = now
    session.add(submission)
    session.commit()
    return result


def process_pending(session: Session, now: datetime | None = None) -> list[IntakeResult]:
    """Process every submission with ``processed_at IS NULL`` (spec 6.1)."""
    pending_ids = session.exec(
        select(FormSubmission.id)
        .where(col(FormSubmission.processed_at).is_(None))
        .order_by(col(FormSubmission.id))
    ).all()
    return [process_submission(session, submission_id, now=now) for submission_id in pending_ids]


def intake_and_process(
    session: Session, payload: FormSubmissionIn, now: datetime | None = None
) -> IntakeResult:
    """Convenience: store and immediately process a single submission."""
    submission = submit_form(session, payload, now=now)
    return process_submission(session, submission.id, now=now)


def _has_open(
    session: Session,
    model: type[Request] | type[SocialServiceRequest],
    household_id: int,
    type_key: str,
) -> bool:
    """Dedup guard: does the household already have an Open row of this type?"""
    return (
        session.exec(
            select(model).where(
                model.household_id == household_id,
                model.type == type_key,
                model.status == RequestStatus.OPEN,
            )
        ).first()
        is not None
    )


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _append_note(existing: str | None, note: str) -> str:
    return f"{existing}\n{note}" if existing else note
