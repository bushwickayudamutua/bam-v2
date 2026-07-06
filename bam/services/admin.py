"""Hard-delete operations (parity with delete-household.js /
delete-submission.js).

Our default lifecycle anonymizes rather than deletes (SPEC-MAPPING decision
1). These give the explicit hard-delete actions the production automations
expose, for the cases where a record must actually be removed.
"""

from __future__ import annotations

from sqlmodel import Session, select

from bam.errors import NotFoundError
from bam.models import (
    FormSubmission,
    Household,
    Request,
    SocialServiceRequest,
)


def delete_household(session: Session, household_id: int) -> dict:
    """Hard-delete a household and all of its requests + submissions."""
    household = session.get(Household, household_id)
    if household is None:
        raise NotFoundError(f"Unknown household id {household_id}")

    deleted = {"requests": 0, "social_service_requests": 0, "form_submissions": 0}
    for req in session.exec(
        select(Request).where(Request.household_id == household_id)
    ).all():
        session.delete(req)
        deleted["requests"] += 1
    for sreq in session.exec(
        select(SocialServiceRequest).where(
            SocialServiceRequest.household_id == household_id
        )
    ).all():
        session.delete(sreq)
        deleted["social_service_requests"] += 1
    for sub in session.exec(
        select(FormSubmission).where(FormSubmission.household_id == household_id)
    ).all():
        # Keep the submission row but unlink it (household is going away).
        sub.household_id = None
        session.add(sub)
    session.delete(household)
    session.commit()
    return {"deleted_household": household_id, **deleted}


def delete_submission(session: Session, submission_id: int) -> dict:
    """Hard-delete a form submission (prod deletes after processing)."""
    submission = session.get(FormSubmission, submission_id)
    if submission is None:
        raise NotFoundError(f"Unknown submission id {submission_id}")
    session.delete(submission)
    session.commit()
    return {"deleted_submission": submission_id}
