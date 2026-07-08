"""Dialpad SMS provider (optional dependency: ``pip install bam-v2[dialpad]``).

Ports the per-message send behaviour of bushwickayudamutua/bam-automation's
``core/bam_core/lib/dialpad.py`` (main). It is deliberately *thin*: the
outreach service (``bam/services/outreach.py``) already owns the spec-6.2
batch pause (30 messages → 30s), the 240-message cap, ``[FIRST_NAME]`` /
``[REQUEST_URL]`` rendering, URL randomization, and ``last_texted`` stamping.
This provider only does what the org's low-level sender does: clean the
number, greedy-split bodies over 160 chars into separate sends, POST each to
Dialpad, and pause ~2s per recipient.
"""

from __future__ import annotations

import time
from typing import Callable

from bam.sms.base import SMSResult

DIALPAD_API_URL = "https://dialpad.com/api/v2/sms"
MAX_MESSAGE_LENGTH = 160


def clean_phone_number(phone: str | None) -> str:
    """Dialpad's own cleaner (verbatim): 10 digits → +1…, 11 → +…, else raw
    digits (no ``+``). Falsy → empty string."""
    if not phone:
        return ""
    digits = "".join(filter(str.isdigit, str(phone)))
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11:
        return "+" + digits
    return digits


def split_message(message: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Greedy word-split (verbatim from the org): a body over ``max_length``
    is broken at word boundaries into separate chunks. A single word longer
    than ``max_length`` goes out as its own oversize chunk (not hard-split),
    matching the source exactly."""
    words = message.split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > max_length:
            if current.strip():
                chunks.append(current.strip())
            current = word + " "
        else:
            current += word + " "
    if current.strip():
        chunks.append(current.strip())
    return chunks or [""]


class DialpadSMSProvider:
    """Send SMS through Dialpad's REST API.

    ``http_post`` is injectable so tests run without ``httpx`` or the network;
    in production it defaults to ``httpx.post``. ``sleeper`` is injectable so
    the per-recipient pause is instant in tests.
    """

    def __init__(
        self,
        api_token: str,
        user_id: str,
        api_url: str = DIALPAD_API_URL,
        send_interval_seconds: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
        http_post: Callable[..., object] | None = None,
    ) -> None:
        if not (api_token and user_id):
            raise RuntimeError(
                "BAM_DIALPAD_API_TOKEN and BAM_DIALPAD_USER_ID must be set for "
                "BAM_SMS_PROVIDER=dialpad"
            )
        self._api_token = api_token
        self._user_id = user_id
        self._api_url = api_url
        self._interval = send_interval_seconds
        self._sleep = sleeper
        if http_post is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "The httpx package is required for BAM_SMS_PROVIDER=dialpad; "
                    "install with: pip install 'bam-v2[dialpad]'"
                ) from exc

            def http_post(url: str, *, json: dict, headers: dict) -> object:
                return httpx.post(url, json=json, headers=headers, timeout=30)

        self._post = http_post

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {self._api_token}",
        }

    def send(self, to: str, body: str) -> SMSResult:
        clean_to = clean_phone_number(to)
        provider_id: str | None = None
        for chunk in split_message(body):
            payload = {
                "infer_country_code": False,
                "to_numbers": clean_to,
                "text": chunk,
                "user_id": self._user_id,
            }
            try:
                resp = self._post(self._api_url, json=payload, headers=self._headers())
            except Exception as exc:  # noqa: BLE001 — provider errors become result rows
                return SMSResult(to=to, body=body, ok=False, error=str(exc))
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {}
            if not getattr(resp, "is_success", False):
                message = (data.get("error", {}) or {}).get("message") or "Unknown error"
                return SMSResult(to=to, body=body, ok=False, error=message)
            if provider_id is None and data.get("id") is not None:
                provider_id = str(data["id"])
        # One pause per recipient (not per chunk), matching the org's 2s sleep.
        self._sleep(self._interval)
        return SMSResult(to=to, body=body, ok=True, provider_id=provider_id or "dialpad-sent")
