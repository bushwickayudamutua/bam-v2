"""Minimal Airtable REST client for the migration (stdlib only).

Covers the two endpoints the import needs — the Metadata API for table
schemas and the records API with pagination — plus Airtable's rate limits:
5 requests/second per base and a 30-second penalty on HTTP 429.

Needs a personal access token with ``schema.bases:read`` and
``data.records:read`` scopes on the base.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterator

API_ROOT = "https://api.airtable.com/v0"
_MIN_REQUEST_INTERVAL = 0.21  # stay under 5 rps
_RATE_LIMIT_PAUSE = 30.0  # Airtable's documented 429 penalty
_MAX_RETRIES = 5


class AirtableError(RuntimeError):
    pass


class AirtableClient:
    def __init__(
        self,
        token: str,
        base_id: str,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise AirtableError(
                "An Airtable personal access token is required "
                "(scopes: schema.bases:read, data.records:read)"
            )
        self._token = token
        self.base_id = base_id
        self._sleeper = sleeper
        self._last_request_at = 0.0

    def _get(self, url: str) -> dict:
        for attempt in range(_MAX_RETRIES):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < _MIN_REQUEST_INTERVAL:
                self._sleeper(_MIN_REQUEST_INTERVAL - elapsed)
            request = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {self._token}"}
            )
            self._last_request_at = time.monotonic()
            try:
                with urllib.request.urlopen(request) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < _MAX_RETRIES - 1:
                    self._sleeper(_RATE_LIMIT_PAUSE)
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise AirtableError(f"Airtable API {exc.code} for {url}: {detail}") from exc
        raise AirtableError(f"Rate-limited after {_MAX_RETRIES} attempts: {url}")

    def schema(self) -> list[dict]:
        """Table schemas: [{id, name, fields: [{name, type, ...}], ...}]."""
        data = self._get(f"{API_ROOT}/meta/bases/{self.base_id}/tables")
        return data.get("tables", [])

    def records(self, table: str) -> Iterator[dict]:
        """All records of a table (by id or name), following pagination.

        Yields raw record dicts: {"id", "createdTime", "fields": {...}}.
        """
        offset: str | None = None
        while True:
            params = {"pageSize": "100"}
            if offset:
                params["offset"] = offset
            url = (
                f"{API_ROOT}/{self.base_id}/{urllib.parse.quote(table)}"
                f"?{urllib.parse.urlencode(params)}"
            )
            data = self._get(url)
            yield from data.get("records", [])
            offset = data.get("offset")
            if not offset:
                return


class SnapshotSource:
    """Record source backed by a directory written by ``dump_snapshot``.

    Lets the import re-run offline, and keeps the raw pull auditable. The
    snapshot contains PII — keep the directory local (it is gitignored).
    """

    def __init__(self, directory: str) -> None:
        from pathlib import Path

        self._dir = Path(directory)

    def schema(self) -> list[dict]:
        return json.loads((self._dir / "schema.json").read_text(encoding="utf-8"))

    def records(self, table: str) -> Iterator[dict]:
        path = self._dir / f"{table}.json"
        if not path.exists():
            return iter(())
        return iter(json.loads(path.read_text(encoding="utf-8")))


def dump_snapshot(client: AirtableClient, directory: str) -> dict[str, int]:
    """Pull the base's schema and every table's records into ``directory``.

    Returns record counts per table.
    """
    from pathlib import Path

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    tables = client.schema()
    (target / "schema.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    counts: dict[str, int] = {}
    for table in tables:
        name = table["name"]
        records = list(client.records(table["id"]))
        (target / f"{name}.json").write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        counts[name] = len(records)
    return counts
