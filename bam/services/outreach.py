"""Distribution outreach services (spec 6.2, outreach flowchart, error table 6.4).

Implements the outreach cycle: build the filtered outreach list the admin
reviews (spec 6.2 step 1), send the templated text blast (spec 6.2 step 2 and
sequence diagram), confirm appointments (spec 6.2 steps 3-4), and record
phone-outreach outcomes A4-A6 (spec 6.4 and the flowchart's Timeout terminal).
"""

from __future__ import annotations

import secrets
import time
from datetime import date, datetime, timedelta, timezone
from collections.abc import Mapping
from typing import Callable, Sequence

from sqlmodel import Session, col, or_, select

from bam.config import settings
from bam.errors import NotFoundError
from bam.models import (
    AppointmentStatus,
    Household,
    Request,
    RequestStatus,
    apply_status_change,
    local_date,
    utcnow,
)
from bam.request_types import normalize_type
from bam.schemas import BlastMessage, BlastReport, OutreachCandidate
from bam.sms.base import (
    SMSProvider,
    render_template,
    resolve_send_language,
    select_template,
)

#: Outcome key -> short tag appended to household notes (spec 6.4 rows A4-A6).
OUTCOME_TAGS: dict[str, str] = {
    "no_response_timeout": "[no response]",  # A4
    "wrong_number": "[wrong number]",  # A5
    "no_longer_needed": "[no longer needed]",  # A6
}


def _as_utc(value: datetime) -> datetime:
    """Treat naive datetimes loaded from SQLite as UTC (see contract ground rules)."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def build_outreach_list(
    session: Session,
    request_types: list[str] | None = None,
    languages: list[str] | None = None,
    exclude_texted_within_days: int = 0,
    exclude_attended_within_days: int = 0,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[OutreachCandidate]:
    """Build the outreach list for a distribution (spec 6.2 step 1).

    Candidates are households with at least one Open goods request (restricted
    to ``request_types`` when given — the "available supplies match" filter),
    a usable phone number, a language overlap when ``languages`` is given, no
    currently-Booked appointment, and ``last_texted`` / ``last_attended`` at
    least the exclusion windows old (0 disables a window). Ordered by the
    Date of Oldest Fulfillable Request ascending, then truncated to ``limit``.
    """
    now = now or utcnow()
    today = local_date(now)

    type_filter: list[str] | None = None
    if request_types:
        type_filter = [normalize_type(t) or t for t in request_types]

    statement = (
        select(Household)
        .join(Request)
        .where(
            Request.status == RequestStatus.OPEN,
            col(Household.phone_number).is_not(None),
            col(Household.invalid_phone_number).is_(False),
            or_(
                col(Household.appointment_status).is_(None),
                Household.appointment_status != AppointmentStatus.BOOKED,
            ),
        )
        .distinct()
    )
    if type_filter is not None:
        statement = statement.where(col(Request.type).in_(type_filter))
    if exclude_texted_within_days > 0:
        texted_cutoff = today - timedelta(days=exclude_texted_within_days)
        statement = statement.where(
            or_(col(Household.last_texted).is_(None), Household.last_texted <= texted_cutoff)
        )
    if exclude_attended_within_days > 0:
        attended_cutoff = today - timedelta(days=exclude_attended_within_days)
        statement = statement.where(
            or_(col(Household.last_attended).is_(None), Household.last_attended <= attended_cutoff)
        )

    candidates: list[OutreachCandidate] = []
    for household in session.exec(statement).all():
        if languages and not set(languages) & set(household.languages or []):
            continue
        open_requests = [r for r in household.requests if r.status == RequestStatus.OPEN]
        fulfillable = [
            r for r in open_requests if type_filter is None or r.type in type_filter
        ]
        if not fulfillable:
            continue
        candidates.append(
            OutreachCandidate(
                household_id=household.id,
                name=household.name,
                phone_number=household.phone_number,
                languages=list(household.languages or []),
                open_request_types=sorted({r.type for r in open_requests}),
                oldest_open_request_at=min(
                    _as_utc(r.request_opened_at) for r in fulfillable
                ),
                last_texted=household.last_texted,
            )
        )

    candidates.sort(key=lambda c: (c.oldest_open_request_at, c.household_id))
    if limit is not None:
        candidates = candidates[:limit]
    return candidates


def send_text_blast(
    session: Session,
    household_ids: Sequence[int],
    template: str,
    provider: SMSProvider,
    max_messages: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
    dry_run: bool = False,
    token_factory: Callable[[], str] | None = None,
    templates: Mapping[str, str] | None = None,
) -> BlastReport:
    """Send a templated text blast to households (spec 6.2 sequence diagram).

    Renders ``[FIRST_NAME]`` (first token of the household name) and
    ``[REQUEST_URL]`` per household. Households with a missing or invalid
    phone are skipped without consuming the message cap; provider send
    attempts count toward ``max_messages`` (default
    ``settings.sms_max_messages``, spec: max 240), and further eligible
    households are reported as ``not_sent_over_limit``. Successful sends set
    ``last_texted`` to today. Rate limit: after every
    ``settings.sms_batch_size`` attempted sends the ``sleeper`` pauses for
    ``settings.sms_batch_pause_seconds`` (spec: 30 msgs then 30s), never
    after the final attempt. Unknown household ids are reported in
    ``unknown_household_ids`` (the blast proceeds — one stale id must not
    abort outreach to 239 other households).

    ``dry_run=True`` previews the blast: the report is built as usual but
    nothing is persisted — the spec ties "Last Texted = TODAY()" to an SMS
    actually delivered, so a preview must not poison the recency filters of
    the real send.

    Spec 6.2's "[REQUEST_URL] (randomized)": each message gets a unique
    ``?r=<token>`` variant of the form URL so providers don't flag hundreds
    of identical bodies as spam (``BAM_RANDOMIZE_REQUEST_URL`` disables;
    ``token_factory`` is injectable for deterministic tests).
    """
    now = now or utcnow()
    cap = settings.sms_max_messages if max_messages is None else max_messages
    batch_size = settings.sms_batch_size
    make_token = token_factory or (lambda: secrets.token_urlsafe(4))
    report = BlastReport()
    attempted = 0
    pause_pending = False

    for household_id in household_ids:
        household = session.get(Household, household_id)
        if household is None:
            report.unknown_household_ids.append(household_id)
            continue
        if not household.phone_number:
            report.skipped_no_phone += 1
            continue
        if household.invalid_phone_number:
            report.skipped_invalid += 1
            continue
        if attempted >= cap:
            report.not_sent_over_limit += 1
            continue
        if pause_pending:
            sleeper(settings.sms_batch_pause_seconds)
            pause_pending = False

        name_tokens = (household.name or "").split()
        request_url = settings.request_form_url
        if settings.randomize_request_url:
            joiner = "&" if "?" in request_url else "?"
            request_url = f"{request_url}{joiner}r={make_token()}"
        # Per-language routing when a template map is supplied; otherwise the
        # single scalar template goes to everyone (spec 6.2 back-compat).
        send_language: str | None = None
        if templates:
            send_language = resolve_send_language(household.languages or [])
            raw_body = select_template(templates, household.languages or [])
        else:
            raw_body = template
        body = render_template(
            raw_body,
            first_name=name_tokens[0] if name_tokens else "",
            request_url=request_url,
        )
        result = provider.send(household.phone_number, body)
        attempted += 1
        report.messages.append(
            BlastMessage(
                household_id=household.id,
                to=household.phone_number,
                body=body,
                ok=result.ok,
                error=result.error,
                send_language=send_language,
            )
        )
        if result.ok:
            report.sent += 1
            if not dry_run:
                household.last_texted = local_date(now)
                household.updated_at = now
                session.add(household)
        else:
            report.failed += 1
        if batch_size > 0 and attempted % batch_size == 0:
            pause_pending = True

    if dry_run:
        session.rollback()
    else:
        session.commit()
    return report


def confirm_appointment(
    session: Session,
    household_id: int,
    appointment_date: date,
    appointment_time: str,
    now: datetime | None = None,
) -> Household:
    """Book a confirmed recipient into a distribution slot (spec 6.2 steps 3-4)."""
    now = now or utcnow()
    household = session.get(Household, household_id)
    if household is None:
        raise NotFoundError(f"Unknown household id: {household_id}")
    household.appointment_date = appointment_date
    household.appointment_time = appointment_time
    household.appointment_status = AppointmentStatus.BOOKED
    household.updated_at = now
    session.add(household)
    session.commit()
    session.refresh(household)
    return household


def record_outreach_outcome(
    session: Session,
    household_id: int,
    outcome: str,
    note: str | None = None,
    now: datetime | None = None,
) -> Household:
    """Close out a household after phone outreach (spec 6.4 rows A4-A6).

    All the household's Open goods and social-service requests are timed out
    via ``apply_status_change``; ``wrong_number`` (A5) additionally flags the
    phone invalid (interpretation rule 7). Any Booked appointment is cleared,
    and the outcome tag plus optional note is appended to the household notes.
    """
    now = now or utcnow()
    if outcome not in OUTCOME_TAGS:
        raise ValueError(f"Unknown outreach outcome: {outcome!r}")
    household = session.get(Household, household_id)
    if household is None:
        raise NotFoundError(f"Unknown household id: {household_id}")

    for request in household.requests:
        if request.status == RequestStatus.OPEN:
            apply_status_change(request, RequestStatus.TIMEOUT, now=now)
            session.add(request)
    for service_request in household.social_service_requests:
        if service_request.status == RequestStatus.OPEN:
            apply_status_change(service_request, RequestStatus.TIMEOUT, now=now)
            session.add(service_request)

    if outcome == "wrong_number":
        household.invalid_phone_number = True
    if household.appointment_status == AppointmentStatus.BOOKED:
        household.appointment_status = None
        household.appointment_date = None
        household.appointment_time = None

    tag = OUTCOME_TAGS[outcome]
    entry = f"{tag} {note}" if note else tag
    household.notes = f"{household.notes}\n{entry}" if household.notes else entry
    household.updated_at = now
    session.add(household)
    session.commit()
    session.refresh(household)
    return household
