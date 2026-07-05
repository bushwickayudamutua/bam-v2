"""Console SMS provider: logs messages instead of sending them.

Used for local development, dry runs, and tests. Keeps every "sent" message
in memory so callers can inspect what would have gone out.
"""

from __future__ import annotations

import logging

from bam.sms.base import SMSResult

logger = logging.getLogger(__name__)


class ConsoleSMSProvider:
    def __init__(self) -> None:
        self.sent: list[SMSResult] = []

    def send(self, to: str, body: str) -> SMSResult:
        result = SMSResult(to=to, body=body, ok=True, provider_id=f"console-{len(self.sent) + 1}")
        self.sent.append(result)
        logger.info("SMS to %s: %s", to, body)
        return result
