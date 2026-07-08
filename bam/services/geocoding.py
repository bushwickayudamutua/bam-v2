"""Address geocoding (parity with bam-automation's ``format_address`` in
``core/bam_core/utils/geo.py``).

Resolves an address to a cleaned address, an accuracy grade, an NYC Building
Identification Number (BIN), and a privacy-truncated Open Location Code
(plus_code), via a three-call Google chain + NYC Planning Labs GeoSearch:

- ``GoogleNycGeocoder`` — the real pipeline (active when a Google Maps key is
  configured): Google Places Autocomplete (accuracy + candidate address) →
  Google Address Validation (cleaned address, fallback accuracy) → Google
  Geocoding (lat/lng, encoded to an 8-char plus code via ``bam.services.olc``)
  → NYC Planning Labs GeoSearch (BIN).
- ``NoopGeocoder`` — a safe passthrough that just formats the address; used
  when no key is set (tests, local dev), so intake still works.

Every HTTP call is swallowed on error — geocoding must never break intake.
``get_geocoder`` follows the SMS ``get_provider`` gating pattern.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

from bam.config import Settings
from bam.services import olc

PLACES_URL = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
VALIDATION_URL = "https://addressvalidation.googleapis.com/v1:validateAddress"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"

DEFAULT_CITY_STATE = "Brooklyn, NY"
COMMON_ZIPCODE_MISTAKES = {"112007": "11207"}
DEFAULT_BIN_RESPONSES = ["3000000", "1000000"]  # NYC placeholder BINs to discard
# Google Places is bounded to a 10-mile radius around "Mayday" (the org's hub).
MAYDAY_LOCATION = "40.7041015,-73.9163523"
MAYDAY_RADIUS = 16093.44  # 10 miles in meters

_ADDRESS_SUBS = (
    (" PISO", " FLOOR"),
    (" APARTAMENTO", " APT"),
    (" APTO", " APT"),
    (" APRT", " APT"),
    (" DE ", " "),
)


@dataclass
class GeocodeResult:
    cleaned_address: str | None
    address_accuracy: str  # Apartment / Building / No result
    bin: str | None  # NYC Building Identification Number
    plus_code: str | None  # Open Location Code (8-char, privacy-truncated)


class Geocoder(Protocol):
    def geocode(
        self, street_address: str | None, city_state: str | None, zip_code: str | None
    ) -> GeocodeResult: ...


def _joined(street: str | None, city_state: str | None, zip_code: str | None) -> str:
    return " ".join(p for p in (street, city_state, zip_code) if p).strip()


def _fix_address(address: str) -> str:
    fixed = (address or "").upper().strip()
    for old, new in _ADDRESS_SUBS:
        fixed = fixed.replace(old, new)
    return fixed.rstrip("#").strip()


def _fix_zip_code(zip_code: str | None) -> str:
    z = (zip_code or "").strip()
    return COMMON_ZIPCODE_MISTAKES.get(z, z)


class NoopGeocoder:
    """Passthrough: format the address, no accuracy/BIN/plus_code."""

    def geocode(self, street_address, city_state, zip_code) -> GeocodeResult:
        parts = [p for p in (street_address, city_state, zip_code) if p]
        return GeocodeResult(
            cleaned_address=", ".join(parts) or None,
            address_accuracy="No result",
            bin=None,
            plus_code=None,
        )


class GoogleNycGeocoder:
    """The real chain. ``fetch`` (url, method, body) → parsed JSON is
    injectable so tests run without the network; it defaults to urllib."""

    def __init__(self, api_key: str, fetch: Callable[..., dict] | None = None) -> None:
        if not api_key:
            raise ValueError("GoogleNycGeocoder needs a Google Maps API key")
        self._api_key = api_key
        self._fetch = fetch or self._default_fetch

    def _default_fetch(self, url: str, *, method: str = "GET", body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (trusted URLs)
            return json.loads(resp.read().decode("utf-8"))

    def _build_query(self, street, city_state, zip_code) -> str:
        return (
            f"{_fix_address(street)}, {city_state or DEFAULT_CITY_STATE} "
            f"{_fix_zip_code(zip_code)}"
        ).strip().upper()

    def geocode(self, street_address, city_state, zip_code) -> GeocodeResult:
        if not (street_address and street_address.strip()):
            return GeocodeResult(_joined(street_address, city_state, zip_code) or None, "No result", None, None)

        query = self._build_query(street_address, city_state, zip_code)
        accuracy = "No result"
        description = query

        # 1. Places Autocomplete → candidate description + accuracy from types.
        try:
            params = urllib.parse.urlencode(
                {
                    "input": query,
                    "key": self._api_key,
                    "location": MAYDAY_LOCATION,
                    "radius": MAYDAY_RADIUS,
                    "strictbounds": "true",
                    "language": "en-US",
                }
            )
            preds = self._fetch(f"{PLACES_URL}?{params}").get("predictions") or []
        except Exception:  # noqa: BLE001 — geocoding must never break intake
            preds = []
        if preds:
            description = preds[0].get("description") or query
            types = preds[0].get("types") or []
            if "subpremise" in types:
                accuracy = "Apartment"
            elif "premise" in types:
                accuracy = "Building"

        # 2. Address Validation → cleaned address (+ fallback accuracy).
        cleaned: str | None = None
        verdict: dict = {}
        try:
            vresp = self._fetch(
                f"{VALIDATION_URL}?key={self._api_key}",
                method="POST",
                body={"address": {"addressLines": [description]}},
            )
            result = vresp.get("result") or {}
            verdict = result.get("verdict") or {}
            std = (result.get("uspsData") or {}).get("standardizedAddress") or {}
            if std.get("firstAddressLine"):
                cleaned = (
                    f"{std.get('firstAddressLine', '')} {std.get('cityStateZipAddressLine', '')}"
                ).strip().upper()
            elif (result.get("address") or {}).get("formattedAddress"):
                cleaned = result["address"]["formattedAddress"].upper()
        except Exception:  # noqa: BLE001
            pass
        if cleaned is None:
            cleaned = description.upper()
        cleaned = cleaned.replace(" # ", " APT ")

        if accuracy == "No result" and verdict:
            vg = verdict.get("validationGranularity", "")
            ig = verdict.get("inputGranularity", "")
            if vg == "SUB_PREMISE":
                accuracy = "Apartment"
            elif vg == "PREMISE" or ig.endswith("PREMISE"):
                accuracy = "Building"

        # BIN + plus_code only when we actually resolved a place.
        plus_code: str | None = None
        bin_number: str | None = None
        if accuracy != "No result":
            plus_code = self._plus_code(cleaned)
            bin_number = self._nyc_bin(cleaned)
        return GeocodeResult(cleaned, accuracy, bin_number, plus_code)

    def _plus_code(self, cleaned: str) -> str | None:
        try:
            params = urllib.parse.urlencode({"address": cleaned, "key": self._api_key})
            results = self._fetch(f"{GEOCODE_URL}?{params}").get("results") or []
        except Exception:  # noqa: BLE001
            return None
        if not results:
            return None
        loc = ((results[0].get("geometry") or {}).get("location")) or {}
        return olc.encode(loc.get("lat"), loc.get("lng"))

    def _nyc_bin(self, address: str) -> str | None:
        try:
            params = urllib.parse.urlencode({"text": address, "size": 1})
            features = self._fetch(f"{GEOSEARCH_URL}?{params}").get("features") or []
        except Exception:  # noqa: BLE001
            return None
        if not features:
            return None
        pad = ((features[0].get("properties") or {}).get("addendum") or {}).get("pad") or {}
        bin_number = pad.get("bin")
        if bin_number is None or str(bin_number) in DEFAULT_BIN_RESPONSES:
            return None
        return str(bin_number)


def get_geocoder(settings: Settings) -> Geocoder:
    provider = settings.geocoder_provider
    if provider == "noop":
        return NoopGeocoder()
    if provider in ("google", "auto") and settings.google_maps_api_key:
        return GoogleNycGeocoder(settings.google_maps_api_key)
    return NoopGeocoder()
