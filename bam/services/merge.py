"""Household merge (parity with automation-scripts/v2/merge-households.js).

Combines several household records into one survivor: relinks all their
requests, unions languages, keeps the newest contact/appointment info and
the widest date ranges, concatenates notes, ORs the boolean flags, then
deletes the merged-away households. Foreign keys do the linking work that
the Airtable script does by rewriting linked-record fields.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlmodel import Session, col, select

from bam.errors import NotFoundError
from bam.models import (
    AppointmentStatus,
    Household,
    Request,
    SocialServiceRequest,
    utcnow,
)
from bam.schemas import MergeReport


def _max_date(values: list) -> object | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _min_date(values: list) -> object | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def merge_households(
    session: Session,
    survivor_id: int,
    other_ids: list[int],
    now: datetime | None = None,
) -> MergeReport:
    """Merge ``other_ids`` into ``survivor_id`` and delete them.

    The survivor keeps its own id (so its existing links and its
    ``airtable_id`` provenance survive). Contact fields, appointment, and
    the date rollups are combined across all involved households.
    """
    now = now or utcnow()
    survivor = session.get(Household, survivor_id)
    if survivor is None:
        raise NotFoundError(f"Unknown household id {survivor_id}")
    others: list[Household] = []
    for oid in other_ids:
        if oid == survivor_id:
            continue
        h = session.get(Household, oid)
        if h is None:
            raise NotFoundError(f"Unknown household id {oid}")
        others.append(h)
    if not others:
        return MergeReport(survivor_id=survivor_id, merged_ids=[], moved_requests=0)

    everyone = [survivor, *others]

    # Relink requests (goods + social/mesh) to the survivor.
    moved = 0
    for model in (Request, SocialServiceRequest):
        rows = session.exec(
            select(model).where(col(model.household_id).in_([h.id for h in others]))
        ).all()
        for row in rows:
            row.household_id = survivor_id
            row.updated_at = now
            session.add(row)
            moved += 1

    # Languages: union, preserve first-seen order.
    merged_langs: list[str] = []
    for h in everyone:
        for lang in h.languages or []:
            if lang not in merged_langs:
                merged_langs.append(lang)
    survivor.languages = merged_langs

    # Notes: concat non-empty, newest-first (like the script's reverse()).
    notes = [h.notes.strip() for h in reversed(everyone) if h.notes and h.notes.strip()]
    survivor.notes = "\n".join(notes) or None

    # Date rollups.
    survivor.last_texted = _max_date([h.last_texted for h in everyone])  # type: ignore[assignment]
    survivor.last_called = _max_date([h.last_called for h in everyone])  # type: ignore[assignment]
    survivor.last_attended = _max_date([h.last_attended for h in everyone])  # type: ignore[assignment]
    survivor.created_at = _min_date([h.created_at for h in everyone]) or survivor.created_at  # type: ignore[assignment]

    # Boolean flags: OR across all.
    survivor.needs_delivery = any(h.needs_delivery for h in everyone)
    survivor.needs_email_outreach = any(h.needs_email_outreach for h in everyone)
    survivor.missed_appointment_count = max(h.missed_appointment_count for h in everyone)

    # Appointment: keep the one with the latest date.
    appt_date = _max_date([h.appointment_date for h in everyone])
    if appt_date is not None:
        source = next(h for h in reversed(everyone) if h.appointment_date == appt_date)
        survivor.appointment_date = source.appointment_date
        survivor.appointment_time = source.appointment_time
        survivor.appointment_status = source.appointment_status

    # Contact/validation fields: prefer a household that has a phone (the
    # survivor's own first, else any other) so we don't lose the number.
    if not survivor.phone_number:
        donor = next((h for h in others if h.phone_number), None)
        if donor is not None:
            survivor.phone_number = donor.phone_number
            survivor.phone_hash = donor.phone_hash
            survivor.invalid_phone_number = donor.invalid_phone_number
            survivor.intl_phone_number = donor.intl_phone_number
    if not survivor.email:
        donor = next((h for h in everyone if h.email), None)
        if donor is not None:
            survivor.email = donor.email
            survivor.email_error = donor.email_error
    if not survivor.name:
        donor = next((h for h in everyone if h.name), None)
        if donor is not None:
            survivor.name = donor.name

    survivor.updated_at = now
    session.add(survivor)
    session.flush()

    merged_ids = [h.id for h in others]
    for h in others:
        session.delete(h)
    session.commit()
    session.refresh(survivor)
    return MergeReport(
        survivor_id=survivor_id, merged_ids=merged_ids, moved_requests=moved
    )
