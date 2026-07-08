"""Catalog route: the vocabulary the console (and any client) builds UI from.

Single source of truth for request types (spec 4/9) and the production
language options (background section 6), so the intake and outreach views
share one vocabulary and cannot drift.
"""

from __future__ import annotations

from fastapi import APIRouter

import bam.instance as instance_mod
from bam.request_types import GOODS, LANGUAGES, SOCIAL_SERVICES

router = APIRouter()


def _catalog_payload() -> dict:
    def entry(t) -> dict:
        return {"key": t.key, "label": t.label, "category": t.category}

    return {
        "goods": [entry(t) for t in GOODS],
        "social_services": [entry(t) for t in SOCIAL_SERVICES],
        "languages": list(LANGUAGES),
    }


@router.get("/catalog")
def get_catalog() -> dict:
    return _catalog_payload()


@router.get("/config")
def get_config() -> dict:
    """The white-label instance config the console themes itself from:
    org identity, branding, enabled features, and the resolved catalog."""
    cfg = instance_mod.instance
    return {
        "org": cfg.org.model_dump(),
        "branding": cfg.branding.model_dump(),
        "features": cfg.features.model_dump(),
        "catalog": _catalog_payload(),
        "request_form_url": cfg.request_form_url,
    }
