"""Request consolidation (parity with merge-households/consolidate-requests.js).

Collapses duplicate requests of the same type within a household into a
single survivor (the earliest ``request_opened_at``), merging the useful
fields off the duplicates and deleting the rest. Mesh requests consolidate
by Building Identification Number and keep the furthest-along pipeline
status and the best-accuracy address bundle.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, col, select

from bam.models import Household, Request, SocialServiceRequest, utcnow
from bam.schemas import ConsolidateReport

# NYC-Mesh pipeline status ranking (higher = further along / more terminal),
# from consolidate-requests.js MESH_STATUS_RANK.
MESH_STATUS_RANK: dict[str, int] = {
    "Open": 0,
    "Texted about Mesh": 1,
    "Step 1 - Interested in Mesh": 2,
    "Needs Panorama": 3,
    "Roof Access In Process": 4,
    "Confirming Permission with Landlord": 5,
    "Roof Access Confirmed": 6,
    "Step 2 - LOS Confirmed": 7,
    "Step 3 - Scheduling IN-PROGRESS": 8,
    "Install Scheduled": 9,
    "YAY! MESH INSTALLED!": 10,
    "Not Interested": 11,
    "Cannot Install - Does not have LOS": 11,
    "NYCHA - Currently Does Not Qualify": 11,
    "Cannot Install - No Roof Access": 11,
    "Cannot Install - Other Reason": 11,
    "INSTALL PENDING ELDERT REPAIR": 12,
}

ADDRESS_ACCURACY_RANK: dict[str, int] = {
    "Apartment": 3,
    "Building": 2,
    "Address Outside NY": 1,
    "No result": 0,
    "": 0,
    "Invalid Address Provided": -1,
}


def _best_address_row(group: list) -> object:
    """Pick the row with the most accurate address (accuracy rank, then a
    non-empty address), mirroring pickAddressBundleIndex."""
    best = group[0]
    best_rank = -2
    for row in group:
        rank = ADDRESS_ACCURACY_RANK.get(row.address_accuracy or "", -2)
        if rank >= best_rank:
            best_rank = rank
            best = row
    if not ((best.address or "").strip() or (best.street_address or "").strip()):
        for row in reversed(group):
            if (row.address or "").strip() or (row.street_address or "").strip():
                return row
    return best


def _consolidate_group(session: Session, group: list, now: datetime, is_mesh: bool) -> int:
    """Merge a same-key group into its earliest member; delete the rest."""
    group.sort(key=lambda r: r.request_opened_at)
    survivor, *rest = group
    if not rest:
        return 0

    survivor.request_opened_at = min(r.request_opened_at for r in group)
    survivor.updated_at = now

    if is_mesh:
        # Best pipeline status + best-accuracy address bundle + unioned
        # internet-access.
        best_status = max(
            (r.mesh_status for r in group if r.mesh_status),
            key=lambda s: MESH_STATUS_RANK.get(s, -1),
            default=survivor.mesh_status,
        )
        survivor.mesh_status = best_status
        addr = _best_address_row(group)
        survivor.street_address = addr.street_address
        survivor.city_state = addr.city_state
        survivor.zip_code = addr.zip_code
        survivor.address = addr.address
        survivor.address_accuracy = addr.address_accuracy
        survivor.bin = addr.bin
        merged_ia: list[str] = []
        for r in group:
            for ia in r.internet_access or []:
                if ia not in merged_ia:
                    merged_ia.append(ia)
        survivor.internet_access = merged_ia

    session.add(survivor)
    for r in rest:
        session.delete(r)
    return len(rest)


def consolidate_requests(
    session: Session,
    household_id: int | None = None,
    now: datetime | None = None,
) -> ConsolidateReport:
    """Consolidate duplicate requests, for one household or all of them."""
    now = now or utcnow()
    report = ConsolidateReport()

    household_ids: list[int]
    if household_id is not None:
        household_ids = [household_id]
    else:
        household_ids = list(session.exec(select(Household.id)).all())

    for hid in household_ids:
        # Goods: group by type.
        goods = session.exec(
            select(Request).where(Request.household_id == hid)
        ).all()
        by_type: dict[str, list] = {}
        for r in goods:
            by_type.setdefault(r.type, []).append(r)
        for group in by_type.values():
            report.requests_removed += _consolidate_group(session, group, now, is_mesh=False)

        # Social services: group by type, EXCEPT mesh which groups by BIN.
        social = session.exec(
            select(SocialServiceRequest).where(SocialServiceRequest.household_id == hid)
        ).all()
        mesh = [s for s in social if s.type == "mesh_internet"]
        non_mesh = [s for s in social if s.type != "mesh_internet"]
        by_type_s: dict[str, list] = {}
        for s in non_mesh:
            by_type_s.setdefault(s.type, []).append(s)
        for group in by_type_s.values():
            report.social_removed += _consolidate_group(session, group, now, is_mesh=False)
        by_bin: dict[str, list] = {}
        for s in mesh:
            by_bin.setdefault(s.bin or f"__id{s.id}", []).append(s)
        for group in by_bin.values():
            report.mesh_removed += _consolidate_group(session, group, now, is_mesh=True)

    session.commit()
    return report
