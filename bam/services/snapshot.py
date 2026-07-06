"""Data snapshots (parity with SnapshotAirtableViews).

Production backs up modified Airtable records to S3 daily. Here we serialize
every table to a timestamped JSON file — written to S3 when S3 is configured,
otherwise to a local directory. This is also the analytics substrate (see
analytics.py), matching prod's snapshot-replay approach.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from bam.config import Settings, settings as default_settings
from bam.models import (
    Distro,
    FormSubmission,
    FulfilledRequestCount,
    Household,
    Request,
    SocialServiceRequest,
    utcnow,
)
from bam.schemas import SnapshotReport

_TABLES = {
    "households": Household,
    "requests": Request,
    "social_service_requests": SocialServiceRequest,
    "distros": Distro,
    "fulfilled_request_counts": FulfilledRequestCount,
    "form_submissions": FormSubmission,
}


def _serialize(session: Session) -> dict:
    out: dict[str, list] = {}
    for name, model in _TABLES.items():
        rows = session.exec(select(model)).all()
        out[name] = [json.loads(r.model_dump_json()) for r in rows]
    return out


def snapshot_data(
    session: Session,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> SnapshotReport:
    """Write a full JSON snapshot; return where it went and the row counts."""
    settings = settings or default_settings
    now = now or utcnow()
    data = _serialize(session)
    counts = {name: len(rows) for name, rows in data.items()}
    key = f"bam-snapshot-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = json.dumps({"generated_at": now.isoformat(), "tables": data}, indent=2)

    if settings.s3_bucket and settings.s3_access_key_id:
        location = _write_s3(settings, key, payload)
    else:
        target = Path(settings.snapshot_dir)
        target.mkdir(parents=True, exist_ok=True)
        path = target / key
        path.write_text(payload, encoding="utf-8")
        location = str(path)
    return SnapshotReport(location=location, counts=counts)


def _write_s3(settings: Settings, key: str, payload: str) -> str:
    try:
        import boto3  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "S3 snapshots need boto3: pip install boto3 (or unset BAM_S3_*)"
        ) from exc
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
    )
    client.put_object(
        Bucket=settings.s3_bucket, Key=key, Body=payload.encode("utf-8"),
        ContentType="application/json",
    )
    endpoint = settings.s3_endpoint_url or "s3"
    return f"{endpoint}/{settings.s3_bucket}/{key}"
