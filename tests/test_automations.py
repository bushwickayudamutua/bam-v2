"""Tests for the parity automations (vs bam-automation@zakieh/automations)."""

import datetime as dt

import pytest
from sqlmodel import Session, select

from bam.models import (
    FormSubmission,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    FulfilledRequestCount,
)
from bam.schemas import FormSubmissionIn
from bam.services import intake as intake_svc
from bam.services.admin import delete_household, delete_submission
from bam.services.analytics import analyze_fulfilled_requests
from bam.services.consolidate import consolidate_requests
from bam.services.count_closed import count_closed_requests
from bam.services.dedupe import dedupe_households
from bam.services.geocoding import GeocodeResult, NoopGeocoder
from bam.services.mailjet import sync_mailjet_lists
from bam.services.merge import merge_households
from bam.services.metrics import fulfilled_counts
from bam.services.snapshot import snapshot_data
from tests.conftest import FIXED_NOW, days_ago

TODAY = FIXED_NOW.date()


class FakeGeocoder:
    def geocode(self, street, city, zip_code):
        return GeocodeResult(
            cleaned_address="123 CLEANED AVE, Brooklyn, NY 11221",
            address_accuracy="Building",
            bin="3000123",
            plus_code="87G8P2QF+2V",
        )


# --- household merge (merge-households.js) --------------------------------

def test_merge_households_relinks_and_unions(session, make_household, make_request):
    survivor = make_household(
        session, name="Ana", languages=["Inglés / English / 英文"], needs_delivery=False
    )
    other = make_household(
        session,
        name="Ana Lopez",
        phone_number=None,
        languages=["Español / Spanish / 西班牙语"],
        needs_delivery=True,
        last_texted=TODAY,
    )
    r1 = make_request(session, survivor, type="soap")
    r2 = make_request(session, other, type="pads")

    report = merge_households(session, survivor.id, [other.id], now=FIXED_NOW)

    assert report.moved_requests == 1
    assert report.merged_ids == [other.id]
    assert session.get(Household, other.id) is None
    surv = session.get(Household, survivor.id)
    assert set(surv.languages) == {"Inglés / English / 英文", "Español / Spanish / 西班牙语"}
    assert surv.needs_delivery is True
    assert surv.last_texted == TODAY
    assert session.get(Request, r2.id).household_id == survivor.id
    assert session.get(Request, r1.id).household_id == survivor.id


def test_merge_rejects_unknown(session, make_household):
    h = make_household(session)
    with pytest.raises(ValueError):
        merge_households(session, h.id, [999999], now=FIXED_NOW)


# --- request consolidation (consolidate-requests.js) ----------------------

def test_consolidate_keeps_earliest_and_deletes_dupes(session, make_household, make_request):
    h = make_household(session)
    old = make_request(session, h, type="soap", request_opened_at=days_ago(10))
    new = make_request(session, h, type="soap", request_opened_at=days_ago(2))
    keep_other = make_request(session, h, type="pads")

    report = consolidate_requests(session, household_id=h.id, now=FIXED_NOW)

    assert report.requests_removed == 1
    remaining = session.exec(select(Request).where(Request.household_id == h.id)).all()
    assert {r.id for r in remaining} == {old.id, keep_other.id}
    assert session.get(Request, new.id) is None


def test_consolidate_mesh_keeps_best_status_and_address(session, make_household):
    h = make_household(session)

    def mesh(bin_, status, accuracy, addr, opened):
        s = SocialServiceRequest(
            household_id=h.id, type="mesh_internet", status=RequestStatus.OPEN,
            bin=bin_, mesh_status=status, address_accuracy=accuracy, address=addr,
            request_opened_at=opened, status_last_updated_at=opened,
        )
        session.add(s)
        session.commit()
        session.refresh(s)
        return s

    a = mesh("3000123", "Open", "No result", None, days_ago(9))
    b = mesh("3000123", "Step 2 - LOS Confirmed", "Building", "10 Real St", days_ago(3))

    report = consolidate_requests(session, household_id=h.id, now=FIXED_NOW)

    assert report.mesh_removed == 1
    survivor = session.get(SocialServiceRequest, a.id)  # earliest = survivor
    assert survivor is not None
    assert session.get(SocialServiceRequest, b.id) is None
    assert survivor.mesh_status == "Step 2 - LOS Confirmed"  # best rank kept
    assert survivor.address == "10 Real St"  # best-accuracy bundle kept


# --- count-closed-requests.js --------------------------------------------

def test_count_closed_tallies_and_optionally_deletes(session, make_household, make_request):
    h = make_household(session)
    r = make_request(
        session, h, type="soap", status=RequestStatus.DELIVERED,
        status_last_updated_at=FIXED_NOW,
    )

    # default: count, keep
    report = count_closed_requests(session, delete=False, now=FIXED_NOW)
    assert report.counted == 1 and report.deleted == 0
    assert session.get(Request, r.id) is not None
    rows = fulfilled_counts(session)
    assert any(row.request_type == "soap" and row.count >= 1 for row in rows)

    # delete=True removes them
    report2 = count_closed_requests(session, delete=True, now=FIXED_NOW)
    assert report2.deleted == 1
    assert session.get(Request, r.id) is None


def test_count_closed_mesh_dedups_by_phone(session, make_household):
    h = make_household(session, phone_number="+17185550001")
    for _ in range(3):  # 3 installs, same phone -> counts once
        s = SocialServiceRequest(
            household_id=h.id, type="mesh_internet", status=RequestStatus.DELIVERED,
            status_last_updated_at=FIXED_NOW,
        )
        session.add(s)
    session.commit()

    report = count_closed_requests(session, delete=False, now=FIXED_NOW)
    counts = {r.request_type: r.count for r in fulfilled_counts(session)}
    assert counts.get("mesh_internet") == 1
    assert report.counted == 1


# --- dedupe households (DedupeAirtableViews) ------------------------------

def test_dedupe_merges_same_hash_households(session, make_household, make_request):
    from bam.validation import hash_phone

    # An active household and an anonymized one (phone nulled, hash kept) for
    # the same person — the realistic duplicate our unique phone constraint
    # still allows.
    shared = hash_phone("+17185559999")
    a = make_household(session, phone_number="+17185559999", phone_hash=shared, name="A")
    b = make_household(session, phone_number=None, phone_hash=shared, name="B")
    make_request(session, b, type="soap")

    report = dedupe_households(session, now=FIXED_NOW)

    assert report.clusters_merged == 1
    assert report.households_removed == 1
    survivor_id = min(a.id, b.id)
    surv = session.get(Household, survivor_id)
    assert surv is not None
    assert session.get(Household, max(a.id, b.id)) is None
    # The survivor keeps a phone number after the merge.
    assert surv.phone_number == "+17185559999"


# --- geocoding + live mesh at intake -------------------------------------

def test_intake_geocodes_furniture_and_creates_mesh(session):
    result = intake_svc.intake_and_process(
        session,
        FormSubmissionIn(
            phone_number="+17185550142",
            furniture_items=["Muebles / Furniture / 家具"],
            social_service_requests=["internet"],
            street_address="1 Test St",
            city_state="Brooklyn, NY",
            zip_code="11221",
        ),
        now=FIXED_NOW,
        geocoder=FakeGeocoder(),
    )
    furn = session.exec(select(Request).where(Request.type == "furniture")).one()
    assert furn.bin == "3000123"
    assert furn.geocode == "87G8P2QF+2V"
    assert furn.address_accuracy == "Building"
    assert furn.address == "123 CLEANED AVE, Brooklyn, NY 11221"

    mesh = session.exec(
        select(SocialServiceRequest).where(SocialServiceRequest.type == "mesh_internet")
    ).one()
    assert mesh.mesh_status == "Open"
    assert mesh.bin == "3000123"
    assert mesh.address_accuracy == "Building"


def test_noop_geocoder_passthrough():
    geo = NoopGeocoder().geocode("1 A St", "Brooklyn, NY", "11221")
    assert geo.cleaned_address == "1 A St, Brooklyn, NY, 11221"
    assert geo.bin is None


# --- hard deletes ---------------------------------------------------------

def test_delete_household_removes_requests(session, make_household, make_request):
    h = make_household(session)
    make_request(session, h, type="soap")
    out = delete_household(session, h.id)
    assert out["requests"] == 1
    assert session.get(Household, h.id) is None
    assert session.exec(select(Request)).all() == []
    with pytest.raises(ValueError):
        delete_household(session, 999999)


def test_delete_submission(session):
    sub = intake_svc.submit_form(session, FormSubmissionIn(phone_number="+17185550142"))
    delete_submission(session, sub.id)
    assert session.get(FormSubmission, sub.id) is None
    with pytest.raises(ValueError):
        delete_submission(session, 999999)


# --- mailjet (dry) + snapshot (local) + analytics ------------------------

def test_mailjet_dry_run(session, make_household):
    make_household(session, email="a@example.com", email_error=None)
    make_household(session, email=None)
    report = sync_mailjet_lists(session)
    assert report.dry_run is True
    assert report.eligible == 1


def test_snapshot_writes_local_json(session, make_household, tmp_path, monkeypatch):
    from bam.config import settings

    monkeypatch.setattr(settings, "snapshot_dir", str(tmp_path))
    monkeypatch.setattr(settings, "s3_bucket", "")
    make_household(session, name="Snap")

    report = snapshot_data(session, now=FIXED_NOW)
    assert report.counts["households"] == 1
    assert (tmp_path).exists()
    files = list(tmp_path.glob("bam-snapshot-*.json"))
    assert len(files) == 1


def test_analytics_shape(session, make_household, make_request):
    h = make_household(session)
    r = make_request(session, h, type="soap")
    from bam.services.checkin import fulfill_requests

    fulfill_requests(session, request_ids=[r.id], now=FIXED_NOW)
    make_request(session, h, type="pads")  # open

    report = analyze_fulfilled_requests(session)
    assert report.total_fulfilled == 1
    assert report.total_open == 1
    assert any(e["type"] == "soap" for e in report.fulfilled_by_type)
    assert any(e["type"] == "pads" for e in report.open_by_type)
