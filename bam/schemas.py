"""Pydantic I/O schemas shared by the service layer, API, and CLI."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, field_validator

from bam.models import AppointmentStatus, RequestStatus


class FormSubmissionIn(BaseModel):
    phone_number: str
    name: str | None = None
    email: str | None = None
    languages: list[str] = []
    request_types: list[str] = []
    furniture_items: list[str] = []
    bed_details: list[str] = []
    furniture_acknowledgement: bool = False
    kitchen_items: list[str] = []
    social_service_requests: list[str] = []
    internet_access: list[str] = []
    roof_accessible: bool = False
    notes: str | None = None
    street_address: str | None = None
    city_state: str | None = None
    zip_code: str | None = None

    @field_validator("zip_code", mode="before")
    @classmethod
    def _coerce_zip(cls, value: object) -> object:
        # Spec 4 types Zip Code as a number; we store strings (leading-zero
        # zips) but accept numeric input rather than 422 a valid submission.
        return str(value) if isinstance(value, int) else value


class IntakeResult(BaseModel):
    submission_id: int
    household_id: int
    created_household: bool
    created_request_ids: list[int] = []
    created_social_service_request_ids: list[int] = []
    skipped_duplicate_types: list[str] = []
    unknown_types: list[str] = []
    phone_valid: bool
    already_processed: bool = False


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    label: str
    status: RequestStatus
    request_opened_at: dt.datetime
    processing_date: dt.date | None = None
    notes: str | None = None


class SocialServiceRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    label: str
    status: RequestStatus
    request_opened_at: dt.datetime
    processing_date: dt.date | None = None
    notes: str | None = None


class HouseholdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None = None
    phone_number: str | None = None
    invalid_phone_number: bool
    intl_phone_number: bool
    email: str | None = None
    email_error: str | None = None
    languages: list[str] = []
    appointment_date: dt.date | None = None
    appointment_time: str | None = None
    appointment_status: AppointmentStatus | None = None
    missed_appointment_count: int
    last_texted: dt.date | None = None
    last_attended: dt.date | None = None


class CheckinView(BaseModel):
    household: HouseholdOut
    open_requests: list[RequestOut] = []
    open_social_service_requests: list[SocialServiceRequestOut] = []


class OutreachCandidate(BaseModel):
    household_id: int
    name: str | None = None
    phone_number: str | None = None
    languages: list[str] = []
    open_request_types: list[str] = []
    oldest_open_request_at: dt.datetime | None = None
    last_texted: dt.date | None = None


class BlastMessage(BaseModel):
    household_id: int
    to: str
    body: str
    ok: bool
    error: str | None = None


class BlastReport(BaseModel):
    sent: int = 0
    failed: int = 0
    skipped_invalid: int = 0
    skipped_no_phone: int = 0
    not_sent_over_limit: int = 0
    unknown_household_ids: list[int] = []
    messages: list[BlastMessage] = []


class NoShowReport(BaseModel):
    missed_household_ids: list[int] = []
    timed_out_household_ids: list[int] = []


class ExpirationReport(BaseModel):
    timed_out_request_ids: list[int] = []
    timed_out_social_service_request_ids: list[int] = []


class ScrubReport(BaseModel):
    households_anonymized: int = 0
    requests_scrubbed: int = 0
    social_service_requests_scrubbed: int = 0
    submissions_scrubbed: int = 0


class DistroIn(BaseModel):
    date_time: dt.datetime
    location: str | None = None
    duration_minutes: int | None = None
    appointments: str | None = None
    notes: str | None = None


class DistroOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date_time: dt.datetime
    location: str | None = None
    duration_minutes: int | None = None
    appointments: str | None = None
    notes: str | None = None


class TableImportCounts(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0


class ImportReport(BaseModel):
    tables_found: dict[str, str] = {}
    households: TableImportCounts = TableImportCounts()
    requests: TableImportCounts = TableImportCounts()
    social_service_requests: TableImportCounts = TableImportCounts()
    mesh_requests: TableImportCounts = TableImportCounts()
    distros: TableImportCounts = TableImportCounts()
    fulfilled_counts: TableImportCounts = TableImportCounts()
    form_submissions: TableImportCounts = TableImportCounts()
    unmatched_request_types: list[str] = []
    unknown_statuses: list[str] = []
    duplicate_phone_airtable_ids: list[str] = []
    orphaned_airtable_ids: list[str] = []
