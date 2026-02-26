"""
normalization/tests/test_schema.py

Tests for canonical schema helpers: make_canonical_id, PropertyRecord computed fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from normalization.schema import (
    DataSource,
    PropertyRecord,
    PropertyType,
    RentalComp,
    RentalStrategy,
    make_canonical_id,
    ZIP_WHITELIST,
)


# ── make_canonical_id ─────────────────────────────────────────────────────────

class TestMakeCanonicalId:
    def test_deterministic(self) -> None:
        cid1 = make_canonical_id("123 Main Street", "32204")
        cid2 = make_canonical_id("123 Main Street", "32204")
        assert cid1 == cid2

    def test_abbreviates_street_suffix(self) -> None:
        # "Street" → "st" means these two produce the same ID
        cid1 = make_canonical_id("123 Main Street", "32204")
        cid2 = make_canonical_id("123 main st", "32204")
        assert cid1 == cid2

    def test_case_insensitive(self) -> None:
        assert make_canonical_id("456 Oak Ave", "32205") == make_canonical_id("456 OAK AVE", "32205")

    def test_strips_unit_numbers(self) -> None:
        assert make_canonical_id("789 Elm Blvd Apt 2", "32206") == make_canonical_id("789 Elm Blvd", "32206")

    def test_different_zips_differ(self) -> None:
        cid1 = make_canonical_id("100 River Rd", "32204")
        cid2 = make_canonical_id("100 River Rd", "32205")
        assert cid1 != cid2

    def test_returns_12_chars(self) -> None:
        cid = make_canonical_id("1 Test Ln", "32207")
        assert len(cid) == 12


# ── PropertyRecord ────────────────────────────────────────────────────────────

def _make_record(**kwargs) -> PropertyRecord:
    defaults = dict(
        canonical_id = "abc123def456",
        source       = DataSource.ZILLOW_SALE,
        source_id    = "zpid_123",
        scraped_at   = datetime(2026, 2, 26, tzinfo=timezone.utc),
        address      = "100 Oak Street",
        zip_code     = "32204",
        price        = 350_000.0,
    )
    defaults.update(kwargs)
    return PropertyRecord(**defaults)


class TestPropertyRecordComputed:
    def test_price_per_sqft(self) -> None:
        rec = _make_record(price=300_000.0, sqft=1_500.0)
        assert rec.price_per_sqft == pytest.approx(200.0, rel=1e-6)

    def test_price_per_sqft_none_when_no_sqft(self) -> None:
        rec = _make_record(sqft=None)
        assert rec.price_per_sqft is None

    def test_is_multifamily_duplex(self) -> None:
        rec = _make_record(property_type=PropertyType.DUPLEX, num_units=2)
        assert rec.is_multifamily is True

    def test_is_multifamily_sfr(self) -> None:
        rec = _make_record(property_type=PropertyType.SFR, num_units=1)
        assert rec.is_multifamily is False

    def test_is_multifamily_sfr_adu(self) -> None:
        rec = _make_record(property_type=PropertyType.SFR_ADU, num_units=2)
        assert rec.is_multifamily is True

    def test_in_target_zip_true(self) -> None:
        rec = _make_record(zip_code="32204")
        assert rec.in_target_zip is True

    def test_in_target_zip_false(self) -> None:
        rec = _make_record(zip_code="32099")
        assert rec.in_target_zip is False


class TestZipWhitelist:
    def test_all_target_zips_present(self) -> None:
        expected = {"32204", "32205", "32206", "32207", "32210", "32211", "32217"}
        assert expected <= ZIP_WHITELIST
