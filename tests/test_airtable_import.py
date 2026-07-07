"""Airtable V2 base migration tests (bam/services/airtable_import.py).

Uses a fake record source shaped exactly like Airtable API payloads
({"id", "createdTime", "fields"}), so no network is involved.
"""

from datetime import date, datetime, timezone

from sqlmodel import Session, select

from bam.models import (
    AppointmentStatus,
    Distro,
    FormSubmission,
    FulfilledRequestCount,
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
)
from bam.services.airtable_import import import_base
from bam.validation import hash_phone

FIXED_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


class FakeSource:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self._tables = tables

    def schema(self) -> list[dict]:
        return [{"id": f"tbl{i}", "name": name} for i, name in enumerate(self._tables)]

    def records(self, table: str):
        return iter(self._tables.get(table, []))


def rec(rec_id: str, created: str = "2026-05-01T10:00:00.000Z", **fields) -> dict:
    return {"id": rec_id, "createdTime": created, "fields": fields}


def base_tables(**overrides) -> dict[str, list[dict]]:
    tables = {
        "Households": [],
        "Requests": [],
        "Social Service Requests": [],
        "Distros": [],
        "Fulfilled Request Count": [],
        "Assistance Request Form Submissions": [],
    }
    tables.update(overrides)
    return tables


def test_household_import_maps_fields_and_normalizes_phone(session: Session) -> None:
    source = FakeSource(
        base_tables(
            Households=[
                rec(
                    "recH1",
                    **{
                        "Name": "Ana Lopez",
                        "Phone Number": "(718) 555-0100",
                        "Email": "ana@example.com",
                        "Languages": ["Español", "English"],
                        "Notes": "prefers texts",
                        "Appointment Date": "2026-07-03",
                        "Appointment Time": "11:00 AM",
                        "Appointment Status": "Booked",
                        "Last Texted": "2026-06-20",
                    },
                )
            ]
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.households.created == 1
    household = session.exec(select(Household)).one()
    assert household.airtable_id == "recH1"
    assert household.phone_number == "+17185550100"
    assert household.phone_hash == hash_phone("+17185550100")
    assert household.languages == ["Español", "English"]
    assert household.appointment_status == AppointmentStatus.BOOKED
    assert household.appointment_date == date(2026, 7, 3)
    assert household.last_texted == date(2026, 6, 20)
    assert household.invalid_phone_number is False


def test_duplicate_normalized_phone_reported_and_second_loses_phone(
    session: Session,
) -> None:
    source = FakeSource(
        base_tables(
            Households=[
                rec("recA", **{"Name": "First", "Phone Number": "+17185550101"}),
                rec("recB", **{"Name": "Second", "Phone Number": "(718) 555-0101"}),
            ]
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.duplicate_phone_airtable_ids == ["recB"]
    first = session.exec(select(Household).where(Household.airtable_id == "recA")).one()
    second = session.exec(select(Household).where(Household.airtable_id == "recB")).one()
    assert first.phone_number == "+17185550101"
    assert second.phone_number is None
    assert "already claimed" in (second.notes or "")


def test_request_import_normalizes_types_and_derives_dates(session: Session) -> None:
    source = FakeSource(
        base_tables(
            Households=[rec("recH1", **{"Phone Number": "+17185550102"})],
            Requests=[
                rec(
                    "recR1",
                    **{
                        "Household": ["recH1"],
                        "Type": "Ollas y sartenes / Pots & Pans / 锅碗瓢盆",
                        "Status": "Open",
                        "Legacy Date Submitted": "2026-04-01",
                    },
                ),
                rec(
                    "recR2",
                    **{
                        "Household": ["recH1"],
                        "Type": "Mesa de centro / Coffee Table / 咖啡桌",
                        "Status": "Delivered",
                        "Status Last Updated At": "2026-06-01T15:00:00.000Z",
                    },
                ),
                rec(
                    "recR3",
                    **{
                        "Household": ["recH1"],
                        "Type": "Something Unrecognizable",
                        "Status": "Weird Status",
                    },
                ),
            ],
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.requests.created == 3
    by_airtable = {
        r.airtable_id: r for r in session.exec(select(Request)).all()
    }
    pots = by_airtable["recR1"]
    assert pots.type == "pots_pans"
    assert pots.request_opened_at.date() == date(2026, 4, 1)  # legacy date wins
    assert pots.status == RequestStatus.OPEN
    assert pots.processing_date is None

    coffee = by_airtable["recR2"]
    assert coffee.type == "coffee_table"  # production select resolves directly
    assert coffee.status == RequestStatus.DELIVERED
    # No Processing Date in the base -> derived from status change (+14, not
    # +30: coffee_table is a standard-window type).
    assert coffee.processing_date == date(2026, 6, 15)

    odd = by_airtable["recR3"]
    assert odd.type == "Something Unrecognizable"  # raw label preserved
    assert odd.status == RequestStatus.OPEN  # unknown status defaults to Open
    assert "Something Unrecognizable" in report.unmatched_request_types
    assert any("Weird Status" in s for s in report.unknown_statuses)


def test_furniture_requests_table_imports_into_requests(session: Session) -> None:
    """The migration script (bam-automation zakieh/automations) splits bed +
    furniture into a separate "Furniture Requests" table. Those rows are goods
    requests and must land in the Request model, with Geocode preserved."""
    source = FakeSource(
        base_tables(
            Households=[rec("recH1", **{"Phone Number": "+17185550102"})],
            Requests=[
                rec("recR1", **{
                    "Household": ["recH1"],
                    "Type": "Jabón & Productos de baño / Soap & Shower Products / 肥皂和淋浴用品",
                    "Status": "Open",
                }),
            ],
            **{"Furniture Requests": [
                rec("recF1", **{
                    "Household": ["recH1"],
                    "Type": "Sofa / Sofa / 沙發",
                    "Status": "Open",
                    "Legacy Date Submitted": "2024-03-05",
                    "Geocode": "87G8P2XR+00",
                }),
                rec("recF2", **{
                    "Household": ["recH1"],
                    "Type": "Colchón individual / Twin Mattress / 單人床墊",
                    "Status": "Open",
                }),
                rec("recF3", **{
                    "Household": ["recH1"],
                    "Type": "Cuna / Crib / 嬰兒床",
                    "Status": "Open",
                }),
            ]},
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.tables_found.get("furniture_requests") == "Furniture Requests"
    by_type = {r.type: r for r in session.exec(select(Request)).all()}
    # soap (Requests) + sofa/twin_mattress/crib (Furniture Requests) all present
    assert set(by_type) == {"soap", "sofa", "twin_mattress", "crib"}
    assert report.requests.created == 4
    assert by_type["sofa"].geocode == "87G8P2XR+00"  # geocode carried over
    assert by_type["sofa"].request_opened_at.date() == date(2024, 3, 5)


def test_orphaned_request_skipped_and_reported(session: Session) -> None:
    source = FakeSource(
        base_tables(
            Households=[rec("recH1", **{"Phone Number": "+17185550103"})],
            Requests=[rec("recR9", **{"Type": "soap", "Status": "Open"})],
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.requests.skipped == 1
    assert report.orphaned_airtable_ids == ["recR9"]
    assert session.exec(select(Request)).all() == []


def test_social_service_request_import(session: Session) -> None:
    source = FakeSource(
        base_tables(
            Households=[rec("recH1", **{"Phone Number": "+17185550104"})],
            **{
                "Social Service Requests": [
                    rec(
                        "recS1",
                        **{
                            "Household": ["recH1"],
                            "Type": "Internet",
                            "Status": "Open",
                            "Internet Access": ["No tengo acceso al red / I don't have internet access at all"],
                            "Roof Accessible?": True,
                        },
                    )
                ]
            },
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.social_service_requests.created == 1
    social = session.exec(select(SocialServiceRequest)).one()
    assert social.type == "internet"
    assert social.roof_accessible is True
    assert len(social.internet_access) == 1


def test_distro_and_fulfilled_count_import(session: Session) -> None:
    source = FakeSource(
        base_tables(
            Distros=[
                rec(
                    "recD1",
                    **{
                        "Date & Time": "2026-06-15T15:00:00.000Z",
                        "Location": "Maria Hernandez Park",
                        "Duration": 7200,  # Airtable durations are seconds
                        "Appointments": 60,
                    },
                ),
                rec("recD2"),  # no date -> skipped
            ],
            **{
                "Fulfilled Request Count": [
                    rec(
                        "recF1",
                        **{
                            "Date": "2026-06-15",
                            "Jabón & Productos de baño / Soap & Shower Products / 肥皂和淋浴用品": 12,
                            "Unknown Column Type": 3,
                        },
                    )
                ]
            },
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.distros.created == 1
    assert report.distros.skipped == 1
    distro = session.exec(select(Distro)).one()
    assert distro.duration_minutes == 120
    assert distro.appointments == "60"

    rows = {
        r.request_type: r.count
        for r in session.exec(select(FulfilledRequestCount)).all()
    }
    assert rows == {"soap": 12, "Unknown Column Type": 3}
    assert "Unknown Column Type" in report.unmatched_request_types


def test_form_submission_link_controls_processed_state(session: Session) -> None:
    source = FakeSource(
        base_tables(
            Households=[rec("recH1", **{"Phone Number": "+17185550105"})],
            **{
                "Assistance Request Form Submissions": [
                    rec(
                        "recFS1",
                        **{
                            "Name": "Ana",
                            "Phone Number": "718-555-0105",
                            "Households": ["recH1"],
                            "Request Types": ["soap"],
                        },
                    ),
                    rec(
                        "recFS2",
                        **{"Name": "New Person", "Phone Number": "718-555-0999"},
                    ),
                ]
            },
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.form_submissions.created == 2
    linked = session.exec(
        select(FormSubmission).where(FormSubmission.airtable_id == "recFS1")
    ).one()
    unlinked = session.exec(
        select(FormSubmission).where(FormSubmission.airtable_id == "recFS2")
    ).one()
    assert linked.household_id is not None
    assert linked.processed_at is not None  # requests already exist in the base
    assert unlinked.household_id is None
    assert unlinked.processed_at is None  # bam process-intake can pick it up


def test_import_is_idempotent(session: Session) -> None:
    tables = base_tables(
        Households=[rec("recH1", **{"Name": "Ana", "Phone Number": "+17185550106"})],
        Requests=[
            rec("recR1", **{"Household": ["recH1"], "Type": "soap", "Status": "Open"})
        ],
    )

    first = import_base(session, FakeSource(tables), now=FIXED_NOW)
    second = import_base(session, FakeSource(tables), now=FIXED_NOW)

    assert first.households.created == 1
    assert second.households.created == 0
    assert second.households.updated == 1
    assert second.requests.created == 0
    assert second.requests.updated == 1
    assert len(session.exec(select(Household)).all()) == 1
    assert len(session.exec(select(Request)).all()) == 1


def test_mesh_requests_import_as_social_service_with_status_buckets(
    session: Session,
) -> None:
    """Mesh pipeline rows land as mesh_internet social-service requests with
    lifecycle-bucketed statuses and the raw pipeline detail on notes."""
    source = FakeSource(
        base_tables(
            Households=[rec("recH1", **{"Phone Number": "+17185550107"})],
            **{
                "Mesh Requests": [
                    rec(
                        "recM1",
                        **{
                            "Household": ["recH1"],
                            "Status": "YAY! MESH INSTALLED!",
                            "Building Identification Number": 3123456,
                            "Address Accuracy": "Building",
                        },
                    ),
                    rec(
                        "recM2",
                        **{
                            "Household": ["recH1"],
                            "Status": "Cannot Install - No Roof Access",
                        },
                    ),
                    rec(
                        "recM3",
                        **{
                            "Household": ["recH1"],
                            "Status": "Step 2 - LOS Confirmed",
                        },
                    ),
                ]
            },
        )
    )

    report = import_base(session, source, now=FIXED_NOW)

    assert report.mesh_requests.created == 3
    by_airtable = {
        s.airtable_id: s
        for s in session.exec(select(SocialServiceRequest)).all()
    }
    assert all(s.type == "mesh_internet" for s in by_airtable.values())
    assert by_airtable["recM1"].status == RequestStatus.DELIVERED
    assert "BIN 3123456" in by_airtable["recM1"].notes
    assert by_airtable["recM2"].status == RequestStatus.TIMEOUT
    assert by_airtable["recM3"].status == RequestStatus.OPEN
    assert "[mesh status] Step 2 - LOS Confirmed" in by_airtable["recM3"].notes


def test_invalid_phone_household_gets_raw_hash(session: Session) -> None:
    source = FakeSource(
        base_tables(Households=[rec("recH1", **{"Phone Number": "not a phone"})])
    )

    import_base(session, source, now=FIXED_NOW)

    household = session.exec(select(Household)).one()
    assert household.phone_number is None
    assert household.invalid_phone_number is True
    assert household.phone_hash == hash_phone("not a phone")
