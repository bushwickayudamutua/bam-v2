"""Phone/email normalization and validation (the V2 "/clean-record" role).

Phone numbers normalize to E.164 with US as the default region. The intake
flow stores the outcome on the household via the spec's flag fields:
``Invalid Phone Number?``, ``Int'l Phone Number?`` and ``Email Error``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import phonenumbers
from email_validator import EmailNotValidError, validate_email


@dataclass
class PhoneValidation:
    normalized: str | None  # E.164, or None if unparseable
    valid: bool
    international: bool  # valid but outside the US


@dataclass
class EmailValidation:
    normalized: str | None
    error: str | None


def validate_phone(raw: str | None, default_region: str = "US") -> PhoneValidation:
    if not raw or not raw.strip():
        return PhoneValidation(normalized=None, valid=False, international=False)
    try:
        parsed = phonenumbers.parse(raw.strip(), default_region)
    except phonenumbers.NumberParseException:
        return PhoneValidation(normalized=None, valid=False, international=False)
    if not phonenumbers.is_valid_number(parsed):
        return PhoneValidation(normalized=None, valid=False, international=False)
    normalized = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    region = phonenumbers.region_code_for_number(parsed)
    return PhoneValidation(normalized=normalized, valid=True, international=region != "US")


def validate_email_address(raw: str | None) -> EmailValidation:
    if not raw or not raw.strip():
        return EmailValidation(normalized=None, error=None)
    try:
        result = validate_email(raw.strip(), check_deliverability=False)
        return EmailValidation(normalized=result.normalized, error=None)
    except EmailNotValidError as exc:
        return EmailValidation(normalized=None, error=str(exc))


def hash_phone(e164: str) -> str:
    """Stable hash so anonymized households reconnect on re-request."""
    return hashlib.sha256(e164.encode("utf-8")).hexdigest()
