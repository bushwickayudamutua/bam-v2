"""Address geocoding (parity with the /clean-record address pipeline).

Production's ``clean-record`` automation resolves an address to a cleaned
address, an accuracy grade, an NYC Building Identification Number (BIN), and
an Open Location Code (plus_code) via Google Maps + NYC Planning Labs. This
mirrors that as a pluggable ``Geocoder``:

- ``GoogleNycGeocoder`` — the real pipeline; active when a Google Maps key
  is configured. Google geocodes the address (accuracy from the result's
  ``location_type`` and component granularity, plus_code from the response),
  and NYC Planning Labs' GeoSearch supplies the BIN.
- ``NoopGeocoder`` — a safe passthrough that just formats the address; used
  when no key is set (tests, local dev), so intake still works.

The SMS layer's ``get_provider`` pattern is followed: ``get_geocoder``
returns the right one from settings.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from bam.config import Settings


@dataclass
class GeocodeResult:
    cleaned_address: str | None
    address_accuracy: str  # Apartment / Building / Address Outside NY / No result
    bin: str | None  # NYC Building Identification Number
    plus_code: str | None  # Open Location Code


class Geocoder(Protocol):
    def geocode(
        self, street_address: str | None, city_state: str | None, zip_code: str | None
    ) -> GeocodeResult: ...


def _joined(street: str | None, city_state: str | None, zip_code: str | None) -> str:
    return " ".join(p for p in (street, city_state, zip_code) if p).strip()


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
    """Real geocoder: Google Maps for the cleaned address + accuracy +
    plus_code, NYC Planning Labs GeoSearch for the BIN."""

    GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"

    def __init__(self, api_key: str, opener=urllib.request.urlopen) -> None:
        if not api_key:
            raise ValueError("GoogleNycGeocoder needs a Google Maps API key")
        self._api_key = api_key
        self._opener = opener

    def _get_json(self, url: str) -> dict:
        with self._opener(url, timeout=10) as resp:  # noqa: S310 (trusted URLs)
            return json.loads(resp.read().decode("utf-8"))

    def geocode(self, street_address, city_state, zip_code) -> GeocodeResult:
        joined = _joined(street_address, city_state, zip_code)
        if not joined:
            return GeocodeResult(None, "No result", None, None)
        try:
            params = urllib.parse.urlencode({"address": joined, "key": self._api_key})
            data = self._get_json(f"{self.GOOGLE_URL}?{params}")
        except Exception:  # noqa: BLE001 — geocoding must never break intake
            return GeocodeResult(joined, "No result", None, None)

        results = data.get("results") or []
        if not results:
            return GeocodeResult(joined, "No result", None, None)
        top = results[0]
        cleaned = top.get("formatted_address") or joined
        plus_code = (data.get("plus_code") or top.get("plus_code") or {}).get(
            "global_code"
        )
        accuracy = self._accuracy(top)
        bin_number = self._nyc_bin(joined) if accuracy != "Address Outside NY" else None
        return GeocodeResult(cleaned, accuracy, bin_number, plus_code)

    @staticmethod
    def _accuracy(result: dict) -> str:
        types = result.get("types") or []
        components = result.get("address_components") or []
        state = next(
            (
                c
                for c in components
                if "administrative_area_level_1" in (c.get("types") or [])
            ),
            None,
        )
        if state and state.get("short_name") != "NY":
            return "Address Outside NY"
        if "subpremise" in types:
            return "Apartment"
        location_type = (result.get("geometry") or {}).get("location_type")
        if location_type == "ROOFTOP" or "premise" in types or "street_address" in types:
            return "Building"
        return "No result"

    def _nyc_bin(self, address: str) -> str | None:
        try:
            params = urllib.parse.urlencode({"text": address, "size": 1})
            data = self._get_json(f"{self.GEOSEARCH_URL}?{params}")
        except Exception:  # noqa: BLE001
            return None
        features = data.get("features") or []
        if not features:
            return None
        addendum = (features[0].get("properties") or {}).get("addendum") or {}
        pad = addendum.get("pad") or {}
        bin_number = pad.get("bin")
        return str(bin_number) if bin_number else None


def get_geocoder(settings: Settings) -> Geocoder:
    provider = settings.geocoder_provider
    if provider == "noop":
        return NoopGeocoder()
    if provider in ("google", "auto") and settings.google_maps_api_key:
        return GoogleNycGeocoder(settings.google_maps_api_key)
    return NoopGeocoder()
