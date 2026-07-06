"""Intake processing tests (spec 6.1, CONTRACT `bam/services/intake.py`).

Covers household create/update with the validation flag fields, phone dedup,
open-request dedup (interpretation decision 2), unknown-type reporting,
furniture address handling, social-service request creation, invalid-phone
fallback matching, anonymized-household reconnection via ``phone_hash``, and
``process_pending``.
"""

from datetime import datetime, timezone

from sqlmodel import Session, select

from bam.models import (
    FormSubmission,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    apply_status_change,
)
from bam.schemas import FormSubmissionIn
from bam.services import intake
from bam.validation import hash_phone

from .conftest import FIXED_NOW, days_ago

VALID_PHONE = "+17185550100"
VALID_PHONE_FORMATTED = "(718) 555-0100"
INTL_PHONE = "+442079460958"  # valid GB number
INVALID_PHONE = "not a phone"


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat naive values as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def household_count(session: Session) -> int:
    return len(session.exec(select(Household)).all())


def test_new_household_created_with_all_flag_fields(session: Session) -> None:
    """Spec 6.1 steps 2-4: new household with validation flags + Open requests."""
    payload = FormSubmissionIn(
        phone_number=VALID_PHONE_FORMATTED,
        name="Ana Lopez",
        email="Ana@Example.com",
        languages=["es", "en"],
        request_types=["soap", "pads"],
    )
    result = intake.intake_and_process(session, payload, now=FIXED_NOW)

    assert result.created_household is True
    assert result.phone_valid is True
    assert result.skipped_duplicate_types == []
    assert result.unknown_types == []
    assert len(result.created_request_ids) == 2
    assert result.created_social_service_request_ids == []

    household = session.get(Household, result.household_id)
    assert household is not None
    assert household.name == "Ana Lopez"
    assert household.phone_number == VALID_PHONE  # normalized to E.164
    assert household.phone_hash == hash_phone(VALID_PHONE)
    assert household.invalid_phone_number is False
    assert household.intl_phone_number is False
    assert household.email == "Ana@example.com"
    assert household.email_error is None
    assert household.languages == ["es", "en"]
    assert as_utc(household.created_at) == FIXED_NOW
    assert as_utc(household.updated_at) == FIXED_NOW

    requests = session.exec(
        select(Request).where(Request.household_id == household.id)
    ).all()
    assert sorted(r.type for r in requests) == ["pads", "soap"]
    for request in requests:
        assert request.status == RequestStatus.OPEN
        assert as_utc(request.request_opened_at) == FIXED_NOW
        assert request.processing_date is None

    submission = session.get(FormSubmission, result.submission_id)
    assert submission is not None
    assert submission.household_id == household.id
    assert as_utc(submission.processed_at) == FIXED_NOW


def test_international_phone_sets_intl_flag(session: Session) -> None:
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=INTL_PHONE, request_types=["soap"]),
        now=FIXED_NOW,
    )
    household = session.get(Household, result.household_id)
    assert result.phone_valid is True
    assert household.phone_number == INTL_PHONE
    assert household.intl_phone_number is True
    assert household.invalid_phone_number is False


def test_invalid_email_records_email_error(session: Session) -> None:
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE, email="not-an-email", request_types=["soap"]
        ),
        now=FIXED_NOW,
    )
    household = session.get(Household, result.household_id)
    assert household.email is None
    assert household.email_error is not None


def test_dedup_by_phone_updates_household_and_merges_languages(session: Session) -> None:
    """Spec 6.1 sequence: find by phone -> UPDATE, no duplicate household."""
    first = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE,
            name="Ana",
            languages=["es"],
            request_types=["soap"],
        ),
        now=days_ago(2),
    )
    second = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE_FORMATTED,  # same number, different formatting
            name="Ana Lopez",
            email="ana@example.com",
            languages=["en", "es"],
            request_types=["pads"],
        ),
        now=FIXED_NOW,
    )

    assert second.created_household is False
    assert second.household_id == first.household_id
    assert household_count(session) == 1

    household = session.get(Household, first.household_id)
    assert household.languages == ["es", "en"]  # merged, no duplicates
    assert household.name == "Ana Lopez"
    assert household.email == "ana@example.com"
    assert as_utc(household.updated_at) == FIXED_NOW

    submissions = session.exec(
        select(FormSubmission).order_by(FormSubmission.id)
    ).all()
    assert len(submissions) == 2
    assert all(s.household_id == household.id for s in submissions)


def test_no_duplicate_open_request_of_same_type(session: Session) -> None:
    """Interpretation decision 2: skip (and report) already-Open types."""
    intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=VALID_PHONE, request_types=["soap", "pads"]),
        now=days_ago(1),
    )
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=VALID_PHONE, request_types=["soap", "groceries"]),
        now=FIXED_NOW,
    )

    assert result.skipped_duplicate_types == ["soap"]
    assert len(result.created_request_ids) == 1
    requests = session.exec(select(Request)).all()
    assert sorted(r.type for r in requests) == ["groceries", "pads", "soap"]


def test_closed_request_does_not_block_new_open_request(session: Session) -> None:
    """Dedup only guards *Open* requests; a Delivered one allows re-request."""
    first = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=VALID_PHONE, request_types=["soap"]),
        now=days_ago(10),
    )
    request = session.get(Request, first.created_request_ids[0])
    apply_status_change(request, RequestStatus.DELIVERED, now=days_ago(5))
    session.add(request)
    session.commit()

    result = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=VALID_PHONE, request_types=["soap"]),
        now=FIXED_NOW,
    )
    assert result.skipped_duplicate_types == []
    assert len(result.created_request_ids) == 1
    open_soap = session.exec(
        select(Request).where(
            Request.type == "soap", Request.status == RequestStatus.OPEN
        )
    ).all()
    assert len(open_soap) == 1


def test_unknown_types_reported_and_not_created(session: Session) -> None:
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE,
            request_types=["hoverboard", "soap"],
            social_service_requests=["time travel"],
        ),
        now=FIXED_NOW,
    )
    assert result.unknown_types == ["hoverboard", "time travel"]
    assert len(result.created_request_ids) == 1
    assert session.exec(select(Request)).one().type == "soap"
    assert session.exec(select(SocialServiceRequest)).all() == []


def test_type_labels_normalize_to_canonical_keys(session: Session) -> None:
    """Intake accepts labels in any language via ``normalize_type``."""
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE,
            request_types=["Jabón & Productos de baño", "Pots & Pans"],
        ),
        now=FIXED_NOW,
    )
    assert result.unknown_types == []
    types = {session.get(Request, rid).type for rid in result.created_request_ids}
    assert types == {"soap", "pots_pans"}


def test_furniture_requests_get_address_and_bed_details_in_notes(
    session: Session,
) -> None:
    """Spec 6.1: Street Address (if furniture); bed details land in notes."""
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE,
            request_types=["bed", "soap"],
            furniture_items=["Muebles / Furniture / 家具"],
            bed_details=["Queen", "Frame needed"],
            furniture_acknowledgement=True,
            street_address="123 Main St",
            city_state="Brooklyn, NY",
            zip_code="11221",
        ),
        now=FIXED_NOW,
    )
    assert result.unknown_types == []
    requests = {
        session.get(Request, rid).type: session.get(Request, rid)
        for rid in result.created_request_ids
    }
    assert set(requests) == {"bed", "furniture", "soap"}

    bed = requests["bed"]
    assert bed.street_address == "123 Main St"
    assert bed.city_state == "Brooklyn, NY"
    assert bed.zip_code == "11221"
    assert bed.address == "123 Main St, Brooklyn, NY, 11221"
    assert bed.notes is not None
    assert "Queen" in bed.notes and "Frame needed" in bed.notes

    furniture = requests["furniture"]
    assert furniture.street_address == "123 Main St"
    assert furniture.zip_code == "11221"

    soap = requests["soap"]
    assert soap.street_address is None
    assert soap.city_state is None
    assert soap.zip_code is None
    assert soap.address is None


def test_social_service_requests_created_with_internet_fields(
    session: Session,
) -> None:
    """Spec 6.1: SocialServiceRequest per service with Internet Access / Roof."""
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE,
            social_service_requests=["internet", "Vivienda / Housing / 住房"],
            internet_access=["Mobile data only"],
            roof_accessible=True,
            street_address="123 Main St",
            city_state="Brooklyn, NY",
            zip_code="11221",
        ),
        now=FIXED_NOW,
    )
    assert result.created_request_ids == []
    assert len(result.created_social_service_request_ids) == 2
    assert result.unknown_types == []

    rows = session.exec(select(SocialServiceRequest)).all()
    assert sorted(r.type for r in rows) == ["housing", "internet"]
    for row in rows:
        assert row.status == RequestStatus.OPEN
        assert row.internet_access == ["Mobile data only"]
        assert row.roof_accessible is True
        assert row.street_address == "123 Main St"
        assert row.city_state == "Brooklyn, NY"
        assert row.zip_code == "11221"
        assert as_utc(row.request_opened_at) == FIXED_NOW
        assert row.processing_date is None


def test_social_service_dedup_skips_open_duplicate(session: Session) -> None:
    intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=VALID_PHONE, social_service_requests=["internet"]),
        now=days_ago(1),
    )
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE,
            social_service_requests=["internet", "housing"],
        ),
        now=FIXED_NOW,
    )
    assert result.skipped_duplicate_types == ["internet"]
    assert len(result.created_social_service_request_ids) == 1
    assert len(session.exec(select(SocialServiceRequest)).all()) == 2


def test_invalid_phone_household_creation(session: Session) -> None:
    """CONTRACT intake bullet 2: invalid phone -> phone_number=None,
    invalid_phone_number=True; the raw string is hashed so dedup and
    post-scrub reconnection stay possible."""
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=INVALID_PHONE, request_types=["soap"]),
        now=FIXED_NOW,
    )
    assert result.phone_valid is False
    assert result.created_household is True
    assert len(result.created_request_ids) == 1

    household = session.get(Household, result.household_id)
    assert household.invalid_phone_number is True
    assert household.phone_hash == hash_phone(INVALID_PHONE)
    assert household.phone_number is None


def test_invalid_phone_rematch_on_exact_raw_string(session: Session) -> None:
    """CONTRACT intake bullet 2: invalid-phone dedup falls back to exact
    raw-string match against prior submissions' phone."""
    first = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=INVALID_PHONE, request_types=["soap"]),
        now=days_ago(1),
    )
    second = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=INVALID_PHONE, request_types=["pads"]),
        now=FIXED_NOW,
    )
    assert second.created_household is False
    assert second.household_id == first.household_id
    assert household_count(session) == 1

    # A *different* invalid raw string must not match the same household.
    third = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number="999", request_types=["soap"]),
        now=FIXED_NOW,
    )
    assert third.created_household is True
    assert third.household_id != first.household_id


def test_anonymized_household_reconnected_via_phone_hash(
    session: Session, make_household
) -> None:
    """CONTRACT intake bullet 2: re-request after anonymization restores the
    household (phone/name/email back, ``anonymized_at`` cleared)."""
    phone = "+17185559876"
    household = make_household(
        session,
        phone_number=None,
        phone_hash=hash_phone(phone),
        name=None,
        email=None,
        languages=[],
        anonymized_at=days_ago(60),
    )

    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number="(718) 555-9876",
            name="Maria Ruiz",
            email="maria@example.com",
            languages=["es"],
            request_types=["soap"],
        ),
        now=FIXED_NOW,
    )

    assert result.created_household is False
    assert result.household_id == household.id
    assert household_count(session) == 1

    restored = session.get(Household, household.id)
    assert restored.phone_number == phone
    assert restored.phone_hash == hash_phone(phone)
    assert restored.name == "Maria Ruiz"
    assert restored.email == "maria@example.com"
    assert restored.anonymized_at is None
    assert restored.invalid_phone_number is False
    assert as_utc(restored.updated_at) == FIXED_NOW

    request = session.get(Request, result.created_request_ids[0])
    assert request.household_id == household.id
    assert request.status == RequestStatus.OPEN


def test_process_pending_processes_only_unprocessed_rows(session: Session) -> None:
    """CONTRACT: ``process_pending`` handles submissions with
    ``processed_at IS NULL`` only."""
    already = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number="+17185550101", request_types=["soap"]),
        now=days_ago(1),
    )
    pending_a = intake.submit_form(
        session,
        FormSubmissionIn(phone_number="+17185550102", request_types=["pads"]),
        now=FIXED_NOW,
    )
    pending_b = intake.submit_form(
        session,
        FormSubmissionIn(phone_number="+17185550103", request_types=["groceries"]),
        now=FIXED_NOW,
    )
    assert pending_a.processed_at is None
    assert pending_b.processed_at is None

    results = intake.process_pending(session, now=FIXED_NOW)

    assert [r.submission_id for r in results] == [pending_a.id, pending_b.id]
    assert all(r.created_household for r in results)

    processed_a = session.get(FormSubmission, pending_a.id)
    processed_b = session.get(FormSubmission, pending_b.id)
    assert as_utc(processed_a.processed_at) == FIXED_NOW
    assert as_utc(processed_b.processed_at) == FIXED_NOW

    untouched = session.get(FormSubmission, already.submission_id)
    assert as_utc(untouched.processed_at) == days_ago(1)  # not reprocessed

    assert intake.process_pending(session, now=FIXED_NOW) == []


def test_reprocessing_a_submission_is_idempotent(session: Session) -> None:
    """A re-run of process_submission must not duplicate anything."""
    submission = intake.submit_form(
        session, FormSubmissionIn(phone_number=VALID_PHONE, request_types=["soap"])
    )
    first = intake.process_submission(session, submission.id, now=FIXED_NOW)
    second = intake.process_submission(session, submission.id, now=FIXED_NOW)

    assert second.already_processed is True
    assert second.household_id == first.household_id
    assert second.created_request_ids == []
    assert household_count(session) == 1
    assert len(session.exec(select(Request)).all()) == 1


def test_reprocessing_invalid_phone_submission_is_idempotent(session: Session) -> None:
    """Invalid-phone submissions were the corruption case: a second run used
    to create a duplicate household plus a dangling duplicate Open request."""
    submission = intake.submit_form(
        session, FormSubmissionIn(phone_number=INVALID_PHONE, request_types=["soap"])
    )
    intake.process_submission(session, submission.id, now=FIXED_NOW)
    second = intake.process_submission(session, submission.id, now=FIXED_NOW)

    assert second.already_processed is True
    assert household_count(session) == 1
    assert len(session.exec(select(Request)).all()) == 1


def test_social_service_type_in_goods_field_is_reported_not_misfiled(
    session: Session,
) -> None:
    """Spec 6.1 diagram routes social services to their own table; a social
    type in a goods field is malformed input, not a goods Request."""
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE, request_types=["Vivienda / Housing / 住房"]
        ),
        now=FIXED_NOW,
    )

    assert result.created_request_ids == []
    assert result.unknown_types == ["Vivienda / Housing / 住房"]
    assert session.exec(select(Request)).all() == []
    assert session.exec(select(SocialServiceRequest)).all() == []


def test_invalid_email_on_resubmission_keeps_last_good_email(session: Session) -> None:
    """A typo in a re-request must not erase working contact info."""
    intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE, email="good@example.com", request_types=["soap"]
        ),
        now=days_ago(1),
    )
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE, email="bad@@nope", request_types=["pads"]
        ),
        now=FIXED_NOW,
    )

    household = session.get(Household, result.household_id)
    assert household.email == "good@example.com"
    assert household.email_error


def test_invalid_phone_household_reconnects_after_anonymization(
    session: Session,
) -> None:
    """The raw-string phone_hash survives the PII scrub, so a re-request from
    the same invalid phone reconnects instead of duplicating the household."""
    first = intake.intake_and_process(
        session,
        FormSubmissionIn(phone_number=INVALID_PHONE, request_types=["soap"]),
        now=days_ago(3),
    )
    household = session.get(Household, first.household_id)
    household.name = None
    household.anonymized_at = FIXED_NOW
    session.add(household)
    session.commit()

    second = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=INVALID_PHONE, name="Rosa", request_types=["pads"]
        ),
        now=FIXED_NOW,
    )

    assert second.created_household is False
    assert second.household_id == first.household_id
    restored = session.get(Household, first.household_id)
    assert restored.anonymized_at is None
    assert restored.name == "Rosa"


def test_numeric_zip_code_is_coerced() -> None:
    """Spec 4 types Zip Code as a number; numeric input must not 422."""
    payload = FormSubmissionIn(phone_number=VALID_PHONE, zip_code=11221)
    assert payload.zip_code == "11221"


def test_item_level_names_resolve_to_first_class_types(session: Session) -> None:
    """Item names (Plates, Sofa, Dresser, ...) resolve to the production
    catalog's first-class types; furniture-category requests carry the
    delivery address."""
    result = intake.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number=VALID_PHONE,
            kitchen_items=["Plates"],
            furniture_items=["Sofa", "Dresser"],
            street_address="123 Knickerbocker Ave",
        ),
        now=FIXED_NOW,
    )

    assert result.unknown_types == []
    requests = session.exec(select(Request)).all()
    assert sorted(r.type for r in requests) == ["clothes_dresser", "plates", "sofa"]
    sofa = next(r for r in requests if r.type == "sofa")
    assert sofa.street_address == "123 Knickerbocker Ave"
