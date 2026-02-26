"""
ingestion/airbnb_comps.py

Airbnb STR comp engine using the Apify tri_angle/airbnb-scraper actor.

Fetches active Airbnb listings near a zip code + bedroom count combination,
applies 2σ outlier filtering, estimates occupancy from a review-count proxy,
and returns a StrCompResult with median ADR, occupancy, and revenue figures.

Cache layer: results are keyed on (zip_code, bedrooms, week_number) so the
same data is reused across all properties sharing the same zip+bed combination
within a given ISO calendar week.  The pipeline handles cache reads/writes
before/after calling get_str_comps().
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_ACTOR_ID = "tri_angle/airbnb-scraper"
_MAX_ITEMS = 30   # fetch extra so 2σ filter still leaves enough

_STR_VALIDATED_MIN   = 5   # comp_count threshold for str_validated=True
_HIGH_CONF_MIN_COMPS    = 10
_HIGH_CONF_MIN_REVIEWS  = 10
_HIGH_CONF_MIN_OCC      = 0.50
_MEDIUM_CONF_MIN_COMPS  = 5

_OCC_MIN     = 0.40   # floor for review-count proxy
_OCC_MAX     = 0.75   # ceiling
_OCC_DEFAULT = 0.60   # fallback when review count not present

_PLATFORM_FEE         = 0.15    # 15% Airbnb host fee
_CLEANING_PER_TURN    = 125.0   # $ per guest turnover
_TURNS_PER_MONTH      = 4.0     # typical short-stay churn


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class StrCompResult:
    comp_count:          int
    median_adr:          float | None
    estimated_occupancy: float | None
    gross_monthly:       float | None
    net_monthly:         float | None
    confidence:          str          # "HIGH" | "MEDIUM" | "LOW"
    str_validated:       bool         # True when comp_count >= _STR_VALIDATED_MIN
    comps:               list[dict] = field(default_factory=list)
    sample_addresses:    list[str]  = field(default_factory=list)


# ── Occupancy estimation ──────────────────────────────────────────────────────

def _estimate_occupancy_from_reviews(comp: dict) -> float | None:
    """
    Proxy occupancy from review count.

    Listings with ≥100 reviews → ~75% occupancy (well-established, high demand).
    Listings with ≤5 reviews   → ~40% (new or rarely booked).
    Linear interpolation in between.
    """
    reviews = (
        comp.get("reviews")
        or comp.get("numberOfReviews")
        or comp.get("reviewCount")
    )
    if reviews is None:
        return None
    try:
        r = float(reviews)
    except (TypeError, ValueError):
        return None

    if r >= 100:
        return _OCC_MAX
    if r <= 5:
        return _OCC_MIN
    # Linear interpolation: (5, 0.40) → (100, 0.75)
    slope = (_OCC_MAX - _OCC_MIN) / (100 - 5)
    return round(_OCC_MIN + slope * (r - 5), 4)


# ── ADR extraction ────────────────────────────────────────────────────────────

def _extract_adr(comp: dict) -> float | None:
    """Extract average daily rate from a raw Airbnb actor item."""
    for key in ("price", "pricePerNight", "basePrice", "weekdayPrice"):
        val = comp.get(key)
        if val is None:
            continue
        # Some actors return {"amount": 95, "currency": "USD"}
        if isinstance(val, dict):
            val = val.get("amount") or val.get("value")
        try:
            v = float(val)  # type: ignore[arg-type]
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return None


# ── Outlier filtering ─────────────────────────────────────────────────────────

def _filter_outliers(adrs: list[float]) -> list[float]:
    """Remove ADR values more than 2 standard deviations from the mean."""
    if len(adrs) < 3:
        return adrs
    mean  = statistics.mean(adrs)
    stdev = statistics.stdev(adrs)
    if stdev == 0:
        return adrs
    return [v for v in adrs if abs(v - mean) <= 2 * stdev]


# ── Confidence classification ─────────────────────────────────────────────────

def _classify_confidence(
    comp_count:          int,
    median_reviews:      float | None,
    estimated_occupancy: float | None,
) -> str:
    if (
        comp_count >= _HIGH_CONF_MIN_COMPS
        and (median_reviews or 0) >= _HIGH_CONF_MIN_REVIEWS
        and (estimated_occupancy or 0) >= _HIGH_CONF_MIN_OCC
    ):
        return "HIGH"
    if comp_count >= _MEDIUM_CONF_MIN_COMPS:
        return "MEDIUM"
    return "LOW"


# ── Revenue calculation ───────────────────────────────────────────────────────

def _calc_revenue(
    median_adr:       float,
    occupancy:        float,
    turns_per_month:  float = _TURNS_PER_MONTH,
) -> tuple[float, float]:
    """
    Returns (gross_monthly, net_monthly) for a single unit.

    gross = ADR × occupancy × 30.4
    net   = gross × (1 - platform_fee) - cleaning_per_month
    """
    gross              = median_adr * occupancy * 30.4
    cleaning_per_month = _CLEANING_PER_TURN * occupancy * turns_per_month
    net                = gross * (1 - _PLATFORM_FEE) - cleaning_per_month
    return round(gross, 2), round(net, 2)


# ── Actor runner ──────────────────────────────────────────────────────────────

def _run_actor(zip_code: str, bedrooms: int) -> list[dict]:
    """
    Call the tri_angle/airbnb-scraper Apify actor and return raw listing dicts.
    Returns empty list on any error — STR analysis uses default assumptions.
    """
    from ingestion.apify_client import ApifyClient, ApifyError

    actor_input: dict[str, Any] = {
        "action":             "getListings",
        "locationQuery":      f"Jacksonville, FL {zip_code}",
        "currency":           "USD",
        "maxItems":           _MAX_ITEMS,
        "minBedrooms":        max(1, bedrooms - 1),
        "maxBedrooms":        bedrooms + 1,
        "locale":             "en-US",
        "proxyConfiguration": {"useApifyProxy": True},
    }

    try:
        client = ApifyClient()
        items  = client.run_actor(_ACTOR_ID, input_payload=actor_input, memory_mbytes=512)
        logger.info("airbnb_actor_done", zip_code=zip_code, bedrooms=bedrooms, count=len(items))
        return items
    except ApifyError as exc:
        logger.warning("airbnb_actor_error",    zip_code=zip_code, bedrooms=bedrooms, error=str(exc))
        return []
    except Exception as exc:
        logger.warning("airbnb_actor_unexpected", zip_code=zip_code, bedrooms=bedrooms, error=str(exc))
        return []


# ── Processing pipeline ───────────────────────────────────────────────────────

def _process_comps(raw_items: list[dict]) -> StrCompResult:
    """
    Turn raw Airbnb actor output into a StrCompResult.

    Steps:
    1. Extract ADRs; apply 2σ outlier filter.
    2. Estimate per-listing occupancy from review count.
    3. Compute median ADR and median occupancy across filtered comps.
    4. Classify confidence (HIGH / MEDIUM / LOW).
    5. Calculate gross and net monthly revenue for a single unit.
    """
    if not raw_items:
        return StrCompResult(
            comp_count=0, median_adr=None, estimated_occupancy=None,
            gross_monthly=None, net_monthly=None,
            confidence="LOW", str_validated=False,
        )

    adrs_raw:      list[float] = []
    occ_samples:   list[float] = []
    review_counts: list[float] = []

    for comp in raw_items:
        adr = _extract_adr(comp)
        if adr and adr > 0:
            adrs_raw.append(adr)

        occ = _estimate_occupancy_from_reviews(comp)
        if occ is not None:
            occ_samples.append(occ)

        r = (
            comp.get("reviews")
            or comp.get("numberOfReviews")
            or comp.get("reviewCount")
        )
        if r is not None:
            try:
                review_counts.append(float(r))
            except (TypeError, ValueError):
                pass

    adrs_filtered = _filter_outliers(adrs_raw)
    comp_count    = len(adrs_filtered)

    if comp_count == 0:
        return StrCompResult(
            comp_count=0, median_adr=None, estimated_occupancy=None,
            gross_monthly=None, net_monthly=None,
            confidence="LOW", str_validated=False,
            comps=raw_items,
        )

    median_adr          = round(statistics.median(adrs_filtered), 2)
    estimated_occupancy = (
        round(statistics.median(occ_samples), 4)
        if occ_samples else _OCC_DEFAULT
    )
    median_reviews = statistics.median(review_counts) if review_counts else None

    confidence                = _classify_confidence(comp_count, median_reviews, estimated_occupancy)
    gross_monthly, net_monthly = _calc_revenue(median_adr, estimated_occupancy)

    sample_addresses = [
        str(c.get("address") or c.get("title") or c.get("name") or "")
        for c in raw_items[:5]
        if c.get("address") or c.get("title") or c.get("name")
    ]

    logger.info(
        "airbnb_comps_processed",
        comp_count=comp_count,
        median_adr=median_adr,
        estimated_occupancy=estimated_occupancy,
        confidence=confidence,
    )

    return StrCompResult(
        comp_count=comp_count,
        median_adr=median_adr,
        estimated_occupancy=estimated_occupancy,
        gross_monthly=gross_monthly,
        net_monthly=net_monthly,
        confidence=confidence,
        str_validated=comp_count >= _STR_VALIDATED_MIN,
        comps=raw_items,
        sample_addresses=sample_addresses,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def get_str_comps(zip_code: str, bedrooms: int) -> StrCompResult:
    """
    Fetch and process Airbnb STR comps for a zip code + bedroom combination.

    This is the primary entry point called by the pipeline after a cache miss.
    Cache reads/writes are handled by the caller (_try_airbnb_str_comps in pipeline.py).

    Args:
        zip_code: 5-digit zip code (e.g. "32204").
        bedrooms: Bedroom count for the unit type being analyzed.

    Returns:
        StrCompResult with processed comp statistics.
    """
    logger.info("airbnb_get_str_comps", zip_code=zip_code, bedrooms=bedrooms)
    raw_items = _run_actor(zip_code, bedrooms)
    return _process_comps(raw_items)
