"""SMS provider abstraction.

The spec's outreach flow (6.2) sends templated text blasts through an SMS
provider. Production uses Twilio (or Dialpad historically); tests and dry
runs use the console provider. Templates support the placeholders the spec
names: ``[FIRST_NAME]`` and ``[REQUEST_URL]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bam.config import Settings


@dataclass
class SMSResult:
    to: str
    body: str
    ok: bool
    provider_id: str | None = None
    error: str | None = None


class SMSProvider(Protocol):
    def send(self, to: str, body: str) -> SMSResult: ...


def render_template(template: str, *, first_name: str = "", request_url: str = "") -> str:
    return template.replace("[FIRST_NAME]", first_name or "").replace(
        "[REQUEST_URL]", request_url or ""
    )


def get_provider(settings: Settings) -> SMSProvider:
    if settings.sms_provider == "twilio":
        from bam.sms.twilio_provider import TwilioSMSProvider

        return TwilioSMSProvider(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_from_number,
        )
    from bam.sms.console import ConsoleSMSProvider

    return ConsoleSMSProvider()
