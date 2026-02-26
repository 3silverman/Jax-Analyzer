"""
normalization/normalizer.py

Field-mapping layer between raw scraper payloads and canonical schema.

Each normalizer function accepts a raw dict (from Apify actor or Rentcast API)
and returns a fully-typed PropertyRecord or RentalComp.

No external API calls are made here — pure data transformation only.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from normalization.confidence import annotate
from normalization.schema import (
    DataSource,
    PropertyRecord,
    PropertyType,
    RentalComp,
    RentalStrategy,
    make_canonical_id,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _to_float(val: Any) -> float | None:
    """Coerce value to float, return None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val: Any) -> int | None:
    """Coerce value to int, return None on failure."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _clean_price(val: Any) -> float | None:
    """Strip $ and commas then parse as float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r"[$,]", "", str(val))
    return _to_float(cleaned)


def _infer_property_type(raw: dict[str, Any]) -> PropertyType:
    """
    Infer PropertyType from Zillow homeType or description heuristics.
    Returns UNKNOWN when type cannot be determined confidently.
    """
    home_type = str(raw.get("homeType") or raw.get("home_type") or "").lower()
    desc = str(raw.get("description") or "").lower()
    beds = _to_int(raw.get("bedrooms") or raw.get("beds"))

    type_map = {
        "multi_family":  PropertyType.DUPLEX,   # default; refined below
        "multifamily":   PropertyType.DUPLEX,
        "duplex":        PropertyType.DUPLEX,
        "triplex":       PropertyType.TRIPLEX,
        "quadplex":      PropertyType.QUADPLEX,
        "single_family": PropertyType.SFR,
        "singlefamily":  PropertyType.SFR,
        "sfr":           PropertyType.SFR,
        "condo":         PropertyType.CONDO,
        "condominium":   PropertyType.CONDO,
    }
    for key, ptype in type_map.items():
        if key in home_type:
            return ptype

    # Description heuristics
    if "quadplex" in desc or "quad-plex" in desc or "4-plex" in desc:
        return PropertyType.QUADPLEX
    if "triplex" in desc or "tri-plex" in desc or "3-plex" in desc:
        return PropertyType.TRIPLEX
    if "duplex" in desc or "2-plex" in desc:
        return PropertyType.DUPLEX
    if "adu" in desc or "accessory dwelling" in desc or "in-law" in desc:
        return PropertyType.SFR_ADU

    # Multifamily with beds hint
    if "multi_family" in home_type or "multifamily" in home_type:
        if beds is not None:
            if beds >= 7:
                return PropertyType.QUADPLEX
            if beds >= 5:
                return PropertyType.TRIPLEX
            return PropertyType.DUPLEX

    return PropertyType.UNKNOWN


def _infer_units(property_type: PropertyType, raw: dict[str, Any]) -> int:
    """Return unit count based on type; fall back to raw field if present."""
    from normalization.schema import UNITS_BY_TYPE
    raw_units = _to_int(raw.get("units") or raw.get("numUnits") or raw.get("num_units"))
    if raw_units is not None and raw_units >= 1:
        return raw_units
    return UNITS_BY_TYPE.get(property_type, 1)


def _parse_dom(raw: dict[str, Any]) -> int | None:
    """Extract days-on-market from various field names."""
    for key in ("daysOnMarket", "days_on_market", "dom", "daysOnZillow"):
        val = _to_int(raw.get(key))
        if val is not None:
            return val
    return None


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ──────────────────────────────────────────────────────────────────────────────
# Zillow for-sale listings  (Apify actor output)
# ──────────────────────────────────────────────────────────────────────────────

def normalize_zillow_listing(raw: dict[str, Any]) -> PropertyRecord:
    """
    Normalize a single Zillow for-sale listing from the Apify actor payload.

    Apify actor: "lukaskrivka/zillow-scraper"
    Key fields: zpid, price, streetAddress, zipcode, bedrooms, bathrooms,
                livingArea, yearBuilt, homeType, daysOnMarket, latitude, longitude
    """
    address = str(raw.get("streetAddress") or raw.get("address") or "")
    zip_code = str(raw.get("zipcode") or raw.get("zip_code") or "").strip()[:5]

    property_type = _infer_property_type(raw)

    record = PropertyRecord(
        canonical_id = make_canonical_id(address, zip_code),
        source       = DataSource.ZILLOW_SALE,
        source_id    = str(raw.get("zpid") or raw.get("id") or ""),
        scraped_at   = _now_utc(),
        address      = address,
        zip_code     = zip_code,
        lat          = _to_float(raw.get("latitude") or raw.get("lat")),
        lon          = _to_float(raw.get("longitude") or raw.get("lng") or raw.get("lon")),
        price        = _clean_price(raw.get("price")) or 0.0,
        beds         = _to_int(raw.get("bedrooms") or raw.get("beds")),
        baths        = _to_float(raw.get("bathrooms") or raw.get("baths")),
        sqft         = _to_float(raw.get("livingArea") or raw.get("sqft")),
        year_built   = _to_int(raw.get("yearBuilt") or raw.get("year_built")),
        property_type = property_type,
        num_units    = _infer_units(property_type, raw),
        lot_size_sqft = _to_float(raw.get("lotAreaValue") or raw.get("lot_size_sqft")),
        days_on_market = _parse_dom(raw),
        raw          = raw,
    )
    return annotate(record)


# ──────────────────────────────────────────────────────────────────────────────
# Zillow rental comps  (Apify actor output)
# ──────────────────────────────────────────────────────────────────────────────

def normalize_zillow_rental(raw: dict[str, Any]) -> RentalComp:
    """
    Normalize a Zillow rental listing from the Apify actor payload.
    These become LTR comps used to validate rent assumptions.

    Key fields: zpid, price (monthly rent), streetAddress, zipcode, bedrooms,
                bathrooms, livingArea, latitude, longitude
    """
    address  = str(raw.get("streetAddress") or raw.get("address") or "")
    zip_code = str(raw.get("zipcode") or raw.get("zip_code") or "").strip()[:5]

    return RentalComp(
        canonical_id     = make_canonical_id(address, zip_code),
        source           = DataSource.ZILLOW_RENTAL,
        source_id        = str(raw.get("zpid") or raw.get("id") or ""),
        scraped_at       = _now_utc(),
        address          = address,
        zip_code         = zip_code,
        lat              = _to_float(raw.get("latitude") or raw.get("lat")),
        lon              = _to_float(raw.get("longitude") or raw.get("lng") or raw.get("lon")),
        beds             = _to_int(raw.get("bedrooms") or raw.get("beds")),
        baths            = _to_float(raw.get("bathrooms") or raw.get("baths")),
        sqft             = _to_float(raw.get("livingArea") or raw.get("sqft")),
        rental_strategy  = RentalStrategy.LTR,
        monthly_rate     = _clean_price(raw.get("price") or raw.get("rent")),
        utilities_included = bool(raw.get("utilitiesIncluded") or False),
        raw              = raw,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Furnished Finder  (Apify actor output — MTR comps)
# ──────────────────────────────────────────────────────────────────────────────

def normalize_furnished_finder(raw: dict[str, Any]) -> RentalComp:
    """
    Normalize a Furnished Finder listing from the Apify actor payload.
    These become MTR comps (corporate / travel-nurse furnished rentals).

    Key fields: id, price (monthly), address, zip, beds, baths, sqft,
                utilitiesIncluded, latitude, longitude
    """
    address  = str(raw.get("address") or raw.get("streetAddress") or "")
    zip_code = str(raw.get("zip") or raw.get("zipcode") or raw.get("zip_code") or "").strip()[:5]

    return RentalComp(
        canonical_id     = make_canonical_id(address, zip_code),
        source           = DataSource.FURNISHED_FINDER,
        source_id        = str(raw.get("id") or raw.get("listingId") or ""),
        scraped_at       = _now_utc(),
        address          = address,
        zip_code         = zip_code,
        lat              = _to_float(raw.get("latitude") or raw.get("lat")),
        lon              = _to_float(raw.get("longitude") or raw.get("lng") or raw.get("lon")),
        beds             = _to_int(raw.get("beds") or raw.get("bedrooms")),
        baths            = _to_float(raw.get("baths") or raw.get("bathrooms")),
        sqft             = _to_float(raw.get("sqft") or raw.get("livingArea")),
        rental_strategy  = RentalStrategy.MTR,
        monthly_rate     = _clean_price(raw.get("price") or raw.get("monthlyRate")),
        utilities_included = bool(raw.get("utilitiesIncluded") or raw.get("utilities_included") or False),
        raw              = raw,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Rentcast API  (property details + tax records)
# ──────────────────────────────────────────────────────────────────────────────

def normalize_rentcast_property(raw: dict[str, Any]) -> PropertyRecord:
    """
    Normalize a Rentcast property record.
    Rentcast supplements Zillow data with tax records, ownership history,
    and assessed value.

    Key fields: id, formattedAddress, zipCode, bedrooms, bathrooms, squareFootage,
                yearBuilt, propertyType, latitude, longitude, lastSalePrice, lastSaleDate
    """
    address  = str(raw.get("formattedAddress") or raw.get("address") or "")
    zip_code = str(raw.get("zipCode") or raw.get("zip_code") or "").strip()[:5]

    raw_type = str(raw.get("propertyType") or "").lower().replace(" ", "_")
    type_map = {
        "multi_family":  PropertyType.DUPLEX,
        "multifamily":   PropertyType.DUPLEX,
        "single_family": PropertyType.SFR,
        "condo":         PropertyType.CONDO,
        "duplex":        PropertyType.DUPLEX,
        "triplex":       PropertyType.TRIPLEX,
        "quadruplex":    PropertyType.QUADPLEX,
    }
    property_type = type_map.get(raw_type, PropertyType.UNKNOWN)

    record = PropertyRecord(
        canonical_id  = make_canonical_id(address, zip_code),
        source        = DataSource.RENTCAST,
        source_id     = str(raw.get("id") or ""),
        scraped_at    = _now_utc(),
        address       = address,
        zip_code      = zip_code,
        lat           = _to_float(raw.get("latitude")),
        lon           = _to_float(raw.get("longitude")),
        price         = _clean_price(raw.get("lastSalePrice") or raw.get("price")) or 0.0,
        beds          = _to_int(raw.get("bedrooms")),
        baths         = _to_float(raw.get("bathrooms")),
        sqft          = _to_float(raw.get("squareFootage")),
        year_built    = _to_int(raw.get("yearBuilt")),
        property_type = property_type,
        num_units     = _infer_units(property_type, raw),
        raw           = raw,
    )
    return annotate(record)


def normalize_rentcast_comp(raw: dict[str, Any], subject_zip: str = "") -> RentalComp:
    """
    Normalize a single Rentcast rental-listing dict into a RentalComp.

    Rentcast /v1/listings/rental/long-term fields:
        id, formattedAddress, addressLine1, city, state, zipCode,
        latitude, longitude, bedrooms, bathrooms, squareFootage,
        price  (monthly rent), utilitiesIncluded
    """
    address  = str(raw.get("formattedAddress") or raw.get("addressLine1") or "").strip()
    zip_code = str(raw.get("zipCode") or subject_zip or "").strip()[:5]

    return RentalComp(
        canonical_id       = make_canonical_id(address, zip_code),
        source             = DataSource.RENTCAST,
        source_id          = str(raw.get("id") or ""),
        scraped_at         = _now_utc(),
        address            = address,
        zip_code           = zip_code,
        lat                = _to_float(raw.get("latitude")),
        lon                = _to_float(raw.get("longitude")),
        beds               = _to_int(raw.get("bedrooms")),
        baths              = _to_float(raw.get("bathrooms")),
        sqft               = _to_float(raw.get("squareFootage")),
        rental_strategy    = RentalStrategy.LTR,
        monthly_rate       = _to_float(raw.get("price")),
        adr                = None,
        utilities_included = bool(raw.get("utilitiesIncluded", False)),
        raw                = raw,
    )
