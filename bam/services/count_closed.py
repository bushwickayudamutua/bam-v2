"""Count-and-close closed requests (parity with count-closed-requests.js).

Production's V2 flow tallies Delivered requests into ``Fulfilled Request
Count`` — bucketed by the day the status last changed — and then *deletes*
the request. Mesh installs are counted once per phone number. This mirrors
that behaviour as an opt-in job (default keeps our count-and-keep model;
enable with ``delete_after_count`` / ``--delete``).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from bam.models import (
    Household,
    Request,
    RequestStatus,
    SocialServiceRequest,
    local_date,
    utcnow,
)
from bam.schemas import CountClosedReport
from bam.services.metrics import increment_fulfilled_count

MESH_INSTALLED_STATUS = "YAY! MESH INSTALLED!"


def count_closed_requests(
    session: Session,
    delete: bool | None = None,
    now: datetime | None = None,
) -> CountClosedReport:
    """Tally Delivered requests into the fulfilled counts (and optionally
    delete them). Buckets by ``status_last_updated_at`` local date."""
    from bam.config import settings

    now = now or utcnow()
    do_delete = settings.delete_after_count if delete is None else delete
    report = CountClosedReport()

    # Goods + non-mesh social: one count per Delivered request.
    for model in (Request, SocialServiceRequest):
        rows = session.exec(
            select(model).where(model.status == RequestStatus.DELIVERED)
        ).all()
        for row in rows:
            if getattr(row, "type", None) == "mesh_internet":
                continue  # handled below with phone dedup
            on_date = local_date(row.status_last_updated_at)
            increment_fulfilled_count(session, on_date, row.type, commit=False)
            report.counted += 1
            if do_delete:
                session.delete(row)
                report.deleted += 1

    # Mesh: count one install per (date, phone number).
    mesh_rows = session.exec(
        select(SocialServiceRequest).where(
            SocialServiceRequest.type == "mesh_internet",
            SocialServiceRequest.status == RequestStatus.DELIVERED,
        )
    ).all()
    seen: set[tuple[str, str]] = set()
    for row in mesh_rows:
        household = session.get(Household, row.household_id)
        phone = (household.phone_number or household.phone_hash or str(row.id)) if household else str(row.id)
        on_date = local_date(row.status_last_updated_at)
        key = (on_date.isoformat(), phone)
        if key not in seen:
            seen.add(key)
            increment_fulfilled_count(session, on_date, "mesh_internet", commit=False)
            report.counted += 1
        if do_delete:
            session.delete(row)
            report.deleted += 1

    session.commit()
    return report
