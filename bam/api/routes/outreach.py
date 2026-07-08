"""Outreach routes (spec 6.2 + 6.4). ``POST /outreach/blast`` is the spec
section 5 ``send_sms`` web-triggered function."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator
from sqlmodel import Session

from bam.api.routes import value_error_to_http
from bam.config import settings
from bam.db import get_session
from bam.models import Household
from bam.schemas import BlastReport, HouseholdOut, OutreachCandidate
from bam.services import outreach
from bam.sms import get_provider

router = APIRouter()


class OutreachListIn(BaseModel):
    request_types: list[str] | None = None
    languages: list[str] | None = None
    exclude_texted_within_days: int = 0
    exclude_attended_within_days: int = 0
    limit: int | None = None


class BlastIn(BaseModel):
    household_ids: list[int]
    # A single template goes to everyone; a per-language map (keys
    # Spanish/Cantonese/English) routes each household to its language, with
    # a Spanish+Cantonese+English "All" fallback. At least one is required.
    template: str | None = None
    templates: dict[str, str] | None = None
    max_messages: int | None = None

    @model_validator(mode="after")
    def _require_a_template(self) -> "BlastIn":
        if not self.template and not self.templates:
            raise ValueError("Provide 'template' or 'templates'.")
        return self


class AppointmentIn(BaseModel):
    appointment_date: dt.date
    appointment_time: str


class OutcomeIn(BaseModel):
    outcome: str
    note: str | None = None


@router.post("/outreach/list", response_model=list[OutreachCandidate])
def outreach_list(
    payload: OutreachListIn, session: Session = Depends(get_session)
) -> list[OutreachCandidate]:
    """Build the filtered outreach list for a distribution (spec 6.2 step 1)."""
    return outreach.build_outreach_list(
        session,
        request_types=payload.request_types,
        languages=payload.languages,
        exclude_texted_within_days=payload.exclude_texted_within_days,
        exclude_attended_within_days=payload.exclude_attended_within_days,
        limit=payload.limit,
    )


@router.post("/outreach/blast", response_model=BlastReport)
def outreach_blast(
    payload: BlastIn, session: Session = Depends(get_session)
) -> BlastReport:
    """Send the templated text blast (spec 5 ``send_sms``, 6.2 step 2)."""
    provider = get_provider(settings)
    return outreach.send_text_blast(
        session,
        payload.household_ids,
        payload.template or "",
        provider,
        max_messages=payload.max_messages,
        templates=payload.templates,
    )


@router.post("/households/{household_id}/appointment", response_model=HouseholdOut)
def book_appointment(
    household_id: int, payload: AppointmentIn, session: Session = Depends(get_session)
) -> Household:
    """Book a confirmed recipient into a slot (spec 6.2 steps 3-4)."""
    try:
        return outreach.confirm_appointment(
            session, household_id, payload.appointment_date, payload.appointment_time
        )
    except ValueError as exc:
        raise value_error_to_http(exc) from exc


@router.post("/households/{household_id}/outreach-outcome", response_model=HouseholdOut)
def record_outcome(
    household_id: int, payload: OutcomeIn, session: Session = Depends(get_session)
) -> Household:
    """Record a phone-outreach outcome A4-A6 (spec 6.4)."""
    try:
        return outreach.record_outreach_outcome(
            session, household_id, payload.outcome, note=payload.note
        )
    except ValueError as exc:
        raise value_error_to_http(exc) from exc
