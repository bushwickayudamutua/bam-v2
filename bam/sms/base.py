"""SMS provider abstraction.

The spec's outreach flow (6.2) sends templated text blasts through an SMS
provider. Production uses Twilio (or Dialpad historically); tests and dry
runs use the console provider. Templates support the placeholders the spec
names: ``[FIRST_NAME]`` and ``[REQUEST_URL]``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from bam.config import Settings

#: Send-language buckets a blast template map may key on (bam-automation
#: send_mass_text.py). ``All`` is synthesized, not a stored template.
SEND_LANGUAGES = ["Spanish", "Cantonese", "English", "All"]
#: Order the "All" message concatenates the per-language texts in (verbatim).
ALL_LANGUAGE_ORDER = ["Spanish", "Cantonese", "English"]


def resolve_send_language(languages: Sequence[str]) -> str:
    """Which language to text a household in, per bam-automation
    ``send_mass_text.determine_message_language`` (exact if/elif order, first
    match wins). Households store full trilingual labels, so we substring-match
    the English middle token — each of Spanish/Quechua/Mandarin/Cantonese/
    English is a unique substring of exactly one catalog label. English
    resolves to ``English`` only when nothing higher matches (effectively the
    sole routing signal); anything unroutable falls to ``All``.
    """
    joined = ",".join(languages or [])
    if "Spanish" in joined:
        return "Spanish"
    if "Quechua" in joined:  # we only write messages in Spanish
        return "Spanish"
    if "Mandarin" in joined:  # we only write messages in Cantonese
        return "Cantonese"
    if "Cantonese" in joined:
        return "Cantonese"
    if "English" in joined:
        return "English"
    return "All"


def assemble_all_message(templates: Mapping[str, str]) -> str:
    """Concatenate the supplied per-language texts in ``ALL_LANGUAGE_ORDER``,
    blank-line separated (verbatim). Absent languages are omitted."""
    return "\n\n".join(templates[lang] for lang in ALL_LANGUAGE_ORDER if lang in templates)


def select_template(templates: Mapping[str, str], languages: Sequence[str]) -> str:
    """Pick the body for a household (bam-automation ``build_twilio_message``):
    resolve the send-language; if a template for it was supplied use it,
    otherwise synthesize the "All" concatenation from whatever texts exist."""
    body = templates.get(resolve_send_language(languages))
    if body is None:
        body = assemble_all_message(templates)
    return body


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
    if settings.sms_provider == "dialpad":
        from bam.sms.dialpad_provider import DialpadSMSProvider

        return DialpadSMSProvider(
            api_token=settings.dialpad_api_token,
            user_id=settings.dialpad_user_id,
            send_interval_seconds=settings.dialpad_send_interval_seconds,
        )
    from bam.sms.console import ConsoleSMSProvider

    return ConsoleSMSProvider()
