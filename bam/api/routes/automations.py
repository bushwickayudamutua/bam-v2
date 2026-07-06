"""Routes for the parity automations (merge, consolidate, dedupe, count,
mailjet, snapshot, analytics, hard-delete).

These mirror the production V2 Airtable automations and maintenance crons.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from bam.api.routes import value_error_to_http
from bam.db import get_session
from bam.schemas import (
    AnalyticsReport,
    ConsolidateReport,
    CountClosedReport,
    DedupeReport,
    MailjetReport,
    MergeReport,
    SnapshotReport,
)
from bam.services import (
    admin,
    analytics,
    consolidate as consolidate_svc,
    count_closed as count_closed_svc,
    dedupe as dedupe_svc,
    mailjet as mailjet_svc,
    merge as merge_svc,
    snapshot as snapshot_svc,
)

router = APIRouter()


class MergeIn(BaseModel):
    survivor_id: int
    other_ids: list[int]


class ConsolidateIn(BaseModel):
    household_id: int | None = None


class CountClosedIn(BaseModel):
    delete: bool | None = None


@router.post("/households/merge", response_model=MergeReport)
def merge_households(payload: MergeIn, session: Session = Depends(get_session)) -> MergeReport:
    """Merge households (merge-households.js)."""
    try:
        return merge_svc.merge_households(session, payload.survivor_id, payload.other_ids)
    except ValueError as exc:
        raise value_error_to_http(exc) from exc


@router.post("/requests/consolidate", response_model=ConsolidateReport)
def consolidate_requests(
    payload: ConsolidateIn, session: Session = Depends(get_session)
) -> ConsolidateReport:
    """Consolidate duplicate requests (consolidate-requests.js)."""
    return consolidate_svc.consolidate_requests(session, household_id=payload.household_id)


@router.post("/jobs/dedupe-households", response_model=DedupeReport)
def dedupe_households(session: Session = Depends(get_session)) -> DedupeReport:
    """Merge same-phone duplicate households (DedupeAirtableViews)."""
    return dedupe_svc.dedupe_households(session)


@router.post("/jobs/count-closed", response_model=CountClosedReport)
def count_closed(
    payload: CountClosedIn, session: Session = Depends(get_session)
) -> CountClosedReport:
    """Tally (and optionally delete) closed requests (count-closed-requests.js)."""
    return count_closed_svc.count_closed_requests(session, delete=payload.delete)


@router.post("/jobs/mailjet-sync", response_model=MailjetReport)
def mailjet_sync(session: Session = Depends(get_session)) -> MailjetReport:
    """Sync email contacts to Mailjet (UpdateMailjetLists)."""
    return mailjet_svc.sync_mailjet_lists(session)


@router.post("/jobs/snapshot", response_model=SnapshotReport)
def snapshot(session: Session = Depends(get_session)) -> SnapshotReport:
    """Write a full data snapshot (SnapshotAirtableViews)."""
    return snapshot_svc.snapshot_data(session)


@router.get("/metrics/analytics", response_model=AnalyticsReport)
def get_analytics(
    start: dt.date | None = None,
    end: dt.date | None = None,
    session: Session = Depends(get_session),
) -> AnalyticsReport:
    """Fulfilled-vs-open analytics (analyze_fulfilled_requests)."""
    return analytics.analyze_fulfilled_requests(session, start=start, end=end)


@router.delete("/households/{household_id}")
def delete_household(household_id: int, session: Session = Depends(get_session)) -> dict:
    """Hard-delete a household (delete-household.js)."""
    try:
        return admin.delete_household(session, household_id)
    except ValueError as exc:
        raise value_error_to_http(exc) from exc


@router.delete("/submissions/{submission_id}")
def delete_submission(submission_id: int, session: Session = Depends(get_session)) -> dict:
    """Hard-delete a form submission (delete-submission.js)."""
    try:
        return admin.delete_submission(session, submission_id)
    except ValueError as exc:
        raise value_error_to_http(exc) from exc
