"""Database models for the BAM Mutual Aid System V2 (spec section 4).

Notes on deviations from the Airtable schema, chosen for a relational store:

- Airtable lookup/rollup/formula fields (``Open Request Types``, ``Processing
  Date`` etc.) are computed in code — see ``apply_status_change`` and the
  service layer — instead of being stored columns.
- ``Fulfilled Request Count`` is one row per (date, request type) rather than
  a 50-column wide table.
- ``Household.phone_hash`` and ``anonymized_at`` support the privacy goal of
  hashing sensitive data after fulfillment: a scrubbed household keeps only
  the hash so a re-request from the same phone reconnects to its history.
- ``Household.missed_appointment_count`` makes the "timeout after 2nd missed
  appointment" rule explicit; it resets when the household attends.
- Zip codes are stored as strings (Airtable declares "number") so
  leading-zero zips survive; the intake schema coerces numeric input.
- The Airtable legacy migration fields (``Legacy First/Last Date Submitted``,
  ``Legacy Date Submitted``) are omitted: this is a fresh implementation, and
  a V1 import should map the legacy date onto ``request_opened_at`` — the
  spec's "Request Opened At = effective open date" formula — so expiration
  and outreach ordering stay correct for migrated rows.
- Timestamps are stored UTC; *business dates* (processing dates, last texted,
  fulfilled counts) are derived via ``local_date`` in the configured local
  timezone so an evening distro is not recorded under the next UTC day.
"""

import datetime as dt
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from bam.config import settings
from bam.request_types import default_expiry_days, expiry_days_for, label_for


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_date(now: datetime | None = None) -> date:
    """The business date for ``now`` in ``settings.local_timezone``.

    Naive datetimes (e.g. loaded back from SQLite) are treated as UTC.
    """
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(settings.local_timezone)).date()


class RequestStatus(str, Enum):
    OPEN = "Open"
    TIMEOUT = "Timeout"
    DELIVERED = "Delivered"


class AppointmentStatus(str, Enum):
    BOOKED = "Booked"
    CHECKED_IN = "Checked-in"
    MISSED = "Missed"


class Household(SQLModel, table=True):
    __tablename__ = "households"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: Optional[str] = None
    phone_number: Optional[str] = Field(default=None, index=True, unique=True)  # E.164
    phone_hash: Optional[str] = Field(default=None, index=True)  # sha256(E.164)
    invalid_phone_number: bool = False
    intl_phone_number: bool = False
    email: Optional[str] = None
    email_error: Optional[str] = None
    languages: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    notes: Optional[str] = None

    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None  # e.g. "11:00 AM"
    appointment_status: Optional[AppointmentStatus] = None
    missed_appointment_count: int = 0

    last_texted: Optional[date] = None
    last_called: Optional[date] = None
    last_attended: Optional[date] = None
    needs_delivery: bool = False
    needs_email_outreach: bool = False

    anonymized_at: Optional[datetime] = None
    airtable_id: Optional[str] = Field(default=None, index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    requests: list["Request"] = Relationship(back_populates="household")
    social_service_requests: list["SocialServiceRequest"] = Relationship(
        back_populates="household"
    )
    form_submissions: list["FormSubmission"] = Relationship(back_populates="household")


class Request(SQLModel, table=True):
    __tablename__ = "requests"

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)  # canonical key from bam.request_types
    household_id: int = Field(foreign_key="households.id", index=True)
    status: RequestStatus = Field(default=RequestStatus.OPEN, index=True)
    notes: Optional[str] = None

    request_opened_at: datetime = Field(default_factory=utcnow)
    status_last_updated_at: datetime = Field(default_factory=utcnow)
    processing_date: Optional[date] = None  # set on Delivered/Timeout, see below

    # Delivery address (furniture and large items only)
    street_address: Optional[str] = None
    city_state: Optional[str] = None
    zip_code: Optional[str] = None
    geocode: Optional[str] = None  # "plus_code" / Open Location Code
    address: Optional[str] = None
    bin: Optional[str] = None  # NYC Building Identification Number
    address_accuracy: Optional[str] = None  # Apartment/Building/No result/...

    airtable_id: Optional[str] = Field(default=None, index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    household: Household = Relationship(back_populates="requests")

    @property
    def label(self) -> str:
        return label_for(self.type)


class SocialServiceRequest(SQLModel, table=True):
    __tablename__ = "social_service_requests"

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    status: RequestStatus = Field(default=RequestStatus.OPEN, index=True)
    notes: Optional[str] = None

    internet_access: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    roof_accessible: bool = False

    street_address: Optional[str] = None
    city_state: Optional[str] = None
    zip_code: Optional[str] = None
    address: Optional[str] = None
    geocode: Optional[str] = None
    bin: Optional[str] = None  # NYC Building Identification Number (Mesh)
    address_accuracy: Optional[str] = None
    # Raw NYC-Mesh pipeline status (17 stages) preserved for consolidation
    # ranking; the coarse RequestStatus above is derived from it.
    mesh_status: Optional[str] = None

    request_opened_at: datetime = Field(default_factory=utcnow)
    status_last_updated_at: datetime = Field(default_factory=utcnow)
    processing_date: Optional[date] = None

    airtable_id: Optional[str] = Field(default=None, index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    household: Household = Relationship(back_populates="social_service_requests")

    @property
    def label(self) -> str:
        return label_for(self.type)


class Distro(SQLModel, table=True):
    __tablename__ = "distros"

    id: Optional[int] = Field(default=None, primary_key=True)
    date_time: datetime
    location: Optional[str] = None
    duration_minutes: Optional[int] = None
    appointments: Optional[str] = None
    notes: Optional[str] = None
    airtable_id: Optional[str] = Field(default=None, index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)


class FulfilledRequestCount(SQLModel, table=True):
    """One row per (date, request type) — Airtable's wide table, normalized."""

    __tablename__ = "fulfilled_request_counts"
    __table_args__ = (UniqueConstraint("date", "request_type"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True)
    request_type: str = Field(index=True)
    count: int = 0


class FormSubmission(SQLModel, table=True):
    """Raw intake data (Assistance Request Form Submissions)."""

    __tablename__ = "form_submissions"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: Optional[str] = None
    phone_number: Optional[str] = None  # as submitted, pre-normalization
    email: Optional[str] = None
    languages: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    request_types: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    furniture_items: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    bed_details: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    furniture_acknowledgement: bool = False
    kitchen_items: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    social_service_requests: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    internet_access: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    roof_accessible: bool = False
    notes: Optional[str] = None

    street_address: Optional[str] = None
    city_state: Optional[str] = None
    zip_code: Optional[str] = None

    airtable_id: Optional[str] = Field(default=None, index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)
    processed_at: Optional[datetime] = None
    scrubbed_at: Optional[datetime] = None
    household_id: Optional[int] = Field(default=None, foreign_key="households.id", index=True)

    household: Optional[Household] = Relationship(back_populates="form_submissions")


def apply_status_change(
    request: Request | SocialServiceRequest,
    status: RequestStatus,
    now: datetime | None = None,
) -> None:
    """Set a request's status plus the fields Airtable derives via formulas.

    Processing Date formula (spec section 4):
    - Delivered: +14 days after the status change (+30 for Pots & Pans)
    - Timeout: +14 days
    Social service requests always use the 14-day window. Windows come from
    the settings-aware helpers so env overrides apply; the base date is the
    local business date.
    """
    now = now or utcnow()
    request.status = status
    request.status_last_updated_at = now
    request.updated_at = now
    if status == RequestStatus.DELIVERED:
        days = (
            expiry_days_for(request.type)
            if isinstance(request, Request)
            else default_expiry_days()
        )
        request.processing_date = local_date(now) + timedelta(days=days)
    elif status == RequestStatus.TIMEOUT:
        request.processing_date = local_date(now) + timedelta(days=default_expiry_days())
    else:
        request.processing_date = None
