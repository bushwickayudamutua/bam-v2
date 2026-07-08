"""Instance (white-label) configuration.

A deployment's *identity* — org name, branding, which features are on, and its
request-type/language catalog — lives in a non-secret config file (TOML or
JSON) pointed to by ``BAM_INSTANCE_CONFIG``. Secrets and operational knobs stay
in ``bam.config.Settings`` (env vars). When no instance file is set, the
defaults below reproduce the Bushwick Ayuda Mutua identity, so an unconfigured
deployment is unchanged.

This is what makes the system white-labelable: any mutual-aid group can point
``BAM_INSTANCE_CONFIG`` at their own ``instance.toml`` (scaffold one with
``bam init-instance``) and launch a fully rebranded, reconfigured instance with
no code changes. ``GET /config`` serves the resolved config to the console,
which themes itself from it at boot.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class OrgInfo(BaseModel):
    name: str = "Bushwick Ayuda Mutua"
    short_name: str = "BAM"
    tagline: str = "Mutual aid distribution operations"
    timezone: str = "America/New_York"
    locale: str = "en"


class Branding(BaseModel):
    """Colors are applied as CSS variables at console boot; ``logo`` is either
    a built-in mark (``hands`` = the BAM clasped hands, ``initials`` = the org
    short name in a chip) or a raw inline ``<svg>…</svg>`` string."""

    primary_color: str = "#ff6e2e"
    accent_color: str = "#aecee6"
    theme_color: str = "#ff6e2e"
    title: str = "Bushwick Ayuda Mutua"
    logo: str = "hands"


class Features(BaseModel):
    """Which console views/modules are enabled. Disabled views are hidden from
    the nav (the API routes stay mounted; this is UX gating, not authz)."""

    checkin: bool = True
    appointments: bool = True
    lookup: bool = True
    intake: bool = True
    outreach: bool = True
    furniture: bool = True
    services: bool = True
    distros: bool = True
    dashboard: bool = True
    admin: bool = True


class CatalogType(BaseModel):
    key: str
    label: str
    category: str
    expiry_days: Optional[int] = None


class CatalogOverride(BaseModel):
    """Optional per-instance catalog. Omit a field to keep the BAM default for
    it. ``goods``/``social_services`` fully REPLACE the default lists when
    given; ``languages`` replaces the default language options."""

    languages: Optional[list[str]] = None
    goods: Optional[list[CatalogType]] = None
    social_services: Optional[list[CatalogType]] = None


class InstanceConfig(BaseModel):
    org: OrgInfo = Field(default_factory=OrgInfo)
    branding: Branding = Field(default_factory=Branding)
    features: Features = Field(default_factory=Features)
    catalog: CatalogOverride = Field(default_factory=CatalogOverride)
    #: Overrides ``BAM_REQUEST_FORM_URL`` for the outreach [REQUEST_URL] token.
    request_form_url: Optional[str] = None
    #: Optional provider hints for the console/docs (secrets stay in env).
    sms_provider: Optional[str] = None
    geocoder: Optional[str] = None


def _read_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"BAM_INSTANCE_CONFIG points at a missing file: {path}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        return json.loads(text)
    return tomllib.loads(text)


def load_instance_config(path: str | None = None) -> InstanceConfig:
    """Load the instance config from ``path`` (or ``BAM_INSTANCE_CONFIG``);
    return the BAM defaults when unset."""
    path = path or os.environ.get("BAM_INSTANCE_CONFIG")
    if not path:
        return InstanceConfig()
    return InstanceConfig.model_validate(_read_file(path))


#: Process-wide resolved instance config. ``reload()`` re-reads (tests, CLI).
instance: InstanceConfig = load_instance_config()


def apply_to_runtime(cfg: InstanceConfig | None = None) -> None:
    """Apply an instance config to the running process: swap the request-type
    catalog + languages, and override the request-form URL. Idempotent; safe
    to call at every app startup."""
    cfg = cfg or instance
    from bam import request_types as rt

    def _to_types(items: list[CatalogType] | None) -> list["rt.RequestType"] | None:
        if items is None:
            return None
        return [
            rt.RequestType(i.key, i.label, i.category, i.expiry_days or rt.DEFAULT_EXPIRY_DAYS)
            for i in items
        ]

    rt.load_catalog(
        goods=_to_types(cfg.catalog.goods),
        social_services=_to_types(cfg.catalog.social_services),
        languages=cfg.catalog.languages,
    )
    if cfg.request_form_url:
        from bam.config import settings

        settings.request_form_url = cfg.request_form_url


def reload(path: str | None = None) -> InstanceConfig:
    global instance
    instance = load_instance_config(path)
    return instance
