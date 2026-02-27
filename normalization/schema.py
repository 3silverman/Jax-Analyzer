"""
normalization/schema.py

Canonical data schemas for the Jacksonville real-estate analyzer.
All modules downstream of ingestion/ work exclusively with these types.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────

class DataSource(str, Enum):
    ZILLOW_SALE       = "zillow_sale"
    ZILLOW_RENTAL     = "zillow_rental"
    FURNISHED_FINDER  = "furnished_finder"
    RENTCAST          = "rentcast"


class PropertyType(str, Enum):
    DUPLEX    = "duplex"
    TRIPLEX   = "triplex"
    QUADPLEX  = "quadplex"
    SFR_ADU   = "sfr_adu"    # SFR with accessory dwelling unit
    SFR       = "sfr"
    CONDO     = "condo"
    UNKNOWN   = "unknown"


class RentalStrategy(str, Enum):
    LTR = "ltr"
    MTR = "mtr"
    STR = "str"


# Units per property type — used by underwriting module
UNITS_BY_TYPE: dict[PropertyType, int] = {
    PropertyType.DUPLEX:   2,
    PropertyType.TRIPLEX:  3,
    PropertyType.QUADPLEX: 4,
    PropertyType.SFR_ADU:  2,
    PropertyType.SFR:      1,
    PropertyType.CONDO:    1,
    PropertyType.UNKNOWN:  1,
}

# Target zip codes for Jacksonville VA house-hack strategy
ZIP_WHITELIST: frozenset[str] = frozenset(
    ["32204", "32205", "32206", "32207"]
)


# ──────────────────────────────────────────────────────────────────────────────
# Canonical ID helpers
# ──────────────────────────────────────────────────────────────────────────────

_STREET_ABBREV: list[tuple[str, str]] = [
    (r"\bstreet\b", "st"),
    (r"\bavenue\b", "ave"),
    (r"\bboulevard\b", "blvd"),
    (r"\bdrive\b", "dr"),
    (r"\bcourt\b", "ct"),
    (r"\bplace\b", "pl"),
    (r"\broad\b", "rd"),
    (r"\blane\b", "ln"),
    (r"\bway\b", "wy"),
    (r"\bcircle\b", "cir"),
    (r"\bnorth\b", "n"),
    (r"\bsouth\b", "s"),
    (r"\beast\b", "e"),
    (r"\bwest\b", "w"),
]


def _normalize_street(raw: str) -> str:
    """Lowercase, strip unit numbers and punctuation, abbreviate common words."""
    s = raw.lower().strip()
    # Remove unit designators
    s = re.sub(r"\b(apt|unit|#|suite|ste|floor|fl)\s*[\w-]+\b", "", s)
    for pattern, repl in _STREET_ABBREV:
        s = re.sub(pattern, repl, s)
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s)).strip()


def make_canonical_id(street_address: str, zip_code: str) -> str:
    """
    Deterministic 12-char hex ID for a property.
    Same physical address always produces the same canonical_id regardless of source.
    """
    key = _normalize_street(street_address) + zip_code.strip()
    return hashlib.sha1(key.encode()).hexdigest()[:12]


# ──────────────────────────────────────────────────────────────────────────────
# Canonical models
# ──────────────────────────────────────────────────────────────────────────────

class PropertyRecord(BaseModel):
    """
    Canonical for-sale listing record.
    Produced by normalization/ and consumed by all downstream modules.
    """

    # Identity
    canonical_id: str          # deterministic hash of normalized address
    source: DataSource
    source_id: str             # MLS / zpid / rentcast ID
    scraped_at: datetime

    # Location
    address: str               # full street address
    city: str = "Jacksonville"
    state: str = "FL"
    zip_code: str
    lat: float | None = None
    lon: float | None = None

    # Property
    price: float
    beds: int | None = None
    baths: float | None = None
    sqft: float | None = None
    year_built: int | None = None
    property_type: PropertyType = PropertyType.UNKNOWN
    num_units: int = 1
    lot_size_sqft: float | None = None

    # Listing
    list_date: date | None = None
    days_on_market: int | None = None

    # Data quality (set by confidence.py)
    confidence_score: float = 0.0       # 0.0–1.0; scaled to 0–30 by scoring/
    missing_fields: list[str] = Field(default_factory=list)

    # Original source payload
    raw: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[misc]
    @property
    def price_per_sqft(self) -> float | None:
        if self.price and self.sqft and self.sqft > 0:
            return round(self.price / self.sqft, 2)
        return None

    @computed_field  # type: ignore[misc]
    @property
    def is_multifamily(self) -> bool:
        return self.num_units > 1 or self.property_type in (
            PropertyType.DUPLEX,
            PropertyType.TRIPLEX,
            PropertyType.QUADPLEX,
            PropertyType.SFR_ADU,
        )

    @computed_field  # type: ignore[misc]
    @property
    def in_target_zip(self) -> bool:
        return self.zip_code in ZIP_WHITELIST


class RentalComp(BaseModel):
    """
    Canonical rental comparable — LTR, MTR, or STR.
    Used by underwriting/ to validate rent assumptions.
    """

    canonical_id: str
    source: DataSource
    source_id: str
    scraped_at: datetime

    address: str
    zip_code: str
    lat: float | None = None
    lon: float | None = None

    beds: int | None = None
    baths: float | None = None
    sqft: float | None = None

    rental_strategy: RentalStrategy
    monthly_rate: float | None = None   # LTR / MTR
    adr: float | None = None            # STR average daily rate
    utilities_included: bool = False    # MTR furnished listings

    raw: dict[str, Any] = Field(default_factory=dict)
