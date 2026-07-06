"""Tests for auto-expiration and PII scrubbing (spec sections 2, 4, 6.1 step 7;
privacy goal "hash sensitive data"; CONTRACT.md interpretation rule 3).

Time is frozen by passing explicit ``now=FIXED_NOW`` into every service call;
row timestamps are backdated with ``days_ago``.
"""

from datetime import timedelta
from typing import Any, Callable

from sqlmodel import Session

from bam.models import (
    AppointmentStatus,
    FormSubmission,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
)
from bam.services.expiration import expire_stale_requests
from bam.services.privacy import scrub_expired_pii
from bam.validation import hash_phone
from tests.conftest import FIXED_NOW, days_ago

HouseholdFactory = Callable[..., Household]
RequestFactory = Callable[..., Request]

REQUEST_PII: dict[str, str] = {
    "street_address": "123 Main St",
    "city_state": "Brooklyn, NY",
    "zip_code": "11221",
    "address": "123 Main St, Brooklyn, NY 11221",
    "notes": "leave with neighbor",
}


def make_social_request(
    session: Session,
    household: Household,
    type: str = "housing",
    **overrides: Any,
) -> SocialServiceRequest:
    request = SocialServiceRequest(household_id=household.id, type=type, **overrides)
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


class TestExpiration:
    """spec 2 / 4: 14-day standard window, 30 days pots & pans."""

    def test_soap_open_15_days_times_out(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        household = make_household(session)
        request = make_request(session, household, type="soap", request_opened_at=days_ago(15))

        report = expire_stale_requests(session, now=FIXED_NOW)

        session.refresh(request)
        assert request.status == RequestStatus.TIMEOUT
        assert report.timed_out_request_ids == [request.id]
        assert report.timed_out_social_service_request_ids == []

    def test_soap_open_13_days_stays_open(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        household = make_household(session)
        request = make_request(session, household, type="soap", request_opened_at=days_ago(13))

        report = expire_stale_requests(session, now=FIXED_NOW)

        session.refresh(request)
        assert request.status == RequestStatus.OPEN
        assert request.processing_date is None
        assert report.timed_out_request_ids == []

    def test_pots_pans_15_days_stays_open(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        household = make_household(session)
        request = make_request(
            session, household, type="pots_pans", request_opened_at=days_ago(15)
        )

        report = expire_stale_requests(session, now=FIXED_NOW)

        session.refresh(request)
        assert request.status == RequestStatus.OPEN
        assert report.timed_out_request_ids == []

    def test_pots_pans_31_days_times_out(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        household = make_household(session)
        request = make_request(
            session, household, type="pots_pans", request_opened_at=days_ago(31)
        )

        report = expire_stale_requests(session, now=FIXED_NOW)

        session.refresh(request)
        assert request.status == RequestStatus.TIMEOUT
        assert report.timed_out_request_ids == [request.id]

    def test_social_service_15_days_times_out(
        self,
        session: Session,
        make_household: HouseholdFactory,
    ) -> None:
        household = make_household(session)
        social = make_social_request(
            session, household, type="housing", request_opened_at=days_ago(15)
        )

        report = expire_stale_requests(session, now=FIXED_NOW)

        session.refresh(social)
        assert social.status == RequestStatus.TIMEOUT
        assert report.timed_out_social_service_request_ids == [social.id]
        assert report.timed_out_request_ids == []

    def test_booked_household_is_exempt(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        booked = make_household(
            session,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_date=FIXED_NOW.date() + timedelta(days=2),
        )
        booked_request = make_request(
            session, booked, type="soap", request_opened_at=days_ago(15)
        )
        booked_social = make_social_request(
            session, booked, type="housing", request_opened_at=days_ago(15)
        )
        unbooked = make_household(session)
        stale_request = make_request(
            session, unbooked, type="soap", request_opened_at=days_ago(15)
        )

        report = expire_stale_requests(session, now=FIXED_NOW)

        session.refresh(booked_request)
        session.refresh(booked_social)
        session.refresh(stale_request)
        assert booked_request.status == RequestStatus.OPEN
        assert booked_social.status == RequestStatus.OPEN
        assert stale_request.status == RequestStatus.TIMEOUT
        assert report.timed_out_request_ids == [stale_request.id]
        assert report.timed_out_social_service_request_ids == []

    def test_timed_out_requests_get_processing_date(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        household = make_household(session)
        request = make_request(session, household, type="soap", request_opened_at=days_ago(15))
        social = make_social_request(
            session, household, type="housing", request_opened_at=days_ago(15)
        )

        expire_stale_requests(session, now=FIXED_NOW)

        session.refresh(request)
        session.refresh(social)
        # spec 4 Processing Date formula: Timeout -> +14 days.
        assert request.processing_date == FIXED_NOW.date() + timedelta(days=14)
        assert social.processing_date == FIXED_NOW.date() + timedelta(days=14)


class TestRequestPiiScrub:
    """scrub pass 1: closed requests past processing_date lose address + notes."""

    def test_closed_request_past_processing_date_is_scrubbed(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        household = make_household(session)
        request = make_request(
            session,
            household,
            type="furniture",
            status=RequestStatus.TIMEOUT,
            processing_date=FIXED_NOW.date() - timedelta(days=1),
            geocode="40.69,-73.93",
            **REQUEST_PII,
        )

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(request)
        assert request.street_address is None
        assert request.city_state is None
        assert request.zip_code is None
        assert request.geocode is None
        assert request.address is None
        assert request.notes is None
        assert report.requests_scrubbed == 1

    def test_open_and_future_processing_date_requests_keep_pii(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        household = make_household(session)
        open_request = make_request(
            session,
            household,
            type="bed",
            status=RequestStatus.OPEN,
            request_opened_at=days_ago(1),
            **REQUEST_PII,
        )
        future_request = make_request(
            session,
            household,
            type="furniture",
            status=RequestStatus.DELIVERED,
            processing_date=FIXED_NOW.date() + timedelta(days=5),
            **REQUEST_PII,
        )

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(open_request)
        session.refresh(future_request)
        for request in (open_request, future_request):
            assert request.street_address == REQUEST_PII["street_address"]
            assert request.city_state == REQUEST_PII["city_state"]
            assert request.zip_code == REQUEST_PII["zip_code"]
            assert request.address == REQUEST_PII["address"]
            assert request.notes == REQUEST_PII["notes"]
        assert report.requests_scrubbed == 0

    def test_closed_social_service_request_is_scrubbed(
        self,
        session: Session,
        make_household: HouseholdFactory,
    ) -> None:
        household = make_household(session)
        social = make_social_request(
            session,
            household,
            type="internet",
            status=RequestStatus.DELIVERED,
            processing_date=FIXED_NOW.date() - timedelta(days=1),
            **REQUEST_PII,
        )

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(social)
        assert social.street_address is None
        assert social.city_state is None
        assert social.zip_code is None
        assert social.address is None
        assert social.notes is None
        assert report.social_service_requests_scrubbed == 1


class TestHouseholdAnonymization:
    """scrub pass 2: inactive households keep only the phone hash."""

    def test_inactive_household_is_anonymized(
        self,
        session: Session,
        make_household: HouseholdFactory,
    ) -> None:
        household = make_household(
            session,
            email="pat@example.com",
            notes="prefers evening calls",
            updated_at=days_ago(31),
        )
        phone = household.phone_number
        expected_hash = household.phone_hash

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(household)
        assert report.households_anonymized == 1
        assert household.anonymized_at is not None
        assert household.phone_number is None
        assert household.name is None
        assert household.email is None
        assert household.notes is None
        assert household.phone_hash == expected_hash == hash_phone(phone)

    def test_phone_hash_derived_when_missing(
        self,
        session: Session,
        make_household: HouseholdFactory,
    ) -> None:
        household = make_household(session, phone_hash=None, updated_at=days_ago(31))
        phone = household.phone_number

        scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(household)
        assert household.anonymized_at is not None
        assert household.phone_number is None
        assert household.phone_hash == hash_phone(phone)

    def test_household_with_open_request_is_not_anonymized(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        household = make_household(session, updated_at=days_ago(31))
        make_request(session, household, type="soap", request_opened_at=days_ago(2))

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(household)
        assert report.households_anonymized == 0
        assert household.anonymized_at is None
        assert household.phone_number is not None
        assert household.name is not None

    def test_household_with_open_social_request_is_not_anonymized(
        self,
        session: Session,
        make_household: HouseholdFactory,
    ) -> None:
        household = make_household(session, updated_at=days_ago(31))
        make_social_request(session, household, request_opened_at=days_ago(2))

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(household)
        assert report.households_anonymized == 0
        assert household.anonymized_at is None

    def test_recently_active_household_is_not_anonymized(
        self,
        session: Session,
        make_household: HouseholdFactory,
    ) -> None:
        household = make_household(session, updated_at=days_ago(5))

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(household)
        assert report.households_anonymized == 0
        assert household.anonymized_at is None
        assert household.phone_number is not None


class TestSubmissionScrub:
    """scrub pass 3: processed submissions older than retention lose raw PII."""

    def test_old_processed_submission_is_scrubbed(self, session: Session) -> None:
        submission = FormSubmission(
            name="Pat Doe",
            phone_number="7185550100",
            email="pat@example.com",
            notes="call after 5pm",
            street_address="123 Main St",
            city_state="Brooklyn, NY",
            zip_code="11221",
            created_at=days_ago(31),
            processed_at=days_ago(30),
        )
        session.add(submission)
        session.commit()
        session.refresh(submission)

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(submission)
        assert report.submissions_scrubbed == 1
        assert submission.scrubbed_at is not None
        assert submission.name is None
        assert submission.phone_number is None
        assert submission.email is None
        assert submission.notes is None
        assert submission.street_address is None
        assert submission.city_state is None
        assert submission.zip_code is None

    def test_unprocessed_submission_is_not_scrubbed(self, session: Session) -> None:
        submission = FormSubmission(
            name="Pat Doe",
            phone_number="7185550100",
            created_at=days_ago(40),
            processed_at=None,
        )
        session.add(submission)
        session.commit()
        session.refresh(submission)

        report = scrub_expired_pii(session, now=FIXED_NOW)

        session.refresh(submission)
        assert report.submissions_scrubbed == 0
        assert submission.scrubbed_at is None
        assert submission.name == "Pat Doe"
        assert submission.phone_number == "7185550100"


class TestScrubReportAndIdempotency:
    def _seed(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        active = make_household(session)
        make_request(
            session,
            active,
            type="furniture",
            status=RequestStatus.TIMEOUT,
            processing_date=FIXED_NOW.date() - timedelta(days=1),
            **REQUEST_PII,
        )
        make_social_request(
            session,
            active,
            type="housing",
            status=RequestStatus.DELIVERED,
            processing_date=FIXED_NOW.date() - timedelta(days=1),
            **REQUEST_PII,
        )
        make_household(session, updated_at=days_ago(31))
        session.add(
            FormSubmission(
                name="Old Sub",
                phone_number="7185550101",
                created_at=days_ago(31),
                processed_at=days_ago(30),
            )
        )
        session.commit()

    def test_report_counters_are_accurate(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        self._seed(session, make_household, make_request)

        report = scrub_expired_pii(session, now=FIXED_NOW)

        assert report.requests_scrubbed == 1
        assert report.social_service_requests_scrubbed == 1
        assert report.households_anonymized == 1
        assert report.submissions_scrubbed == 1

    def test_second_scrub_run_counts_zero(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        self._seed(session, make_household, make_request)

        first = scrub_expired_pii(session, now=FIXED_NOW)
        second = scrub_expired_pii(session, now=FIXED_NOW)

        assert first.requests_scrubbed == 1
        assert second.requests_scrubbed == 0
        assert second.social_service_requests_scrubbed == 0
        assert second.households_anonymized == 0
        assert second.submissions_scrubbed == 0


class TestExpirationRegressions:
    def test_past_dated_booking_does_not_exempt(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
    ) -> None:
        """A dangling Booked status from a distro whose no-show pass never ran
        must not make requests immortal (or block anonymization)."""
        dangling = make_household(
            session,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_date=FIXED_NOW.date() - timedelta(days=10),
        )
        stale = make_request(session, dangling, request_opened_at=days_ago(15))

        report = expire_stale_requests(session, now=FIXED_NOW)

        assert stale.id in report.timed_out_request_ids

    def test_expiry_window_settings_override_is_honored(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
        monkeypatch,
    ) -> None:
        """BAM_DEFAULT_EXPIRY_DAYS must actually drive the window and the
        processing-date formula, not just sit in config."""
        from bam.config import settings

        monkeypatch.setattr(settings, "default_expiry_days", 7)
        household = make_household(session)
        request = make_request(session, household, request_opened_at=days_ago(8))

        report = expire_stale_requests(session, now=FIXED_NOW)

        assert request.id in report.timed_out_request_ids
        session.refresh(request)
        assert request.processing_date == FIXED_NOW.date() + timedelta(days=7)
