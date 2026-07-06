"""One-shot migration of the production Airtable V2 base into this system.

The source base (``appjIo54Z8MWrqhlI``) is the one the spec's section 4 was
documented from, so field names largely match the spec verbatim. The import
is schema-driven and tolerant: tables are located by name, fields are read
through variant-aware helpers, and everything unmappable is *reported*, never
silently dropped.

Idempotent: every imported row carries its ``airtable_id`` and re-runs
update in place (Fulfilled Request Count uses its natural (date, type) key).

Mapping decisions (see also docs/SPEC-MAPPING.md):

- Request types are normalized to catalog keys via ``normalize_type``; a
  label that doesn't resolve keeps its raw Airtable string as the ``type``
  (data is preserved; it just won't match catalog-driven filters) and is
  listed in the report.
- ``request_opened_at`` = "Request Opened At" or "Legacy Date Submitted" or
  the record's ``createdTime`` — the spec's "effective open date".
- Households whose phone normalizes to a number an earlier record already
  claimed (the spec's shared-phone edge case) are imported without a phone
  (raw value kept in notes) and reported.
- Form submissions with a household link are marked processed (their
  requests already exist in the base); unlinked ones stay unprocessed so
  ``bam process-intake`` can pick them up.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, Protocol

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
    local_date,
    utcnow,
)
from bam.request_types import BY_KEY, default_expiry_days, expiry_days_for, normalize_type
from bam.schemas import ImportReport, TableImportCounts
from bam.validation import hash_phone, validate_phone


class RecordSource(Protocol):
    """What the importer needs: the real AirtableClient or a snapshot/fake."""

    def schema(self) -> list[dict]: ...
    def records(self, table: str) -> Iterable[dict]: ...


#: role -> accepted table names (lowercased) in the base.
TABLE_ALIASES: dict[str, tuple[str, ...]] = {
    "households": ("households",),
    "requests": ("requests",),
    "social_service_requests": ("social service requests", "social services"),
    "distros": ("distros", "distributions"),
    "fulfilled_counts": ("fulfilled request count", "fulfilled request counts"),
    "form_submissions": (
        "assistance request form submissions",
        "form submissions",
    ),
}

_STATUS_BY_VALUE = {status.value.lower(): status for status in RequestStatus}
_APPOINTMENT_BY_VALUE = {status.value.lower(): status for status in AppointmentStatus}


def _first(fields: dict, *names: str):
    """First present field among naming variants."""
    for name in names:
        if name in fields:
            return fields[name]
    return None


def _scalar(value):
    """Airtable lookups arrive as lists; unwrap single-valued ones."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _parse_datetime(value) -> dt.datetime | None:
    value = _scalar(value)
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _parse_date(value) -> dt.date | None:
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _append_note(existing: str | None, note: str) -> str:
    return f"{existing}\n{note}" if existing else note


def find_tables(source: RecordSource) -> dict[str, str]:
    """Map importer roles to actual table names present in the base."""
    found: dict[str, str] = {}
    for table in source.schema():
        name = table.get("name", "")
        lowered = name.lower().strip()
        for role, aliases in TABLE_ALIASES.items():
            if lowered in aliases and role not in found:
                found[role] = name
    return found


def import_base(
    session: Session,
    source: RecordSource,
    now: dt.datetime | None = None,
) -> ImportReport:
    """Import every recognized table from the base. Safe to re-run."""
    now = now or utcnow()
    tables = find_tables(source)
    report = ImportReport(tables_found=tables)

    if "households" in tables:
        household_map = _import_households(session, source, tables["households"], report, now)
    else:
        household_map = _existing_household_map(session)

    if "requests" in tables:
        _import_requests(
            session, source, tables["requests"], Request, report.requests,
            household_map, report, now,
        )
    if "social_service_requests" in tables:
        _import_requests(
            session, source, tables["social_service_requests"], SocialServiceRequest,
            report.social_service_requests, household_map, report, now,
        )
    if "distros" in tables:
        _import_distros(session, source, tables["distros"], report, now)
    if "fulfilled_counts" in tables:
        _import_fulfilled_counts(session, source, tables["fulfilled_counts"], report)
    if "form_submissions" in tables:
        _import_form_submissions(
            session, source, tables["form_submissions"], household_map, report, now
        )

    session.commit()
    return report


def _existing_household_map(session: Session) -> dict[str, int]:
    rows = session.exec(
        select(Household.airtable_id, Household.id).where(
            Household.airtable_id.is_not(None)  # type: ignore[union-attr]
        )
    ).all()
    return {airtable_id: local_id for airtable_id, local_id in rows}


def _import_households(
    session: Session,
    source: RecordSource,
    table: str,
    report: ImportReport,
    now: dt.datetime,
) -> dict[str, int]:
    counts = report.households
    claimed_phones: dict[str, str] = {}  # normalized phone -> airtable id
    for row in session.exec(
        select(Household).where(Household.phone_number.is_not(None))  # type: ignore[union-attr]
    ).all():
        claimed_phones[row.phone_number] = row.airtable_id or f"local-{row.id}"

    household_map: dict[str, int] = {}
    for record in source.records(table):
        fields = record.get("fields", {})
        created_time = _parse_datetime(record.get("createdTime")) or now

        raw_phone = _scalar(_first(fields, "Phone Number"))
        raw_phone = str(raw_phone).strip() if raw_phone else None
        validation = validate_phone(raw_phone)

        phone_number = validation.normalized if validation.valid else None
        duplicate_of = claimed_phones.get(phone_number) if phone_number else None
        if duplicate_of is not None and duplicate_of != record["id"]:
            report.duplicate_phone_airtable_ids.append(record["id"])
            phone_number = None

        household = session.exec(
            select(Household).where(Household.airtable_id == record["id"])
        ).first()
        created = household is None
        if household is None:
            household = Household(airtable_id=record["id"], created_at=created_time)

        household.name = _scalar(_first(fields, "Name", "First Name"))
        household.phone_number = phone_number
        hash_source = validation.normalized if validation.valid else raw_phone
        household.phone_hash = hash_phone(hash_source) if hash_source else None
        household.invalid_phone_number = bool(
            _first(fields, "Invalid Phone Number?") or not validation.valid
        )
        household.intl_phone_number = bool(
            _first(fields, "Int'l Phone Number?", "Intl Phone Number?")
            or validation.international
        )
        household.email = _scalar(_first(fields, "Email"))
        household.email_error = _scalar(_first(fields, "Email Error"))
        household.languages = _as_list(_first(fields, "Languages", "Language"))
        notes = _scalar(_first(fields, "Notes", "Case Notes"))
        household.notes = str(notes) if notes else None
        if duplicate_of is not None and duplicate_of != record["id"] and raw_phone:
            household.notes = _append_note(
                household.notes,
                f"[migration] phone {raw_phone} already claimed by another household",
            )

        household.appointment_date = _parse_date(_first(fields, "Appointment Date"))
        household.appointment_time = _scalar(_first(fields, "Appointment Time"))
        status_raw = _scalar(_first(fields, "Appointment Status"))
        household.appointment_status = (
            _APPOINTMENT_BY_VALUE.get(str(status_raw).lower()) if status_raw else None
        )
        if status_raw and household.appointment_status is None:
            _append_unique(report.unknown_statuses, f"appointment: {status_raw}")
        household.last_texted = _parse_date(_first(fields, "Last Texted"))

        legacy_first = _parse_datetime(_first(fields, "Legacy First Date Submitted"))
        if legacy_first and legacy_first < household.created_at.replace(
            tzinfo=household.created_at.tzinfo or dt.timezone.utc
        ):
            household.created_at = legacy_first
        household.updated_at = now

        session.add(household)
        session.flush()
        household_map[record["id"]] = household.id
        if phone_number:
            claimed_phones[phone_number] = record["id"]
        counts.created += created
        counts.updated += not created
    session.commit()
    return household_map


def _import_requests(
    session: Session,
    source: RecordSource,
    table: str,
    model: type[Request] | type[SocialServiceRequest],
    counts: TableImportCounts,
    household_map: dict[str, int],
    report: ImportReport,
    now: dt.datetime,
) -> None:
    for record in source.records(table):
        fields = record.get("fields", {})
        created_time = _parse_datetime(record.get("createdTime")) or now

        links = _first(fields, "Household", "Households") or []
        link_id = links[0] if isinstance(links, list) and links else None
        household_id = household_map.get(link_id) if link_id else None
        if household_id is None:
            report.orphaned_airtable_ids.append(record["id"])
            counts.skipped += 1
            continue

        type_raw = str(_scalar(_first(fields, "Type")) or "").strip()
        type_key = normalize_type(type_raw)
        if type_key is None:
            type_key = type_raw  # preserve the raw label rather than drop data
            if type_raw:
                _append_unique(report.unmatched_request_types, type_raw)

        status_raw = _scalar(_first(fields, "Status"))
        status = _STATUS_BY_VALUE.get(str(status_raw).lower()) if status_raw else None
        if status is None:
            if status_raw:
                _append_unique(report.unknown_statuses, f"request: {status_raw}")
            status = RequestStatus.OPEN

        opened_at = (
            _parse_datetime(_first(fields, "Request Opened At"))
            or _parse_datetime(_first(fields, "Legacy Date Submitted"))
            or created_time
        )
        status_updated_at = (
            _parse_datetime(_first(fields, "Status Last Updated At")) or created_time
        )
        processing_date = _parse_date(_first(fields, "Processing Date"))
        if processing_date is None and status != RequestStatus.OPEN:
            days = (
                expiry_days_for(type_key)
                if status == RequestStatus.DELIVERED
                and model is Request
                and type_key in BY_KEY
                else default_expiry_days()
            )
            processing_date = local_date(status_updated_at) + dt.timedelta(days=days)

        obj = session.exec(
            select(model).where(model.airtable_id == record["id"])
        ).first()
        created = obj is None
        if obj is None:
            obj = model(
                airtable_id=record["id"],
                household_id=household_id,
                type=type_key,
                created_at=created_time,
            )

        obj.household_id = household_id
        obj.type = type_key
        obj.status = status
        obj.request_opened_at = opened_at
        obj.status_last_updated_at = status_updated_at
        obj.processing_date = processing_date
        notes = _scalar(_first(fields, "Notes"))
        obj.notes = str(notes) if notes else None
        obj.street_address = _scalar(_first(fields, "Street Address"))
        obj.city_state = _scalar(_first(fields, "City, State", "City State", "City"))
        zip_code = _scalar(_first(fields, "Zip Code"))
        obj.zip_code = str(zip_code) if zip_code is not None else None
        obj.address = _scalar(_first(fields, "Address", "Current Address"))
        if model is Request:
            obj.geocode = _scalar(_first(fields, "Geocode"))
        else:
            obj.internet_access = _as_list(_first(fields, "Internet Access"))
            obj.roof_accessible = bool(_first(fields, "Roof Accessible?"))
        obj.updated_at = now

        session.add(obj)
        counts.created += created
        counts.updated += not created
    session.commit()


def _import_distros(
    session: Session,
    source: RecordSource,
    table: str,
    report: ImportReport,
    now: dt.datetime,
) -> None:
    counts = report.distros
    for record in source.records(table):
        fields = record.get("fields", {})
        date_time = _parse_datetime(_first(fields, "Date & Time", "Date and Time", "Date"))
        if date_time is None:
            counts.skipped += 1
            continue
        distro = session.exec(
            select(Distro).where(Distro.airtable_id == record["id"])
        ).first()
        created = distro is None
        if distro is None:
            distro = Distro(airtable_id=record["id"], date_time=date_time)
        distro.date_time = date_time
        distro.location = _scalar(_first(fields, "Location"))
        duration = _scalar(_first(fields, "Duration"))
        # Airtable duration fields are seconds.
        distro.duration_minutes = int(duration) // 60 if duration else None
        appointments = _scalar(_first(fields, "Appointments"))
        distro.appointments = str(appointments) if appointments is not None else None
        notes = _scalar(_first(fields, "Notes"))
        distro.notes = str(notes) if notes else None
        session.add(distro)
        counts.created += created
        counts.updated += not created
    session.commit()


def _import_fulfilled_counts(
    session: Session,
    source: RecordSource,
    table: str,
    report: ImportReport,
) -> None:
    """The wide Airtable table (Date + one column per type) → one row per
    (date, type). The Airtable value wins on re-run."""
    counts = report.fulfilled_counts
    for record in source.records(table):
        fields = record.get("fields", {})
        on_date = _parse_date(_first(fields, "Date"))
        if on_date is None:
            counts.skipped += 1
            continue
        for column, value in fields.items():
            if column == "Date" or not isinstance(value, (int, float)):
                continue
            type_key = normalize_type(column) or column
            if normalize_type(column) is None:
                _append_unique(report.unmatched_request_types, column)
            row = session.exec(
                select(FulfilledRequestCount).where(
                    FulfilledRequestCount.date == on_date,
                    FulfilledRequestCount.request_type == type_key,
                )
            ).first()
            created = row is None
            if row is None:
                row = FulfilledRequestCount(date=on_date, request_type=type_key)
            row.count = int(value)
            session.add(row)
            counts.created += created
            counts.updated += not created
    session.commit()


def _import_form_submissions(
    session: Session,
    source: RecordSource,
    table: str,
    household_map: dict[str, int],
    report: ImportReport,
    now: dt.datetime,
) -> None:
    counts = report.form_submissions
    for record in source.records(table):
        fields = record.get("fields", {})
        created_time = _parse_datetime(record.get("createdTime")) or now

        submission = session.exec(
            select(FormSubmission).where(FormSubmission.airtable_id == record["id"])
        ).first()
        created = submission is None
        if submission is None:
            submission = FormSubmission(airtable_id=record["id"], created_at=created_time)

        phone = _scalar(_first(fields, "Phone Number"))
        submission.name = _scalar(_first(fields, "Name", "First Name"))
        submission.phone_number = str(phone).strip() if phone else None
        submission.email = _scalar(_first(fields, "Email"))
        submission.languages = _as_list(_first(fields, "Languages", "Language"))
        submission.request_types = _as_list(_first(fields, "Request Types"))
        submission.furniture_items = _as_list(_first(fields, "Furniture Items"))
        submission.bed_details = _as_list(_first(fields, "Bed Details"))
        submission.furniture_acknowledgement = bool(
            _first(fields, "Furniture Acknowledgement")
        )
        submission.kitchen_items = _as_list(_first(fields, "Kitchen Items"))
        submission.social_service_requests = _as_list(
            _first(fields, "Social Service Requests")
        )
        submission.internet_access = _as_list(_first(fields, "Internet Access"))
        submission.roof_accessible = bool(_first(fields, "Roof Accessible?"))
        notes = _scalar(_first(fields, "Notes"))
        submission.notes = str(notes) if notes else None
        submission.street_address = _scalar(_first(fields, "Street Address"))
        submission.city_state = _scalar(_first(fields, "City, State", "City State"))
        zip_code = _scalar(_first(fields, "Zip Code"))
        submission.zip_code = str(zip_code) if zip_code is not None else None

        links = _first(fields, "Households", "Household") or []
        link_id = links[0] if isinstance(links, list) and links else None
        household_id = household_map.get(link_id) if link_id else None
        submission.household_id = household_id
        # Linked submissions were already turned into requests in the base;
        # unlinked ones stay unprocessed for `bam process-intake`.
        if household_id is not None and submission.processed_at is None:
            submission.processed_at = created_time

        session.add(submission)
        counts.created += created
        counts.updated += not created
    session.commit()


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
