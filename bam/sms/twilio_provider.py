"""Twilio SMS provider (optional dependency: ``pip install bam-v2[twilio]``)."""

from __future__ import annotations

from bam.sms.base import SMSResult


class TwilioSMSProvider:
    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        try:
            from twilio.rest import Client
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The twilio package is required for BAM_SMS_PROVIDER=twilio; "
                "install with: pip install 'bam-v2[twilio]'"
            ) from exc
        if not (account_sid and auth_token and from_number):
            raise RuntimeError(
                "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER must be set"
            )
        self._client = Client(account_sid, auth_token)
        self._from_number = from_number

    def send(self, to: str, body: str) -> SMSResult:
        try:
            message = self._client.messages.create(to=to, from_=self._from_number, body=body)
            return SMSResult(to=to, body=body, ok=True, provider_id=message.sid)
        except Exception as exc:  # noqa: BLE001 — provider errors become result rows
            return SMSResult(to=to, body=body, ok=False, error=str(exc))
