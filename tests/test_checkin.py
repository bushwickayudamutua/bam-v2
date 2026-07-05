"""Tests for bam.services.checkin (spec 6.3 + no-show sequence, A1-A3).

Covers the distribution-day flow: phone lookup with formatted input (6.3
step 2-3), check-in resetting the missed-appointment counter (interpretation
rule 5), fulfillment with the 14/30-day processing windows feeding the
Fulfilled Request Count table (6.3 step 4), partial fulfillment (A1), and the
end-of-event no-show pass (A2 first miss, A3 second miss).
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from bam.models import (
    AppointmentStatus,
    FulfilledRequestCount,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
)
from bam.services.checkin import (
    check_in,
    fulfill_requests,
    lookup_by_phone,
    process_no_shows,
)
from tests.conftest import FIXED_NOW, days_ago

TODAY = FIXED_NOW.date()


def make_social_request(
    session: Session,
    household: Household,
    type: str = "housing",
    **overrides,
) -> SocialServiceRequest:
    social = SocialServiceRequest(household_id=household.id, type=type, **overrides)
    session.add(social)
    session.commit()
    session.refresh(social)
    return social


class TestLookupByPhone:
    def test_formatted_variant_finds_e164_household(
        self, session, make_household, make_request
    ) -> None:
        household = make_household(session, phone_number="+17185550000")
        request = make_request(session, household, type="soap")
        social = make_social_request(session, household, type="housing")

        view = lookup_by_phone(session, "(718) 555-0000")

        assert view is not None
        assert view.household.id == household.id
        assert [r.id for r in view.open_requests] == [request.id]
        assert view.open_requests[0].type == "soap"
        assert [s.id for s in view.open_social_service_requests] == [social.id]
        assert view.open_social_service_requests[0].type == "housing"

    def test_only_open_requests_returned(
        self, session, make_household, make_request
    ) -> None:
        household = make_household(session, phone_number="+17185550001")
        open_request = make_request(session, household, type="pads")
        make_request(session, household, type="soap", status=RequestStatus.DELIVERED)
        make_request(session, household, type="clothing", status=RequestStatus.TIMEOUT)

        view = lookup_by_phone(session, "718-555-0001")

        assert view is not None
        assert [r.id for r in view.open_requests] == [open_request.id]

    def test_miss_returns_none(self, session, make_household) -> None:
        make_household(session, phone_number="+17185550002")

        assert lookup_by_phone(session, "(212) 555-9999") is None


class TestCheckIn:
    def test_sets_status_last_attended_and_resets_missed_count(
        self, session, make_household
    ) -> None:
        household = make_household(
            session,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_date=TODAY,
            appointment_time="11:00 AM",
            missed_appointment_count=1,
        )

        updated = check_in(session, household.id, now=FIXED_NOW)

        assert updated.appointment_status == AppointmentStatus.CHECKED_IN
        assert updated.last_attended == TODAY
        assert updated.missed_appointment_count == 0

    def test_unknown_household_raises(self, session) -> None:
        with pytest.raises(ValueError):
            check_in(session, 99999, now=FIXED_NOW)


class TestFulfillRequests:
    def test_delivered_with_processing_windows(
        self, session, make_household, make_request
    ) -> None:
        household = make_household(session)
        soap = make_request(session, household, type="soap")
        pots = make_request(session, household, type="pots_pans")

        fulfill_requests(session, request_ids=[soap.id, pots.id], now=FIXED_NOW)

        session.refresh(soap)
        session.refresh(pots)
        assert soap.status == RequestStatus.DELIVERED
        assert soap.processing_date == TODAY + timedelta(days=14)
        assert pots.status == RequestStatus.DELIVERED
        assert pots.processing_date == TODAY + timedelta(days=30)

    def test_increments_fulfilled_counts(
        self, session, make_household, make_request
    ) -> None:
        household = make_household(session)
        soap = make_request(session, household, type="soap")
        pots = make_request(session, household, type="pots_pans")

        fulfill_requests(session, request_ids=[soap.id, pots.id], now=FIXED_NOW)

        rows = {
            (row.date, row.request_type): row.count
            for row in session.exec(select(FulfilledRequestCount)).all()
        }
        assert rows[(TODAY, "soap")] == 1
        assert rows[(TODAY, "pots_pans")] == 1

    def test_second_fulfill_same_day_upserts_count_row(
        self, session, make_household, make_request
    ) -> None:
        first = make_household(session)
        second = make_household(session)
        soap_a = make_request(session, first, type="soap")
        soap_b = make_request(session, second, type="soap")

        fulfill_requests(session, request_ids=[soap_a.id], now=FIXED_NOW)
        fulfill_requests(session, request_ids=[soap_b.id], now=FIXED_NOW)

        rows = session.exec(
            select(FulfilledRequestCount).where(
                FulfilledRequestCount.date == TODAY,
                FulfilledRequestCount.request_type == "soap",
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].count == 2

    def test_handles_social_service_ids(self, session, make_household) -> None:
        household = make_household(session)
        social = make_social_request(session, household, type="housing")

        fulfill_requests(
            session, social_service_request_ids=[social.id], now=FIXED_NOW
        )

        session.refresh(social)
        assert social.status == RequestStatus.DELIVERED
        assert social.processing_date == TODAY + timedelta(days=14)

    def test_unknown_request_id_raises(self, session, make_household) -> None:
        make_household(session)

        with pytest.raises(ValueError):
            fulfill_requests(session, request_ids=[99999], now=FIXED_NOW)

    def test_unknown_social_service_id_raises(self, session) -> None:
        with pytest.raises(ValueError):
            fulfill_requests(
                session, social_service_request_ids=[99999], now=FIXED_NOW
            )

    def test_partial_fulfillment_leaves_other_request_open(
        self, session, make_household, make_request
    ) -> None:
        """A1: out of stock — the unfulfilled request stays Open."""
        household = make_household(session)
        soap = make_request(session, household, type="soap")
        pads = make_request(session, household, type="pads")

        fulfill_requests(session, request_ids=[soap.id], now=FIXED_NOW)

        session.refresh(soap)
        session.refresh(pads)
        assert soap.status == RequestStatus.DELIVERED
        assert pads.status == RequestStatus.OPEN
        assert pads.processing_date is None


class TestProcessNoShows:
    def test_first_miss_marks_missed_and_keeps_requests_open(
        self, session, make_household, make_request
    ) -> None:
        """A2: 1st missed appointment returns the household to the queue."""
        household = make_household(
            session,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_date=TODAY,
            appointment_time="11:00 AM",
            missed_appointment_count=0,
        )
        request = make_request(session, household, type="soap")
        social = make_social_request(session, household, type="housing")

        report = process_no_shows(session, TODAY, now=FIXED_NOW)

        session.refresh(household)
        session.refresh(request)
        session.refresh(social)
        assert report.missed_household_ids == [household.id]
        assert report.timed_out_household_ids == []
        assert household.appointment_status == AppointmentStatus.MISSED
        assert household.missed_appointment_count == 1
        assert household.appointment_date is None
        assert household.appointment_time is None
        assert request.status == RequestStatus.OPEN
        assert social.status == RequestStatus.OPEN

    def test_second_miss_times_out_goods_and_social_requests(
        self, session, make_household, make_request
    ) -> None:
        """A3: 2nd missed appointment closes all open requests via Timeout."""
        household = make_household(
            session,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_date=TODAY,
            appointment_time="1:00 PM",
            missed_appointment_count=1,
        )
        request = make_request(session, household, type="soap", request_opened_at=days_ago(5))
        social = make_social_request(session, household, type="housing")

        report = process_no_shows(session, TODAY, now=FIXED_NOW)

        session.refresh(household)
        session.refresh(request)
        session.refresh(social)
        assert report.missed_household_ids == [household.id]
        assert report.timed_out_household_ids == [household.id]
        assert household.appointment_status == AppointmentStatus.MISSED
        assert household.missed_appointment_count == 2
        assert household.appointment_date is None
        assert household.appointment_time is None
        assert request.status == RequestStatus.TIMEOUT
        assert request.processing_date == TODAY + timedelta(days=14)
        assert social.status == RequestStatus.TIMEOUT
        assert social.processing_date == TODAY + timedelta(days=14)

    def test_checked_in_and_other_date_households_untouched(
        self, session, make_household, make_request
    ) -> None:
        checked_in = make_household(
            session,
            appointment_status=AppointmentStatus.CHECKED_IN,
            appointment_date=TODAY,
            appointment_time="11:00 AM",
            missed_appointment_count=0,
        )
        other_date = make_household(
            session,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_date=TODAY + timedelta(days=7),
            appointment_time="2:00 PM",
            missed_appointment_count=1,
        )
        checked_in_request = make_request(session, checked_in, type="soap")
        other_date_request = make_request(session, other_date, type="pads")

        report = process_no_shows(session, TODAY, now=FIXED_NOW)

        session.refresh(checked_in)
        session.refresh(other_date)
        session.refresh(checked_in_request)
        session.refresh(other_date_request)
        assert report.missed_household_ids == []
        assert report.timed_out_household_ids == []
        assert checked_in.appointment_status == AppointmentStatus.CHECKED_IN
        assert checked_in.appointment_date == TODAY
        assert checked_in.missed_appointment_count == 0
        assert other_date.appointment_status == AppointmentStatus.BOOKED
        assert other_date.appointment_date == TODAY + timedelta(days=7)
        assert other_date.missed_appointment_count == 1
        assert checked_in_request.status == RequestStatus.OPEN
        assert other_date_request.status == RequestStatus.OPEN
