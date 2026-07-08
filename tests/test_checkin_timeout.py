"""Per-item Timeout at check-in + last-4-digits phone search
(bam/services/checkin.py). From the volunteer-checkin-guide Step 4 / Step 2."""

from __future__ import annotations

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
    fulfill_requests,
    search_by_phone_suffix,
    timeout_requests,
)
from tests.conftest import FIXED_NOW

TODAY = FIXED_NOW.date()


def _hh(session, **kw):
    hh = Household(**kw)
    session.add(hh)
    session.commit()
    session.refresh(hh)
    return hh


def _req(session, hh, type="soap", status=RequestStatus.OPEN):
    r = Request(household_id=hh.id, type=type, status=status)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def test_declined_item_times_out_with_14_day_window(session: Session):
    hh = _hh(session, name="Ana")
    r = _req(session, hh)
    (out,) = timeout_requests(session, request_ids=[r.id], now=FIXED_NOW)
    assert out.status == RequestStatus.TIMEOUT
    assert out.processing_date == TODAY + timedelta(days=14)


def test_pots_pans_timeout_uses_14_not_30_days(session: Session):
    hh = _hh(session, name="Bea")
    r = _req(session, hh, type="pots_pans")
    (out,) = timeout_requests(session, request_ids=[r.id], now=FIXED_NOW)
    assert out.processing_date == TODAY + timedelta(days=14)  # 30d is Delivered-only


def test_timeout_does_not_increment_fulfilled_count(session: Session):
    hh = _hh(session, name="Cid")
    r = _req(session, hh)
    timeout_requests(session, request_ids=[r.id], now=FIXED_NOW)
    assert session.exec(select(FulfilledRequestCount)).all() == []


def test_timeout_handles_social_service_requests(session: Session):
    hh = _hh(session, name="Dev")
    s = SocialServiceRequest(household_id=hh.id, type="housing", status=RequestStatus.OPEN)
    session.add(s)
    session.commit()
    session.refresh(s)
    (out,) = timeout_requests(session, social_service_request_ids=[s.id], now=FIXED_NOW)
    assert out.status == RequestStatus.TIMEOUT
    assert out.processing_date == TODAY + timedelta(days=14)


def test_timeout_leaves_unselected_open_and_skips_delivered(session: Session):
    hh = _hh(session, name="Eve")
    open_kept = _req(session, hh, type="soap")
    declined = _req(session, hh, type="clothing")
    delivered = _req(session, hh, type="microwave", status=RequestStatus.DELIVERED)
    timeout_requests(session, request_ids=[declined.id, delivered.id], now=FIXED_NOW)
    session.refresh(open_kept)
    session.refresh(declined)
    session.refresh(delivered)
    assert open_kept.status == RequestStatus.OPEN and open_kept.processing_date is None
    assert declined.status == RequestStatus.TIMEOUT
    assert delivered.status == RequestStatus.DELIVERED  # not flipped


def test_timeout_does_not_touch_household_appointment(session: Session):
    hh = _hh(session, name="Fay", appointment_status=AppointmentStatus.BOOKED, missed_appointment_count=1)
    r = _req(session, hh)
    timeout_requests(session, request_ids=[r.id], now=FIXED_NOW)
    session.refresh(hh)
    assert hh.appointment_status == AppointmentStatus.BOOKED
    assert hh.missed_appointment_count == 1


def test_timeout_unknown_id_raises(session: Session):
    with pytest.raises(ValueError):
        timeout_requests(session, request_ids=[999999])


def test_last_four_digits_search(session: Session):
    a = _hh(session, name="Aa", phone_number="+17185550142")
    _hh(session, name="Bb", phone_number="+12125559142")
    hits = search_by_phone_suffix(session, "0142")
    assert [h.id for h in hits] == [a.id]


def test_phone_suffix_ignores_non_digits_and_empty(session: Session):
    a = _hh(session, name="Aa", phone_number="+17185550142")
    assert [h.id for h in search_by_phone_suffix(session, "(0142)")] == [a.id]
    assert search_by_phone_suffix(session, "") == []
    assert search_by_phone_suffix(session, "abc") == []


def test_phone_suffix_skips_null_phone(session: Session):
    _hh(session, name="Anon", phone_number=None)
    assert search_by_phone_suffix(session, "0000") == []
