"""Application settings.

All values can be overridden via environment variables so the same code runs
locally (SQLite + console SMS) and in production (Postgres + Twilio) without
changes. Defaults encode the operational constants from the V2 spec.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("BAM_DATABASE_URL", "sqlite:///bam.db")
    )

    # SMS delivery
    sms_provider: str = field(
        default_factory=lambda: os.environ.get("BAM_SMS_PROVIDER", "console")
    )
    twilio_account_sid: str = field(
        default_factory=lambda: os.environ.get("TWILIO_ACCOUNT_SID", "")
    )
    twilio_auth_token: str = field(
        default_factory=lambda: os.environ.get("TWILIO_AUTH_TOKEN", "")
    )
    twilio_from_number: str = field(
        default_factory=lambda: os.environ.get("TWILIO_FROM_NUMBER", "")
    )

    # Text blast behaviour (spec 6.2: max 240 messages, 30 msgs then 30s delay)
    sms_max_messages: int = field(default_factory=lambda: _env_int("BAM_SMS_MAX_MESSAGES", 240))
    sms_batch_size: int = field(default_factory=lambda: _env_int("BAM_SMS_BATCH_SIZE", 30))
    sms_batch_pause_seconds: int = field(
        default_factory=lambda: _env_int("BAM_SMS_BATCH_PAUSE_SECONDS", 30)
    )
    request_form_url: str = field(
        default_factory=lambda: os.environ.get(
            "BAM_REQUEST_FORM_URL", "https://forms.fillout.com/t/ivajQbwoWxus"
        )
    )

    # Request lifecycle (spec 2 / 4: 14-day standard window, 30 days pots & pans)
    default_expiry_days: int = field(default_factory=lambda: _env_int("BAM_DEFAULT_EXPIRY_DAYS", 14))
    extended_expiry_days: int = field(
        default_factory=lambda: _env_int("BAM_EXTENDED_EXPIRY_DAYS", 30)
    )
    max_missed_appointments: int = field(
        default_factory=lambda: _env_int("BAM_MAX_MISSED_APPOINTMENTS", 2)
    )

    # Privacy: how long after a household's last activity (with no open
    # requests) its PII is retained before being anonymized.
    pii_retention_days: int = field(default_factory=lambda: _env_int("BAM_PII_RETENTION_DAYS", 30))

    # Business dates (last texted, fulfilled counts, processing dates) are
    # derived in this timezone so an evening distro in Brooklyn is not
    # recorded under the next UTC day.
    local_timezone: str = field(
        default_factory=lambda: os.environ.get("BAM_LOCAL_TIMEZONE", "America/New_York")
    )

    # Website request-count JSON output (spec 5: UpdateWebsiteRequestData)
    website_data_path: str = field(
        default_factory=lambda: os.environ.get(
            "BAM_WEBSITE_DATA_PATH", "website_request_data.json"
        )
    )

    # Airtable migration source (the production V2 base). Token needs the
    # schema.bases:read and data.records:read scopes on this base.
    airtable_token: str = field(
        default_factory=lambda: os.environ.get("BAM_AIRTABLE_V2_TOKEN")
        or os.environ.get("AIRTABLE_TOKEN", "")
    )
    airtable_base_id: str = field(
        default_factory=lambda: os.environ.get(
            "BAM_AIRTABLE_V2_BASE_ID", "appjIo54Z8MWrqhlI"
        )
    )


settings = Settings()
