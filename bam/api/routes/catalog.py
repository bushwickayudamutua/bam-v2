"""Catalog route: the vocabulary the console (and any client) builds UI from.

Single source of truth for request types (spec 4/9) and the production
language options (background section 6), so the intake and outreach views
share one vocabulary and cannot drift.
"""

from __future__ import annotations

from fastapi import APIRouter

from bam.request_types import GOODS, LANGUAGES, SOCIAL_SERVICES

router = APIRouter()


@router.get("/catalog")
def get_catalog() -> dict:
    def entry(t) -> dict:
        return {"key": t.key, "label": t.label, "category": t.category}

    return {
        "goods": [entry(t) for t in GOODS],
        "social_services": [entry(t) for t in SOCIAL_SERVICES],
        "languages": list(LANGUAGES),
    }
