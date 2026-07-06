"""Household deduplication (parity with DedupeAirtableViews).

Production dedupes records across ~23 Airtable views by phone number. Our
model keys households on the normalized phone, so exact duplicates are rare,
but merges and imports can still leave two households sharing a phone (or
phone hash). This finds those clusters and merges each via
``merge_households`` (keeping the lowest id as survivor), then consolidates
the survivor's requests.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, col, select

from bam.models import Household, utcnow
from bam.schemas import DedupeReport
from bam.services.consolidate import consolidate_requests
from bam.services.merge import merge_households


def dedupe_households(
    session: Session,
    consolidate: bool = True,
    now: datetime | None = None,
) -> DedupeReport:
    """Merge households that share a phone number or phone hash."""
    now = now or utcnow()
    report = DedupeReport()

    households = session.exec(
        select(Household).where(col(Household.anonymized_at).is_(None))
    ).all()
    # Key by phone_hash (the stable identity derived from the normalized
    # phone) so an anonymized household — which keeps only its hash — dedups
    # against the active household for the same person. Fall back to the raw
    # phone for rows that somehow lack a hash.
    clusters: dict[str, list[int]] = {}
    for h in households:
        key = h.phone_hash or h.phone_number
        if not key:
            continue
        clusters.setdefault(key, []).append(h.id)

    for ids in clusters.values():
        if len(ids) < 2:
            continue
        ids.sort()
        survivor_id, *others = ids
        merge_households(session, survivor_id, others, now=now)
        report.clusters_merged += 1
        report.households_removed += len(others)
        if consolidate:
            consolidate_requests(session, household_id=survivor_id, now=now)

    return report
