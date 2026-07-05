"""Tests for bam.validation and bam.request_types.

Covers the V2 "/clean-record" role (phone/email normalization, spec 6.1 step
2/5), the trilingual request-type catalog with 14/30-day expiry windows (spec
sections 2 and 4), and the stable phone hash backing the privacy goal.
"""

import hashlib

import pytest

from bam.request_types import (
    ALL_TYPES,
    DEFAULT_EXPIRY_DAYS,
    EXTENDED_EXPIRY_DAYS,
    GOODS,
    SOCIAL_SERVICES,
    expiry_days_for,
    get_request_type,
    goods_keys,
    is_social_service,
    label_for,
    normalize_type,
    social_service_keys,
)
from bam.validation import hash_phone, validate_email_address, validate_phone


class TestNormalizeType:
    def test_accepts_canonical_keys(self) -> None:
        for rt in ALL_TYPES:
            assert normalize_type(rt.key) == rt.key

    def test_accepts_full_trilingual_label(self) -> None:
        for rt in ALL_TYPES:
            assert normalize_type(rt.label) == rt.key

    def test_accepts_every_language_segment(self) -> None:
        for rt in ALL_TYPES:
            for segment in rt.label.split(" / "):
                assert normalize_type(segment) == rt.key, segment

    def test_segments_are_case_insensitive(self) -> None:
        assert normalize_type("SOAP & SHOWER PRODUCTS") == "soap"
        assert normalize_type("ollas y sartenes") == "pots_pans"

    def test_segments_tolerate_surrounding_whitespace(self) -> None:
        assert normalize_type("  Jabón & Productos de baño  ") == "soap"
        assert normalize_type(" 住房 ") == "housing"

    def test_unknown_and_empty_values(self) -> None:
        assert normalize_type("helicopter") is None
        assert normalize_type("") is None

    def test_no_segment_collides_across_types(self) -> None:
        seen: dict[str, str] = {}
        for rt in ALL_TYPES:
            for segment in rt.label.split(" / "):
                key = segment.strip().lower()
                assert seen.setdefault(key, rt.key) == rt.key
        # Sanity: near-miss Spanish segments resolve to distinct types.
        assert normalize_type("Comida para mascotas") == "pet_food"
        assert normalize_type("Asistencia para mascotas") == "pet_assistance"


class TestExpiryWindows:
    def test_standard_types_get_14_days(self) -> None:
        assert expiry_days_for("soap") == DEFAULT_EXPIRY_DAYS == 14

    def test_pots_pans_gets_30_days(self) -> None:
        assert expiry_days_for("pots_pans") == EXTENDED_EXPIRY_DAYS == 30

    def test_pots_pans_is_the_only_extended_type(self) -> None:
        extended = [t.key for t in ALL_TYPES if t.expiry_days != DEFAULT_EXPIRY_DAYS]
        assert extended == ["pots_pans"]

    def test_unknown_type_falls_back_to_default(self) -> None:
        assert expiry_days_for("helicopter") == DEFAULT_EXPIRY_DAYS

    def test_social_services_all_use_default_window(self) -> None:
        for rt in SOCIAL_SERVICES:
            assert rt.expiry_days == DEFAULT_EXPIRY_DAYS


class TestCatalogHelpers:
    def test_get_request_type_and_label_for(self) -> None:
        rt = get_request_type("pots_pans")
        assert rt.key == "pots_pans"
        assert label_for("pots_pans") == rt.label
        assert label_for("helicopter") == "helicopter"  # passthrough for unknowns

    def test_is_social_service(self) -> None:
        assert is_social_service("housing")
        assert not is_social_service("soap")
        assert not is_social_service("helicopter")

    def test_key_lists_partition_the_catalog(self) -> None:
        assert goods_keys() == [t.key for t in GOODS]
        assert social_service_keys() == [t.key for t in SOCIAL_SERVICES]
        assert set(goods_keys()).isdisjoint(social_service_keys())


class TestValidatePhone:
    def test_us_number_normalizes_to_e164(self) -> None:
        for raw in ["(718) 555-0123", "718-555-0123", "+1 718 555 0123"]:
            result = validate_phone(raw)
            assert result.normalized == "+17185550123"
            assert result.valid
            assert not result.international

    def test_international_number_sets_flag(self) -> None:
        result = validate_phone("+44 20 7946 0958")
        assert result.normalized == "+442079460958"
        assert result.valid
        assert result.international

    def test_invalid_number_rejected(self) -> None:
        result = validate_phone("718-555")  # too short to be a US number
        assert not result.valid
        assert result.normalized is None
        assert not result.international

    @pytest.mark.parametrize("raw", ["not a phone", "123", None, "", "   "])
    def test_garbage_and_empty_input(self, raw: str | None) -> None:
        result = validate_phone(raw)
        assert result.normalized is None
        assert not result.valid
        assert not result.international


class TestValidateEmail:
    def test_valid_email_normalizes_domain(self) -> None:
        result = validate_email_address("Foo@EXAMPLE.com")
        assert result.normalized == "Foo@example.com"
        assert result.error is None

    def test_invalid_email_reports_error(self) -> None:
        result = validate_email_address("not-an-email")
        assert result.normalized is None
        assert result.error

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_empty_email_is_not_an_error(self, raw: str | None) -> None:
        result = validate_email_address(raw)
        assert result.normalized is None
        assert result.error is None


class TestHashPhone:
    def test_stable_across_calls(self) -> None:
        assert hash_phone("+17185550123") == hash_phone("+17185550123")

    def test_matches_sha256_hexdigest(self) -> None:
        e164 = "+17185550123"
        assert hash_phone(e164) == hashlib.sha256(e164.encode("utf-8")).hexdigest()
        assert len(hash_phone(e164)) == 64

    def test_distinct_numbers_hash_differently(self) -> None:
        assert hash_phone("+17185550123") != hash_phone("+17185550124")
