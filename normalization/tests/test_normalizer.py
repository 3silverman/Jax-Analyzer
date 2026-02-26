"""
normalization/tests/test_normalizer.py

Tests for the field-mapping normalizer functions.
"""

from __future__ import annotations

import pytest

from normalization.normalizer import (
    normalize_furnished_finder,
    normalize_rentcast_comp,
    normalize_rentcast_property,
    normalize_zillow_listing,
    normalize_zillow_rental,
)
from normalization.schema import DataSource, PropertyType, RentalStrategy


# ── Zillow for-sale listing ───────────────────────────────────────────────────

_ZILLOW_LISTING_RAW = {
    "zpid": "12345678",
    "streetAddress": "210 Oak Avenue",
    "zipcode": "32204",
    "price": 375000,
    "bedrooms": 4,
    "bathrooms": 2.0,
    "livingArea": 1800,
    "yearBuilt": 1962,
    "homeType": "MULTI_FAMILY",
    "latitude": 30.3204,
    "longitude": -81.6609,
    "daysOnMarket": 21,
    "lotAreaValue": 5400,
}


class TestNormalizeZillowListing:
    def setup_method(self) -> None:
        self.rec = normalize_zillow_listing(_ZILLOW_LISTING_RAW)

    def test_source(self) -> None:
        assert self.rec.source == DataSource.ZILLOW_SALE

    def test_source_id(self) -> None:
        assert self.rec.source_id == "12345678"

    def test_address(self) -> None:
        assert self.rec.address == "210 Oak Avenue"

    def test_zip_code(self) -> None:
        assert self.rec.zip_code == "32204"

    def test_price(self) -> None:
        assert self.rec.price == pytest.approx(375_000.0)

    def test_beds(self) -> None:
        assert self.rec.beds == 4

    def test_baths(self) -> None:
        assert self.rec.baths == pytest.approx(2.0)

    def test_sqft(self) -> None:
        assert self.rec.sqft == pytest.approx(1800.0)

    def test_year_built(self) -> None:
        assert self.rec.year_built == 1962

    def test_lat_lon(self) -> None:
        assert self.rec.lat == pytest.approx(30.3204)
        assert self.rec.lon == pytest.approx(-81.6609)

    def test_property_type_multifamily(self) -> None:
        assert self.rec.property_type == PropertyType.DUPLEX

    def test_num_units_inferred(self) -> None:
        assert self.rec.num_units == 2

    def test_days_on_market(self) -> None:
        assert self.rec.days_on_market == 21

    def test_canonical_id_is_12_chars(self) -> None:
        assert len(self.rec.canonical_id) == 12

    def test_confidence_score_positive(self) -> None:
        assert self.rec.confidence_score > 0.0

    def test_price_string_cleaned(self) -> None:
        raw = dict(_ZILLOW_LISTING_RAW, price="$375,000")
        rec = normalize_zillow_listing(raw)
        assert rec.price == pytest.approx(375_000.0)


class TestNormalizeZillowListingEdgeCases:
    def test_minimal_raw_no_error(self) -> None:
        minimal = {"zpid": "99", "streetAddress": "1 Main St", "zipcode": "32205", "price": 100_000}
        rec = normalize_zillow_listing(minimal)
        assert rec.price == pytest.approx(100_000.0)
        assert rec.beds is None

    def test_description_heuristic_duplex(self) -> None:
        raw = {
            "zpid": "1", "streetAddress": "5 Elm St", "zipcode": "32206",
            "price": 200_000, "homeType": "MULTI_FAMILY",
            "description": "Classic duplex with two units",
        }
        rec = normalize_zillow_listing(raw)
        assert rec.property_type == PropertyType.DUPLEX

    def test_description_heuristic_triplex(self) -> None:
        raw = {
            "zpid": "2", "streetAddress": "7 Pine Rd", "zipcode": "32207",
            "price": 300_000, "homeType": "",
            "description": "Rare triplex in Riverside",
        }
        rec = normalize_zillow_listing(raw)
        assert rec.property_type == PropertyType.TRIPLEX


# ── Zillow rental comp ────────────────────────────────────────────────────────

_ZILLOW_RENTAL_RAW = {
    "zpid": "88887777",
    "streetAddress": "350 Riverside Ave",
    "zipcode": "32204",
    "price": 1_800,
    "bedrooms": 3,
    "bathrooms": 1.5,
    "livingArea": 1_100,
    "latitude": 30.315,
    "longitude": -81.668,
}


class TestNormalizeZillowRental:
    def setup_method(self) -> None:
        self.comp = normalize_zillow_rental(_ZILLOW_RENTAL_RAW)

    def test_source(self) -> None:
        assert self.comp.source == DataSource.ZILLOW_RENTAL

    def test_strategy(self) -> None:
        assert self.comp.rental_strategy == RentalStrategy.LTR

    def test_monthly_rate(self) -> None:
        assert self.comp.monthly_rate == pytest.approx(1_800.0)

    def test_beds(self) -> None:
        assert self.comp.beds == 3

    def test_zip_code(self) -> None:
        assert self.comp.zip_code == "32204"


# ── Furnished Finder ──────────────────────────────────────────────────────────

_FF_RAW = {
    "id": "ff_abcd",
    "address": "420 Magnolia St",
    "zip": "32204",
    "beds": 2,
    "baths": 1,
    "sqft": 900,
    "price": 2_600,
    "utilitiesIncluded": True,
    "latitude": 30.320,
    "longitude": -81.660,
}


class TestNormalizeFurnishedFinder:
    def setup_method(self) -> None:
        self.comp = normalize_furnished_finder(_FF_RAW)

    def test_source(self) -> None:
        assert self.comp.source == DataSource.FURNISHED_FINDER

    def test_strategy(self) -> None:
        assert self.comp.rental_strategy == RentalStrategy.MTR

    def test_monthly_rate(self) -> None:
        assert self.comp.monthly_rate == pytest.approx(2_600.0)

    def test_utilities_included(self) -> None:
        assert self.comp.utilities_included is True

    def test_zip_code(self) -> None:
        assert self.comp.zip_code == "32204"


# ── Rentcast property ─────────────────────────────────────────────────────────

_RENTCAST_RAW = {
    "id": "rc_xyzzy",
    "formattedAddress": "789 Avondale Ave, Jacksonville, FL 32205",
    "zipCode": "32205",
    "bedrooms": 6,
    "bathrooms": 3,
    "squareFootage": 2_400,
    "yearBuilt": 1948,
    "propertyType": "Multi Family",
    "latitude": 30.308,
    "longitude": -81.673,
    "lastSalePrice": 420_000,
}


class TestNormalizeRentcastProperty:
    def setup_method(self) -> None:
        self.rec = normalize_rentcast_property(_RENTCAST_RAW)

    def test_source(self) -> None:
        assert self.rec.source == DataSource.RENTCAST

    def test_source_id(self) -> None:
        assert self.rec.source_id == "rc_xyzzy"

    def test_price_from_last_sale(self) -> None:
        assert self.rec.price == pytest.approx(420_000.0)

    def test_property_type_multi_family(self) -> None:
        assert self.rec.property_type == PropertyType.DUPLEX

    def test_zip_code(self) -> None:
        assert self.rec.zip_code == "32205"

    def test_year_built(self) -> None:
        assert self.rec.year_built == 1948


# ── normalize_rentcast_comp ────────────────────────────────────────────────────

_RENTCAST_COMP_RAW = {
    "id":               "rc_comp_001",
    "formattedAddress": "321 Riverside Ave, Jacksonville, FL 32204",
    "zipCode":          "32204",
    "latitude":         30.3170,
    "longitude":        -81.6630,
    "bedrooms":         2,
    "bathrooms":        1.0,
    "squareFootage":    950,
    "price":            1450.0,
    "utilitiesIncluded": False,
}


class TestNormalizeRentcastComp:
    def setup_method(self) -> None:
        self.comp = normalize_rentcast_comp(_RENTCAST_COMP_RAW)

    def test_source_is_rentcast(self) -> None:
        assert self.comp.source == DataSource.RENTCAST

    def test_source_id(self) -> None:
        assert self.comp.source_id == "rc_comp_001"

    def test_address(self) -> None:
        assert "321 Riverside" in self.comp.address

    def test_zip_code(self) -> None:
        assert self.comp.zip_code == "32204"

    def test_lat_lon(self) -> None:
        assert self.comp.lat == pytest.approx(30.3170)
        assert self.comp.lon == pytest.approx(-81.6630)

    def test_beds_baths_sqft(self) -> None:
        assert self.comp.beds == 2
        assert self.comp.baths == pytest.approx(1.0)
        assert self.comp.sqft == pytest.approx(950.0)

    def test_monthly_rate(self) -> None:
        assert self.comp.monthly_rate == pytest.approx(1450.0)

    def test_rental_strategy_ltr(self) -> None:
        assert self.comp.rental_strategy == RentalStrategy.LTR

    def test_adr_is_none(self) -> None:
        assert self.comp.adr is None

    def test_utilities_included_false(self) -> None:
        assert self.comp.utilities_included is False

    def test_canonical_id_deterministic(self) -> None:
        other = normalize_rentcast_comp(_RENTCAST_COMP_RAW)
        assert self.comp.canonical_id == other.canonical_id

    def test_fallback_zip_from_subject(self) -> None:
        raw = {**_RENTCAST_COMP_RAW, "zipCode": ""}
        comp = normalize_rentcast_comp(raw, subject_zip="32205")
        assert comp.zip_code == "32205"

    def test_missing_price_returns_none_rate(self) -> None:
        raw = {k: v for k, v in _RENTCAST_COMP_RAW.items() if k != "price"}
        comp = normalize_rentcast_comp(raw)
        assert comp.monthly_rate is None

    def test_uses_addressLine1_fallback(self) -> None:
        raw = {**_RENTCAST_COMP_RAW}
        del raw["formattedAddress"]
        raw["addressLine1"] = "999 Elm St"
        comp = normalize_rentcast_comp(raw)
        assert "999 Elm" in comp.address
