"""Intake routes (spec 6.1): the assistance request form endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from bam.db import get_session
from bam.schemas import FormSubmissionIn, IntakeResult
from bam.services import intake

router = APIRouter()


@router.post("/intake/submissions", response_model=IntakeResult, status_code=201)
def create_submission(
    payload: FormSubmissionIn, session: Session = Depends(get_session)
) -> IntakeResult:
    """Store and immediately process a form submission (spec 6.1 steps 1-7)."""
    return intake.intake_and_process(session, payload)
