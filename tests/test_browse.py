"""Browse/list endpoints (bam/api/routes/browse.py) — the console's parity
views (Appointments, Look up, Furniture, Social Services).

Uses the TestClient + a seeded in-memory DB (conftest fixtures)."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session

from bam.models import (
    AppointmentStatus,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    local_date,
)


def _hh(session: Session, name: str, **kw) -> Household:
    hh = Household(name=name, phone_number=kw.pop("phone", None), **kw)
    session.add(hh)
    session.commit()
    session.refresh(hh)
    return hh


def test_appointments_lists_the_day_queue_ordered_by_time(client, session):
    today = local_date()
    a = _hh(session, "Ana", phone="+17185550001",
            appointment_date=today, appointment_time="11:00 AM",
            appointment_status=AppointmentStatus.BOOKED)
    _hh(session, "Bea", phone="+17185550002",
        appointment_date=today, appointment_time="9:00 AM",
        appointment_status=AppointmentStatus.CHECKED_IN)
    _hh(session, "Cid", phone="+17185550003",
        appointment_date=date(2024, 1, 1), appointment_time="10:00 AM")
    # an open request so the count is non-zero
    session.add(Request(household_id=a.id, type="soap", status=RequestStatus.OPEN))
    session.commit()

    rows = client.get("/appointments").json()
    assert [r["name"] for r in rows] == ["Bea", "Ana"]  # 9am before 11am
    assert rows[1]["open_request_count"] == 1
    assert rows[0]["appointment_status"] == "Checked-in"

    # explicit date selects the other day
    other = client.get("/appointments", params={"date": "2024-01-01"}).json()
    assert [r["name"] for r in other] == ["Cid"]


def test_households_browse_search_and_paginate(client, session):
    for i, nm in enumerate(["Alice", "Bob", "Bianca"]):
        _hh(session, nm, phone=f"+1718555010{i}")

    page = client.get("/households").json()
    assert page["total"] == 3
    assert [i["name"] for i in page["items"]] == ["Alice", "Bianca", "Bob"]  # by name

    # search matches name OR phone (case-insensitive)
    hits = client.get("/households", params={"query": "bi"}).json()
    assert [i["name"] for i in hits["items"]] == ["Bianca"]

    # pagination
    p1 = client.get("/households", params={"limit": 2, "offset": 0}).json()
    p2 = client.get("/households", params={"limit": 2, "offset": 2}).json()
    assert len(p1["items"]) == 2 and p1["total"] == 3
    assert len(p2["items"]) == 1 and p2["offset"] == 2


def test_requests_filter_by_category_type_and_status(client, session):
    hh = _hh(session, "Rosa", phone="+17185550200")
    session.add_all([
        Request(household_id=hh.id, type="sofa", status=RequestStatus.OPEN,
                address="123 Bleecker St", geocode="87G8P2XR+00", address_accuracy="Building"),
        Request(household_id=hh.id, type="crib", status=RequestStatus.DELIVERED),
        Request(household_id=hh.id, type="microwave", status=RequestStatus.OPEN),  # kitchen
    ])
    session.commit()

    furn = client.get("/requests", params={"category": "furniture"}).json()
    assert furn["total"] == 2
    assert {r["type"] for r in furn["items"]} == {"sofa", "crib"}
    assert all(r["category"] == "furniture" for r in furn["items"])
    sofa = next(r for r in furn["items"] if r["type"] == "sofa")
    assert sofa["address"] == "123 Bleecker St" and sofa["geocode"] == "87G8P2XR+00"
    assert sofa["household_name"] == "Rosa"

    # status filter within category
    open_furn = client.get("/requests", params={"category": "furniture", "status": "Open"}).json()
    assert [r["type"] for r in open_furn["items"]] == ["sofa"]

    # kitchen category is separate
    kitchen = client.get("/requests", params={"category": "kitchen"}).json()
    assert [r["type"] for r in kitchen["items"]] == ["microwave"]


def test_social_service_requests_by_type_including_mesh(client, session):
    hh = _hh(session, "Luis", phone="+17185550300")
    session.add_all([
        SocialServiceRequest(household_id=hh.id, type="english_classes", status=RequestStatus.OPEN),
        SocialServiceRequest(household_id=hh.id, type="mesh_internet", status=RequestStatus.OPEN,
                             mesh_status="Step 1 - Interested in Mesh", bin="3000001",
                             address_accuracy="Building",
                             internet_access=["El red es caro / My internet is expensive / 我的網絡很貴"]),
    ])
    session.commit()

    eng = client.get("/social-service-requests", params={"type": "english_classes"}).json()
    assert eng["total"] == 1 and eng["items"][0]["household_name"] == "Luis"

    mesh = client.get("/social-service-requests", params={"type": "mesh_internet"}).json()
    row = mesh["items"][0]
    assert row["mesh_status"] == "Step 1 - Interested in Mesh"
    assert row["bin"] == "3000001"
    assert row["internet_access"] == ["El red es caro / My internet is expensive / 我的網絡很貴"]

    # no type filter returns both
    both = client.get("/social-service-requests").json()
    assert both["total"] == 2


def test_invalid_status_is_rejected(client):
    assert client.get("/requests", params={"status": "Nope"}).status_code == 422
