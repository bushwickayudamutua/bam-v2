"""Tests for bam.services.outreach (spec 6.2, outreach flowchart, 6.4 A4-A6).

Covers building the filtered outreach list (spec 6.2 step 1), the templated
text blast with its message cap and 30-then-pause rate limit (spec 6.2
sequence diagram), appointment confirmation (spec 6.2 steps 3-4), and the
phone-outreach outcomes A4-A6 (spec 6.4 error table, interpretation rules
4 and 7).
"""

from datetime import date, timedelta
from typing import Any, Callable

import pytest
from sqlmodel import Session

from bam.config import settings
from bam.models import (
    AppointmentStatus,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
)
from bam.services.outreach import (
    build_outreach_list,
    confirm_appointment,
    record_outreach_outcome,
    send_text_blast,
)
from bam.sms.base import SMSResult
from bam.sms.console import ConsoleSMSProvider
from tests.conftest import FIXED_NOW, RecordingSleeper, days_ago

HouseholdFactory = Callable[..., Household]
RequestFactory = Callable[..., Request]


class FailingSMSProvider:
    """Stub provider whose every send fails (delivery-error path)."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str]] = []

    def send(self, to: str, body: str) -> SMSResult:
        self.attempts.append((to, body))
        return SMSResult(to=to, body=body, ok=False, error="delivery failed")


def make_social_service_request(
    session: Session, household: Household, type: str = "housing", **overrides: Any
) -> SocialServiceRequest:
    ssr = SocialServiceRequest(household_id=household.id, type=type, **overrides)
    session.add(ssr)
    session.commit()
    session.refresh(ssr)
    return ssr


class TestBuildOutreachList:
    def test_includes_household_with_open_request(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Spec 6.2 step 1: households with an open request are candidates."""
        household = make_household(session)
        make_request(session, household, type="soap", request_opened_at=days_ago(3))

        candidates = build_outreach_list(session, now=FIXED_NOW)

        assert [c.household_id for c in candidates] == [household.id]
        candidate = candidates[0]
        assert candidate.name == household.name
        assert candidate.phone_number == household.phone_number
        assert candidate.open_request_types == ["soap"]
        assert candidate.oldest_open_request_at == days_ago(3)

    def test_excludes_household_with_no_open_requests(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        household = make_household(session)
        make_request(session, household, type="soap", status=RequestStatus.TIMEOUT)
        make_request(session, household, type="pads", status=RequestStatus.DELIVERED)

        assert build_outreach_list(session, now=FIXED_NOW) == []

    def test_request_type_filter_matches_supplies(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Spec 6.2 step 1: "available supplies match" filter."""
        soap_household = make_household(session)
        make_request(session, soap_household, type="soap")
        clothing_household = make_household(session)
        make_request(session, clothing_household, type="clothing")

        candidates = build_outreach_list(session, request_types=["soap"], now=FIXED_NOW)

        assert [c.household_id for c in candidates] == [soap_household.id]

    def test_oldest_date_uses_only_fulfillable_requests(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Ordering key is the Date of Oldest *Fulfillable* Request (spec 4)."""
        household = make_household(session)
        make_request(session, household, type="soap", request_opened_at=days_ago(20))
        make_request(session, household, type="clothing", request_opened_at=days_ago(2))

        candidates = build_outreach_list(session, request_types=["clothing"], now=FIXED_NOW)

        assert len(candidates) == 1
        assert candidates[0].oldest_open_request_at == days_ago(2)

    def test_language_overlap_filter(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Spec 6.2 step 1: language availability at the distro."""
        spanish = make_household(session, languages=["es"])
        make_request(session, spanish, type="soap")
        english_only = make_household(session, languages=["en"])
        make_request(session, english_only, type="soap")
        no_languages = make_household(session, languages=[])
        make_request(session, no_languages, type="soap")

        candidates = build_outreach_list(session, languages=["es", "zh"], now=FIXED_NOW)

        assert [c.household_id for c in candidates] == [spanish.id]

    def test_excludes_invalid_or_missing_phone(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        invalid = make_household(session, invalid_phone_number=True)
        make_request(session, invalid, type="soap")
        phoneless = make_household(session, phone_number=None)
        make_request(session, phoneless, type="soap")
        good = make_household(session)
        make_request(session, good, type="soap")

        candidates = build_outreach_list(session, now=FIXED_NOW)

        assert [c.household_id for c in candidates] == [good.id]

    def test_excludes_booked_but_not_other_appointment_statuses(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """A booked household is already scheduled; Missed returns to the queue (A2)."""
        booked = make_household(
            session,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_date=date(2026, 7, 3),
            appointment_time="11:00 AM",
        )
        make_request(session, booked, type="soap", request_opened_at=days_ago(5))
        missed = make_household(session, appointment_status=AppointmentStatus.MISSED)
        make_request(session, missed, type="soap", request_opened_at=days_ago(4))

        candidates = build_outreach_list(session, now=FIXED_NOW)

        assert [c.household_id for c in candidates] == [missed.id]

    def test_last_texted_exclusion_window(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        recently_texted = make_household(session, last_texted=days_ago(2).date())
        make_request(session, recently_texted, type="soap")
        texted_long_ago = make_household(session, last_texted=days_ago(10).date())
        make_request(session, texted_long_ago, type="soap")
        never_texted = make_household(session, last_texted=None)
        make_request(session, never_texted, type="soap")

        candidates = build_outreach_list(
            session, exclude_texted_within_days=7, now=FIXED_NOW
        )

        assert {c.household_id for c in candidates} == {texted_long_ago.id, never_texted.id}

    def test_last_attended_exclusion_window(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Spec 6.2 step 1: "not recently attended" filter."""
        recent_attendee = make_household(session, last_attended=days_ago(3).date())
        make_request(session, recent_attendee, type="soap")
        old_attendee = make_household(session, last_attended=days_ago(30).date())
        make_request(session, old_attendee, type="soap")

        candidates = build_outreach_list(
            session, exclude_attended_within_days=14, now=FIXED_NOW
        )

        assert [c.household_id for c in candidates] == [old_attendee.id]

    def test_zero_windows_disable_exclusion(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        household = make_household(
            session, last_texted=days_ago(1).date(), last_attended=days_ago(1).date()
        )
        make_request(session, household, type="soap")

        candidates = build_outreach_list(
            session, exclude_texted_within_days=0, exclude_attended_within_days=0, now=FIXED_NOW
        )

        assert [c.household_id for c in candidates] == [household.id]

    def test_ordered_by_oldest_open_request(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Spec 4: rollup "Date of Oldest Fulfillable Request", oldest first."""
        middle = make_household(session)
        make_request(session, middle, type="soap", request_opened_at=days_ago(5))
        oldest = make_household(session)
        make_request(session, oldest, type="pads", request_opened_at=days_ago(10))
        newest = make_household(session)
        make_request(session, newest, type="soap", request_opened_at=days_ago(1))

        candidates = build_outreach_list(session, now=FIXED_NOW)

        assert [c.household_id for c in candidates] == [oldest.id, middle.id, newest.id]

    def test_limit_truncates_after_ordering(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        newest = make_household(session)
        make_request(session, newest, type="soap", request_opened_at=days_ago(1))
        oldest = make_household(session)
        make_request(session, oldest, type="soap", request_opened_at=days_ago(9))
        middle = make_household(session)
        make_request(session, middle, type="soap", request_opened_at=days_ago(5))

        candidates = build_outreach_list(session, limit=2, now=FIXED_NOW)

        assert [c.household_id for c in candidates] == [oldest.id, middle.id]


class TestSendTextBlast:
    def test_renders_first_name_and_request_url(
        self,
        session: Session,
        make_household: HouseholdFactory,
        sms: ConsoleSMSProvider,
    ) -> None:
        """Spec 6.2 sequence diagram: SMS with [FIRST_NAME] and [REQUEST_URL]."""
        household = make_household(session, name="Maria Lopez")

        report = send_text_blast(
            session,
            [household.id],
            "Hola [FIRST_NAME]! Book here: [REQUEST_URL]",
            sms,
            now=FIXED_NOW,
            token_factory=lambda: "tok1",
        )

        expected = f"Hola Maria! Book here: {settings.request_form_url}?r=tok1"
        assert report.sent == 1
        assert report.failed == 0
        assert len(sms.sent) == 1
        assert sms.sent[0].to == household.phone_number
        assert sms.sent[0].body == expected
        assert report.messages[0].household_id == household.id
        assert report.messages[0].body == expected
        assert report.messages[0].ok is True

    def test_request_url_randomized_per_message(
        self,
        session: Session,
        make_household: HouseholdFactory,
        sms: ConsoleSMSProvider,
    ) -> None:
        """Spec 6.2 sequence diagram: "[REQUEST_URL] (randomized)" — each
        message carries a distinct URL variant of the same base."""
        first = make_household(session)
        second = make_household(session)

        send_text_blast(
            session, [first.id, second.id], "[REQUEST_URL]", sms, now=FIXED_NOW
        )

        bodies = [m.body for m in sms.sent]
        assert len(bodies) == 2
        assert bodies[0] != bodies[1]
        assert all(b.startswith(settings.request_form_url + "?r=") for b in bodies)

    def test_request_url_randomization_can_be_disabled(
        self,
        session: Session,
        make_household: HouseholdFactory,
        sms: ConsoleSMSProvider,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "randomize_request_url", False)
        household = make_household(session)

        send_text_blast(session, [household.id], "[REQUEST_URL]", sms, now=FIXED_NOW)

        assert sms.sent[0].body == settings.request_form_url

    def test_success_sets_last_texted_today(
        self,
        session: Session,
        make_household: HouseholdFactory,
        sms: ConsoleSMSProvider,
    ) -> None:
        """Spec 6.2 sequence diagram: UPDATE Last Texted = TODAY() after send."""
        household = make_household(session, last_texted=None)

        send_text_blast(session, [household.id], "hi [FIRST_NAME]", sms, now=FIXED_NOW)

        session.refresh(household)
        assert household.last_texted == FIXED_NOW.date()

    def test_skip_counters_and_unknown_ids(
        self,
        session: Session,
        make_household: HouseholdFactory,
        sms: ConsoleSMSProvider,
    ) -> None:
        phoneless = make_household(session, phone_number=None)
        invalid = make_household(session, invalid_phone_number=True)
        good = make_household(session)

        report = send_text_blast(
            session,
            [phoneless.id, invalid.id, 999_999, good.id],
            "hi",
            sms,
            now=FIXED_NOW,
        )

        assert report.sent == 1
        assert report.skipped_no_phone == 1
        assert report.skipped_invalid == 1
        assert report.failed == 0
        assert report.not_sent_over_limit == 0
        assert len(sms.sent) == 1
        session.refresh(phoneless)
        session.refresh(invalid)
        assert phoneless.last_texted is None
        assert invalid.last_texted is None

    def test_max_messages_cap_reports_not_sent_over_limit(
        self,
        session: Session,
        make_household: HouseholdFactory,
        sms: ConsoleSMSProvider,
    ) -> None:
        """Spec 6.2: blast stops at max_messages; the rest stay unsent."""
        households = [make_household(session) for _ in range(5)]

        report = send_text_blast(
            session,
            [h.id for h in households],
            "hi",
            sms,
            max_messages=3,
            now=FIXED_NOW,
        )

        assert report.sent == 3
        assert report.not_sent_over_limit == 2
        assert len(sms.sent) == 3
        for sent_household in households[:3]:
            session.refresh(sent_household)
            assert sent_household.last_texted == FIXED_NOW.date()
        for unsent_household in households[3:]:
            session.refresh(unsent_household)
            assert unsent_household.last_texted is None

    def test_skipped_households_do_not_consume_cap(
        self,
        session: Session,
        make_household: HouseholdFactory,
        sms: ConsoleSMSProvider,
    ) -> None:
        phoneless = make_household(session, phone_number=None)
        good = [make_household(session) for _ in range(2)]

        report = send_text_blast(
            session,
            [phoneless.id, good[0].id, good[1].id],
            "hi",
            sms,
            max_messages=2,
            now=FIXED_NOW,
        )

        assert report.sent == 2
        assert report.skipped_no_phone == 1
        assert report.not_sent_over_limit == 0

    def test_rate_limit_pauses_twice_for_65_sends(
        self,
        session: Session,
        make_household: HouseholdFactory,
        sms: ConsoleSMSProvider,
        no_sleep: RecordingSleeper,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spec 6.2: rate limit of 30 msgs then a pause — 65 sends pause twice."""
        monkeypatch.setattr(settings, "sms_batch_size", 30)
        monkeypatch.setattr(settings, "sms_batch_pause_seconds", 30)
        monkeypatch.setattr(settings, "sms_max_messages", 240)
        households = [make_household(session) for _ in range(65)]

        report = send_text_blast(
            session,
            [h.id for h in households],
            "hi",
            sms,
            sleeper=no_sleep,
            now=FIXED_NOW,
        )

        assert report.sent == 65
        assert no_sleep.calls == [settings.sms_batch_pause_seconds] * 2

    def test_failing_provider_counts_failed_and_keeps_last_texted_unset(
        self, session: Session, make_household: HouseholdFactory
    ) -> None:
        """Failed delivery must not mark the household as texted."""
        household = make_household(session, last_texted=None)
        provider = FailingSMSProvider()

        report = send_text_blast(session, [household.id], "hi", provider, now=FIXED_NOW)

        assert report.sent == 0
        assert report.failed == 1
        assert len(provider.attempts) == 1
        assert report.messages[0].ok is False
        assert report.messages[0].error == "delivery failed"
        session.refresh(household)
        assert household.last_texted is None


class TestConfirmAppointment:
    def test_sets_booked_with_date_and_slot(
        self, session: Session, make_household: HouseholdFactory
    ) -> None:
        """Spec 6.2 steps 3-4: confirmed recipients get Booked + date + slot."""
        household = make_household(session)

        confirm_appointment(
            session, household.id, date(2026, 7, 10), "11:30 AM", now=FIXED_NOW
        )

        session.refresh(household)
        assert household.appointment_status == AppointmentStatus.BOOKED
        assert household.appointment_date == date(2026, 7, 10)
        assert household.appointment_time == "11:30 AM"


class TestRecordOutreachOutcome:
    def test_a4_no_response_times_out_all_open_requests(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Spec 6.4 A4: no response after phone attempts -> timeout, close."""
        household = make_household(session)
        open_goods = make_request(session, household, type="soap")
        delivered = make_request(session, household, type="pads", status=RequestStatus.DELIVERED)
        open_service = make_social_service_request(session, household, type="housing")

        record_outreach_outcome(session, household.id, "no_response_timeout", now=FIXED_NOW)

        session.refresh(open_goods)
        session.refresh(delivered)
        session.refresh(open_service)
        session.refresh(household)
        assert open_goods.status == RequestStatus.TIMEOUT
        assert open_goods.processing_date == FIXED_NOW.date() + timedelta(days=14)
        assert open_service.status == RequestStatus.TIMEOUT
        assert open_service.processing_date == FIXED_NOW.date() + timedelta(days=14)
        assert delivered.status == RequestStatus.DELIVERED
        assert household.invalid_phone_number is False

    def test_a5_wrong_number_also_flags_invalid_phone(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Spec 6.4 A5 + interpretation rule 7: mark invalid, close requests."""
        household = make_household(session)
        request = make_request(session, household, type="soap")

        record_outreach_outcome(session, household.id, "wrong_number", now=FIXED_NOW)

        session.refresh(household)
        session.refresh(request)
        assert household.invalid_phone_number is True
        assert request.status == RequestStatus.TIMEOUT

    def test_a6_no_longer_needed_times_out_with_note(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        """Spec 6.4 A6 + interpretation rule 4: Timeout with a note."""
        household = make_household(session, notes=None)
        request = make_request(session, household, type="bed")

        record_outreach_outcome(
            session, household.id, "no_longer_needed", note="found a bed elsewhere", now=FIXED_NOW
        )

        session.refresh(household)
        session.refresh(request)
        assert request.status == RequestStatus.TIMEOUT
        assert household.notes is not None
        assert "found a bed elsewhere" in household.notes
        assert household.invalid_phone_number is False

    def test_clears_booked_appointment(
        self, session: Session, make_household: HouseholdFactory, make_request: RequestFactory
    ) -> None:
        household = make_household(
            session,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_date=date(2026, 7, 8),
            appointment_time="11:00 AM",
        )
        make_request(session, household, type="soap")

        record_outreach_outcome(session, household.id, "no_response_timeout", now=FIXED_NOW)

        session.refresh(household)
        assert household.appointment_status is None
        assert household.appointment_date is None
        assert household.appointment_time is None

    def test_rejects_unknown_outcome(
        self, session: Session, make_household: HouseholdFactory
    ) -> None:
        household = make_household(session)

        with pytest.raises(ValueError):
            record_outreach_outcome(session, household.id, "ghosted", now=FIXED_NOW)

    def test_rejects_unknown_household(self, session: Session) -> None:
        with pytest.raises(ValueError):
            record_outreach_outcome(session, 999_999, "no_response_timeout", now=FIXED_NOW)


class TestBlastRegressions:
    def test_dry_run_does_not_persist_last_texted(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
        sms: ConsoleSMSProvider,
        no_sleep: RecordingSleeper,
    ) -> None:
        """A preview blast must not poison the recency filters of the real
        send: last_texted stays untouched and nothing is committed."""
        household = make_household(session)
        make_request(session, household)

        report = send_text_blast(
            session,
            [household.id],
            "hi [FIRST_NAME]",
            sms,
            sleeper=no_sleep,
            now=FIXED_NOW,
            dry_run=True,
        )

        assert report.sent == 1  # the preview still reports what would happen
        assert len(sms.sent) == 1
        session.expire_all()
        refreshed = session.get(Household, household.id)
        assert refreshed.last_texted is None

    def test_unknown_household_ids_are_reported(
        self,
        session: Session,
        make_household: HouseholdFactory,
        make_request: RequestFactory,
        sms: ConsoleSMSProvider,
        no_sleep: RecordingSleeper,
    ) -> None:
        """A stale id must be visible in the report, not silently dropped."""
        household = make_household(session)
        make_request(session, household)

        report = send_text_blast(
            session,
            [999_999, household.id],
            "hi",
            sms,
            sleeper=no_sleep,
            now=FIXED_NOW,
        )

        assert report.unknown_household_ids == [999_999]
        assert report.sent == 1
