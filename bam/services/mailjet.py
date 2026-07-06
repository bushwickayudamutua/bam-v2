"""Mailjet email-list sync (parity with UpdateMailjetLists).

Syncs household contacts with a valid email into a Mailjet contact list.
Pluggable like the SMS layer: with credentials it calls Mailjet's REST API;
without them it runs in dry mode and just reports who *would* be synced, so
the daily job is always runnable.
"""

from __future__ import annotations

import base64
import json
import urllib.request

from sqlmodel import Session, col, select

from bam.config import Settings, settings as default_settings
from bam.models import Household
from bam.schemas import MailjetReport


def _valid_email_households(session: Session) -> list[Household]:
    rows = session.exec(
        select(Household).where(
            col(Household.email).is_not(None),
            col(Household.email_error).is_(None),
            col(Household.anonymized_at).is_(None),
        )
    ).all()
    return [h for h in rows if h.email]


def sync_mailjet_lists(
    session: Session,
    settings: Settings | None = None,
    opener=urllib.request.urlopen,
) -> MailjetReport:
    """Push contacts to the configured Mailjet list (dry when unconfigured)."""
    settings = settings or default_settings
    households = _valid_email_households(session)
    contacts = [
        {"Email": h.email, "Name": h.name or ""}
        for h in households
    ]
    configured = bool(
        settings.mailjet_api_key and settings.mailjet_api_secret and settings.mailjet_list_id
    )
    if not configured:
        return MailjetReport(eligible=len(contacts), synced=0, dry_run=True)

    auth = base64.b64encode(
        f"{settings.mailjet_api_key}:{settings.mailjet_api_secret}".encode()
    ).decode()
    body = json.dumps(
        {"Action": "addnoforce", "Contacts": contacts}
    ).encode("utf-8")
    url = (
        f"https://api.mailjet.com/v3/REST/contactslist/"
        f"{settings.mailjet_list_id}/managemanycontacts"
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
    )
    with opener(request, timeout=30) as resp:  # noqa: S310
        ok = 200 <= resp.status < 300
    return MailjetReport(eligible=len(contacts), synced=len(contacts) if ok else 0, dry_run=False)
