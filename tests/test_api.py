"""End-to-end API tests (CONTRACT.md `bam/api/`).

Drives the whole recipient journey through the HTTP layer: intake -> lookup
-> outreach list -> text blast -> appointment -> check-in -> fulfillment ->
metrics, plus distros/no-shows, the scheduled-job endpoints, and the
outreach-outcome error mapping (400 vs 404).

Job routes run against the real clock (they take no ``now``), so seeded rows
use offsets relative to ``datetime.now`` rather than ``FIXED_NOW``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import select

from bam.config import settings
from bam.models import (
    AppointmentStatus,
    FormSubmission,
    FulfilledRequestCount,
    RequestStatus,
)
from bam.request_types import label_for

PHONE_RAW = "718-555-0142"
PHONE_E164 = "+17185550142"


def _intake_payload(**overrides) -> dict:
    payload = {
        "name": "Maria Lopez",
        "phone_number": PHONE_RAW,
        "email": "maria@example.com",
        "languages": ["es"],
        "request_types": ["Soap & Shower Products", "groceries"],
        "social_service_requests": ["Housing"],
    }
    payload.update(overrides)
    return payload


def test_recipient_journey(client, session):
    # --- Intake (spec 6.1): POST /intake/submissions -> 201 IntakeResult ---
    response = client.post("/intake/submissions", json=_intake_payload())
    assert response.status_code == 201
    intake = response.json()
    assert intake["created_household"] is True
    assert intake["phone_valid"] is True
    assert isinstance(intake["submission_id"], int)
    assert len(intake["created_request_ids"]) == 2
    assert len(intake["created_social_service_request_ids"]) == 1
    assert intake["skipped_duplicate_types"] == []
    assert intake["unknown_types"] == []
    household_id = intake["household_id"]

    # --- Lookup (spec 6.3): phone normalized to E.164, CheckinView shape ---
    response = client.get("/households/lookup", params={"phone": "(718) 555-0142"})
    assert response.status_code == 200
    view = response.json()
    assert view["household"]["id"] == household_id
    assert view["household"]["name"] == "Maria Lopez"
    assert view["household"]["phone_number"] == PHONE_E164
    assert view["household"]["invalid_phone_number"] is False
    assert {r["type"] for r in view["open_requests"]} == {"soap", "groceries"}
    assert [s["type"] for s in view["open_social_service_requests"]] == ["housing"]
    assert all(r["status"] == "Open" for r in view["open_requests"])
    soap_id = next(r["id"] for r in view["open_requests"] if r["type"] == "soap")

    # Unknown phone -> 404.
    response = client.get("/households/lookup", params={"phone": "+17185550999"})
    assert response.status_code == 404

    # --- Outreach list (spec 6.2 step 1) ---
    response = client.post("/outreach/list", json={})
    assert response.status_code == 200
    candidates = response.json()
    assert [c["household_id"] for c in candidates] == [household_id]
    assert candidates[0]["phone_number"] == PHONE_E164
    assert candidates[0]["open_request_types"] == ["groceries", "soap"]

    # --- Text blast (spec 5 send_sms; console provider by default) ---
    response = client.post(
        "/outreach/blast",
        json={
            "household_ids": [household_id],
            "template": "Hola [FIRST_NAME], pide en [REQUEST_URL]",
        },
    )
    assert response.status_code == 200
    blast = response.json()
    assert blast["sent"] == 1
    assert blast["failed"] == 0
    assert blast["skipped_invalid"] == 0
    assert blast["skipped_no_phone"] == 0
    message = blast["messages"][0]
    assert message["to"] == PHONE_E164
    assert message["ok"] is True
    assert "Maria" in message["body"]
    assert settings.request_form_url in message["body"]

    # last_texted is visible via a follow-up lookup.
    response = client.get("/households/lookup", params={"phone": PHONE_E164})
    assert response.json()["household"]["last_texted"] is not None

    # --- Appointment (spec 6.2 steps 3-4) ---
    response = client.post(
        f"/households/{household_id}/appointment",
        json={"appointment_date": "2026-08-01", "appointment_time": "11:00 AM"},
    )
    assert response.status_code == 200
    booked = response.json()
    assert booked["appointment_status"] == "Booked"
    assert booked["appointment_date"] == "2026-08-01"
    assert booked["appointment_time"] == "11:00 AM"

    # Booked households drop out of the outreach list.
    response = client.post("/outreach/list", json={})
    assert response.json() == []

    # --- Check-in (spec 6.3) ---
    response = client.post(f"/households/{household_id}/checkin")
    assert response.status_code == 200
    checked_in = response.json()
    assert checked_in["appointment_status"] == "Checked-in"
    assert checked_in["missed_appointment_count"] == 0
    assert checked_in["last_attended"] is not None

    # --- Fulfillment (spec 6.3 step 4): deliver the soap request only ---
    response = client.post("/requests/fulfill", json={"request_ids": [soap_id]})
    assert response.status_code == 200
    fulfilled = response.json()
    delivered = fulfilled["requests"]
    assert [r["id"] for r in delivered] == [soap_id]
    assert delivered[0]["status"] == "Delivered"

    session.expire_all()
    counted = session.exec(
        select(FulfilledRequestCount).where(FulfilledRequestCount.request_type == "soap")
    ).one()
    assert counted.count == 1

    # --- Metrics reflect the delivery: soap gone, groceries + housing open ---
    response = client.get("/metrics/open-requests")
    assert response.status_code == 200
    data = response.json()
    assert {e["type"]: e["count"] for e in data["counts"]} == {
        "groceries": 1,
        "housing": 1,
    }
    assert all(e["label"] == label_for(e["type"]) for e in data["counts"])
    assert "generated_at" in data


def test_distros_and_no_shows(client):
    intake = client.post(
        "/intake/submissions",
        json=_intake_payload(
            name="Wei Chen",
            phone_number="718-555-0177",
            request_types=["soap"],
            social_service_requests=[],
        ),
    ).json()
    household_id = intake["household_id"]

    # --- POST /distros + GET /distros ---
    response = client.post(
        "/distros",
        json={
            "date_time": "2026-08-01T11:00:00",
            "location": "BAM Plaza",
            "duration_minutes": 120,
        },
    )
    assert response.status_code == 200
    distro = response.json()
    assert distro["id"] is not None
    assert distro["location"] == "BAM Plaza"

    response = client.get("/distros")
    assert response.status_code == 200
    listed = response.json()
    assert [d["id"] for d in listed] == [distro["id"]]
    assert listed[0]["date_time"].startswith("2026-08-01T11:00:00")

    # --- First no-show: Missed, count 1, nothing timed out yet (A2) ---
    client.post(
        f"/households/{household_id}/appointment",
        json={"appointment_date": "2026-08-01", "appointment_time": "11:00 AM"},
    )
    response = client.post("/distros/no-shows", json={"distro_date": "2026-08-01"})
    assert response.status_code == 200
    report = response.json()
    assert report["missed_household_ids"] == [household_id]
    assert report["timed_out_household_ids"] == []

    view = client.get("/households/lookup", params={"phone": "+17185550177"}).json()
    assert view["household"]["appointment_status"] == "Missed"
    assert view["household"]["missed_appointment_count"] == 1
    assert view["household"]["appointment_date"] is None
    assert len(view["open_requests"]) == 1

    # --- Second no-show hits max_missed_appointments: requests time out (A3) ---
    client.post(
        f"/households/{household_id}/appointment",
        json={"appointment_date": "2026-08-08", "appointment_time": "11:00 AM"},
    )
    response = client.post("/distros/no-shows", json={"distro_date": "2026-08-08"})
    report = response.json()
    assert report["missed_household_ids"] == [household_id]
    assert report["timed_out_household_ids"] == [household_id]

    view = client.get("/households/lookup", params={"phone": "+17185550177"}).json()
    assert view["household"]["missed_appointment_count"] == 2
    assert view["open_requests"] == []
    assert view["open_social_service_requests"] == []


def test_jobs_expire(client, session, make_household, make_request):
    now = datetime.now(timezone.utc)
    stale_household = make_household(session)
    stale = make_request(
        session, stale_household, type="soap", request_opened_at=now - timedelta(days=20)
    )
    fresh_household = make_household(session)
    fresh = make_request(session, fresh_household, type="soap")
    booked = make_household(
        session,
        appointment_status=AppointmentStatus.BOOKED,
        appointment_date=now.date(),
    )
    protected = make_request(
        session, booked, type="soap", request_opened_at=now - timedelta(days=20)
    )

    response = client.post("/jobs/expire")
    assert response.status_code == 200
    report = response.json()
    assert report["timed_out_request_ids"] == [stale.id]
    assert report["timed_out_social_service_request_ids"] == []
    assert fresh.id not in report["timed_out_request_ids"]
    assert protected.id not in report["timed_out_request_ids"]

    session.expire_all()
    assert session.get(type(stale), stale.id).status == RequestStatus.TIMEOUT


def test_jobs_website_data_writes_tmp_path(
    client, session, make_household, make_request, tmp_path, monkeypatch
):
    import json

    target = tmp_path / "website_request_data.json"
    monkeypatch.setattr(settings, "website_data_path", str(target))

    household = make_household(session)
    make_request(session, household, type="soap")

    response = client.post("/jobs/website-data")
    assert response.status_code == 200
    data = response.json()
    assert [(e["type"], e["count"]) for e in data["counts"]] == [("soap", 1)]

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == data


def test_jobs_scrub_pii(client, session, make_household, make_request):
    now = datetime.now(timezone.utc)
    old = make_household(session, updated_at=now - timedelta(days=40), notes="call after 5")
    make_request(
        session,
        old,
        type="soap",
        status=RequestStatus.TIMEOUT,
        processing_date=now.date() - timedelta(days=1),
        street_address="123 Main St",
    )
    submission = FormSubmission(
        name="Old Submitter",
        phone_number="718-555-0100",
        processed_at=now - timedelta(days=39),
        created_at=now - timedelta(days=40),
        household_id=old.id,
    )
    session.add(submission)
    session.commit()

    response = client.post("/jobs/scrub-pii")
    assert response.status_code == 200
    report = response.json()
    assert report["households_anonymized"] == 1
    assert report["requests_scrubbed"] == 1
    assert report["submissions_scrubbed"] == 1

    session.expire_all()
    session.refresh(old)
    assert old.phone_number is None
    assert old.name is None
    assert old.notes is None
    assert old.phone_hash is not None
    assert old.anonymized_at is not None


def test_outreach_outcome_error_mapping(client):
    intake = client.post(
        "/intake/submissions",
        json=_intake_payload(phone_number="718-555-0188", social_service_requests=[]),
    ).json()
    household_id = intake["household_id"]

    # Bad outcome value -> 400.
    response = client.post(
        f"/households/{household_id}/outreach-outcome", json={"outcome": "ghosted"}
    )
    assert response.status_code == 400

    # Unknown household with a valid outcome -> 404.
    response = client.post(
        "/households/999999/outreach-outcome", json={"outcome": "wrong_number"}
    )
    assert response.status_code == 404

    # Sanity: a valid outcome on a real household still works (A5).
    response = client.post(
        f"/households/{household_id}/outreach-outcome",
        json={"outcome": "wrong_number", "note": "reached a stranger"},
    )
    assert response.status_code == 200
    assert response.json()["invalid_phone_number"] is True

    # An invalid outcome whose text happens to contain "not found" is still a
    # 400: error mapping is by exception type, not message substring.
    response = client.post(
        f"/households/{household_id}/outreach-outcome",
        json={"outcome": "number not found"},
    )
    assert response.status_code == 400


def test_catalog_endpoint(client):
    """GET /catalog: the vocabulary the console builds its UI from."""
    response = client.get("/catalog")
    assert response.status_code == 200
    catalog = response.json()
    goods_keys = {t["key"] for t in catalog["goods"]}
    social_keys = {t["key"] for t in catalog["social_services"]}
    assert {"soap", "pots_pans", "sofa"} <= goods_keys
    assert len(social_keys) >= 12  # spec 4: "Service type (12 options)"
    assert {"tenant_legal", "housing", "pet_assistance", "internet"} <= social_keys
    assert "Español / Spanish / 西班牙语" in catalog["languages"]
    assert all({"key", "label", "category"} <= set(t) for t in catalog["goods"])


def test_name_search_and_view_by_id(client):
    """Spec journey step 5: check in via phone number/name — API path."""
    created = client.post(
        "/intake/submissions",
        json={
            "phone_number": "718-555-0177",
            "name": "Carmen Reyes",
            "request_types": ["soap"],
        },
    ).json()

    matches = client.get("/households/search", params={"name": "carmen"}).json()
    assert [m["id"] for m in matches] == [created["household_id"]]

    view = client.get(f"/households/{created['household_id']}")
    assert view.status_code == 200
    assert view.json()["household"]["name"] == "Carmen Reyes"
    assert [r["type"] for r in view.json()["open_requests"]] == ["soap"]

    assert client.get("/households/999999").status_code == 404


def test_fulfilled_metrics_endpoint(client):
    """Spec 2 goal "track fulfilled vs outstanding": the fulfilled read surface."""
    created = client.post(
        "/intake/submissions",
        json={"phone_number": "718-555-0178", "request_types": ["soap"]},
    ).json()
    request_id = created["created_request_ids"][0]
    client.post("/requests/fulfill", json={"request_ids": [request_id]})

    rows = client.get("/metrics/fulfilled").json()
    assert len(rows) == 1
    assert rows[0]["type"] == "soap"
    assert rows[0]["count"] == 1
    assert "Soap" in rows[0]["label"]

    on_date = rows[0]["date"]
    assert client.get("/metrics/fulfilled", params={"start": on_date, "end": on_date}).json() == rows
    assert client.get("/metrics/fulfilled", params={"start": "2099-01-01"}).json() == []
