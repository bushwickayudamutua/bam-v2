"""Real geocoding backend (bam/services/geocoding.py + olc.py).

The Google/GeoSearch chain is driven by an injected ``fetch`` — no network.
"""

from __future__ import annotations

from bam.config import Settings
from bam.services import olc
from bam.services.geocoding import (
    DEFAULT_BIN_RESPONSES,
    GoogleNycGeocoder,
    NoopGeocoder,
    _fix_address,
    _fix_zip_code,
    get_geocoder,
)


def test_olc_test_vector_and_guards():
    assert olc.encode(40.7128, -74.0060) == "87G7PX7V+"  # 8 chars + '+'
    assert olc.encode(None, 45.0) is None
    assert olc.encode(40.0, None) is None


def test_fix_address_and_zip():
    assert _fix_address("123 Main St APTO 2 #") == "123 MAIN ST APT 2"
    assert _fix_address("5 Ave PISO 3") == "5 AVE FLOOR 3"
    assert _fix_zip_code("112007") == "11207"


class FakeFetch:
    """Routes canned responses by URL substring; records call count."""

    def __init__(self, places=None, validation=None, geocode=None, geosearch=None):
        self.responses = {
            "place/autocomplete": places or {"predictions": []},
            "validateAddress": validation or {},
            "maps/api/geocode": geocode or {"results": []},
            "geosearch": geosearch or {"features": []},
        }
        self.calls = 0

    def __call__(self, url, *, method="GET", body=None):
        self.calls += 1
        for key, resp in self.responses.items():
            if key in url:
                return resp
        return {}


def _geo(fetch):
    return GoogleNycGeocoder("test-key", fetch=fetch)


def test_full_chain_apartment_with_bin_and_plus_code():
    fetch = FakeFetch(
        places={"predictions": [{"description": "123 Main St APT # 1, Brooklyn, NY 11201", "types": ["subpremise"]}]},
        validation={"result": {"uspsData": {"standardizedAddress": {"firstAddressLine": "123 MAIN ST # 1", "cityStateZipAddressLine": "BROOKLYN, NY 11201"}}}},
        geocode={"results": [{"geometry": {"location": {"lat": 40.6782, "lng": -73.9442}}}]},
        geosearch={"features": [{"properties": {"addendum": {"pad": {"bin": "3000001"}}}}]},
    )
    res = _geo(fetch).geocode("123 Main St", "Brooklyn, NY", "11201")
    assert res.address_accuracy == "Apartment"
    assert res.cleaned_address == "123 MAIN ST APT 1 BROOKLYN, NY 11201"  # ' # ' -> ' APT '
    assert res.bin == "3000001"
    assert res.plus_code == olc.encode(40.6782, -73.9442)


def test_no_places_falls_back_to_validation_verdict_building():
    fetch = FakeFetch(
        places={"predictions": []},
        validation={"result": {"verdict": {"validationGranularity": "PREMISE"}, "address": {"formattedAddress": "45 Oak Ave, Brooklyn, NY"}}},
        geocode={"results": [{"geometry": {"location": {"lat": 40.6, "lng": -73.9}}}]},
        geosearch={"features": []},
    )
    res = _geo(fetch).geocode("45 Oak Ave", "Brooklyn, NY", "11221")
    assert res.address_accuracy == "Building"
    assert res.cleaned_address == "45 OAK AVE, BROOKLYN, NY"
    assert res.bin is None  # no GeoSearch feature


def test_placeholder_bin_is_discarded():
    fetch = FakeFetch(
        places={"predictions": [{"description": "1 Somewhere", "types": ["premise"]}]},
        geocode={"results": [{"geometry": {"location": {"lat": 40.6, "lng": -73.9}}}]},
        geosearch={"features": [{"properties": {"addendum": {"pad": {"bin": DEFAULT_BIN_RESPONSES[0]}}}}]},
    )
    res = _geo(fetch).geocode("1 Somewhere", "Brooklyn, NY", "11207")
    assert res.bin is None  # 3000000 placeholder dropped
    assert res.plus_code is not None


def test_empty_address_makes_no_calls():
    fetch = FakeFetch()
    res = _geo(fetch).geocode("", "Brooklyn, NY", "11221")
    assert res.address_accuracy == "No result"
    assert fetch.calls == 0  # no Google/GeoSearch calls for an empty address


def test_get_geocoder_gating():
    assert isinstance(get_geocoder(Settings(google_maps_api_key="")), NoopGeocoder)
    assert isinstance(
        get_geocoder(Settings(google_maps_api_key="k", geocoder_provider="auto")),
        GoogleNycGeocoder,
    )
    assert isinstance(
        get_geocoder(Settings(google_maps_api_key="k", geocoder_provider="noop")),
        NoopGeocoder,
    )


def test_noop_passthrough_unchanged():
    geo = NoopGeocoder().geocode("1 A St", "Brooklyn, NY", "11221")
    assert geo.cleaned_address == "1 A St, Brooklyn, NY, 11221"
    assert geo.bin is None
