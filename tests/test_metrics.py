"""Metrics service tests (CONTRACT.md `bam/services/metrics.py`, spec 5).

Covers the FulfilledRequestCount upsert, the open-request-count payload shape
(goods + social services combined, catalog labels, count-desc/key-asc sort),
the website JSON writer, and the fulfilled-count date-range filter.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from bam.models import RequestStatus, SocialServiceRequest
from bam.request_types import label_for
from bam.services.metrics import (
    fulfilled_counts,
    increment_fulfilled_count,
    open_request_counts,
    update_website_request_data,
)
from tests.conftest import FIXED_NOW


class TestIncrementFulfilledCount:
    def test_creates_row_on_first_increment(self, session):
        row = increment_fulfilled_count(session, FIXED_NOW.date(), "soap")
        assert row.id is not None
        assert row.date == FIXED_NOW.date()
        assert row.request_type == "soap"
        assert row.count == 1

    def test_upserts_existing_date_type_row(self, session):
        first = increment_fulfilled_count(session, FIXED_NOW.date(), "soap")
        second = increment_fulfilled_count(session, FIXED_NOW.date(), "soap", n=4)
        assert second.id == first.id
        assert second.count == 5
        stored = fulfilled_counts(session)
        assert len(stored) == 1
        assert stored[0].count == 5

    def test_distinct_date_or_type_gets_own_row(self, session):
        base = increment_fulfilled_count(session, FIXED_NOW.date(), "soap")
        other_type = increment_fulfilled_count(session, FIXED_NOW.date(), "groceries")
        other_date = increment_fulfilled_count(
            session, FIXED_NOW.date() - timedelta(days=1), "soap"
        )
        assert len({base.id, other_type.id, other_date.id}) == 3
        assert other_type.count == 1
        assert other_date.count == 1
        assert len(fulfilled_counts(session)) == 3


class TestOpenRequestCounts:
    def _seed(self, session, make_household, make_request) -> None:
        h1 = make_household(session)
        h2 = make_household(session)
        # Open goods: soap x2, groceries x1.
        make_request(session, h1, type="soap")
        make_request(session, h2, type="soap")
        make_request(session, h1, type="groceries")
        # Closed goods must not be counted.
        make_request(session, h2, type="groceries", status=RequestStatus.TIMEOUT)
        make_request(session, h2, type="pads", status=RequestStatus.DELIVERED)
        # Open social service: housing x1; closed one excluded.
        session.add(SocialServiceRequest(type="housing", household_id=h1.id))
        session.add(
            SocialServiceRequest(
                type="tutoring", household_id=h2.id, status=RequestStatus.DELIVERED
            )
        )
        session.commit()

    def test_shape_labels_and_sort_order(self, session, make_household, make_request):
        self._seed(session, make_household, make_request)
        data = open_request_counts(session, now=FIXED_NOW)

        assert set(data.keys()) == {"generated_at", "counts"}
        assert data["generated_at"] == FIXED_NOW.isoformat()

        counts = data["counts"]
        assert all(set(entry.keys()) == {"type", "label", "count"} for entry in counts)
        # Goods and social services combined; closed statuses excluded.
        assert [(e["type"], e["count"]) for e in counts] == [
            ("soap", 2),  # highest count first
            ("groceries", 1),  # ties broken by type key ascending
            ("housing", 1),
        ]
        # Labels come from the trilingual catalog.
        for entry in counts:
            assert entry["label"] == label_for(entry["type"])
            assert " / " in entry["label"]

    def test_empty_database_yields_empty_counts(self, session):
        data = open_request_counts(session, now=FIXED_NOW)
        assert data["counts"] == []
        assert data["generated_at"] == FIXED_NOW.isoformat()


class TestUpdateWebsiteRequestData:
    def test_writes_valid_json_and_returns_dict(
        self, session, make_household, make_request, tmp_path
    ):
        household = make_household(session)
        make_request(session, household, type="soap")
        session.add(SocialServiceRequest(type="housing", household_id=household.id))
        session.commit()

        target = tmp_path / "website_request_data.json"
        data = update_website_request_data(session, path=str(target))

        assert target.exists()
        on_disk = json.loads(target.read_text(encoding="utf-8"))
        assert on_disk == data
        assert {e["type"]: e["count"] for e in data["counts"]} == {
            "soap": 1,
            "housing": 1,
        }
        for entry in data["counts"]:
            assert entry["label"] == label_for(entry["type"])


class TestFulfilledCounts:
    def _seed(self, session) -> None:
        increment_fulfilled_count(session, date(2026, 6, 1), "soap")
        increment_fulfilled_count(session, date(2026, 6, 10), "groceries", n=3)
        increment_fulfilled_count(session, date(2026, 6, 10), "soap", n=2)
        increment_fulfilled_count(session, date(2026, 6, 20), "soap")

    def test_no_bounds_returns_all_ordered(self, session):
        self._seed(session)
        rows = fulfilled_counts(session)
        assert [(r.date, r.request_type) for r in rows] == [
            (date(2026, 6, 1), "soap"),
            (date(2026, 6, 10), "groceries"),
            (date(2026, 6, 10), "soap"),
            (date(2026, 6, 20), "soap"),
        ]

    def test_start_and_end_are_inclusive(self, session):
        self._seed(session)
        rows = fulfilled_counts(session, start=date(2026, 6, 10), end=date(2026, 6, 10))
        assert [(r.date, r.request_type, r.count) for r in rows] == [
            (date(2026, 6, 10), "groceries", 3),
            (date(2026, 6, 10), "soap", 2),
        ]

    def test_open_ended_bounds(self, session):
        self._seed(session)
        from_mid = fulfilled_counts(session, start=date(2026, 6, 5))
        assert {r.date for r in from_mid} == {date(2026, 6, 10), date(2026, 6, 20)}
        until_mid = fulfilled_counts(session, end=date(2026, 6, 15))
        assert {r.date for r in until_mid} == {date(2026, 6, 1), date(2026, 6, 10)}

    def test_range_excluding_everything(self, session):
        self._seed(session)
        assert fulfilled_counts(session, start=date(2026, 7, 1)) == []
