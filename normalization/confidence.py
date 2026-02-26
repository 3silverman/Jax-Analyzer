"""
normalization/confidence.py

Data confidence scoring for PropertyRecord instances.

Produces a float 0.0–1.0:
  0.70  from field completeness  (7 weighted optional fields)
  0.30  from scrape freshness    (decays over 7 days)

The scoring module scales this to 0–30 points toward the Confidence Score.
A deal is never surfaced if confidence < 0.50 (configurable threshold).
"""

from __future__ import annotations

from datetime import datetime, timezone

from normalization.schema import PropertyRecord, PropertyType


# ──────────────────────────────────────────────────────────────────────────────
# Field weights  (sum = 0.70)
# ──────────────────────────────────────────────────────────────────────────────

_FIELD_WEIGHTS: dict[str, float] = {
    "beds":          0.12,
    "baths":         0.08,
    "sqft":          0.15,
    "year_built":    0.08,
    "lat_lon":       0.12,   # both lat & lon must be present
    "property_type": 0.10,   # must be a known type (not UNKNOWN)
    "days_on_market": 0.05,
}

assert abs(sum(_FIELD_WEIGHTS.values()) - 0.70) < 1e-9, "field weights must sum to 0.70"


# ──────────────────────────────────────────────────────────────────────────────
# Freshness bands  (sum = 0.30)
# ──────────────────────────────────────────────────────────────────────────────

def _freshness_score(scraped_at: datetime) -> float:
    """Return 0.0–0.30 based on how recently the record was scraped."""
    now = datetime.now(tz=timezone.utc)
    # Ensure scraped_at is tz-aware for comparison
    if scraped_at.tzinfo is None:
        scraped_at = scraped_at.replace(tzinfo=timezone.utc)
    age_hours = (now - scraped_at).total_seconds() / 3600

    if age_hours < 24:
        return 0.30
    elif age_hours < 72:
        return 0.24
    elif age_hours < 168:    # 7 days
        return 0.15
    else:
        return 0.06


# ──────────────────────────────────────────────────────────────────────────────
# Sanity checks (applied after scoring; can downgrade a record)
# ──────────────────────────────────────────────────────────────────────────────

def _sanity_penalty(record: PropertyRecord) -> float:
    """
    Return a penalty (0.0–0.10) for values that are technically present but
    implausible for a Jacksonville multifamily property.
    """
    penalty = 0.0

    if record.price is not None:
        if record.price < 50_000 or record.price > 5_000_000:
            penalty += 0.05

    if record.beds is not None and (record.beds < 1 or record.beds > 20):
        penalty += 0.03

    if record.sqft is not None and (record.sqft < 300 or record.sqft > 20_000):
        penalty += 0.03

    if record.year_built is not None and (record.year_built < 1880 or record.year_built > 2025):
        penalty += 0.02

    return min(penalty, 0.10)


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def score_confidence(record: PropertyRecord) -> tuple[float, list[str]]:
    """
    Compute a confidence score and list of missing fields for a PropertyRecord.

    Args:
        record: A PropertyRecord to evaluate.

    Returns:
        (score, missing_fields) where score is 0.0–1.0 and missing_fields lists
        the weighted fields that were absent.
    """
    field_score = 0.0
    missing: list[str] = []

    # beds
    if record.beds is not None:
        field_score += _FIELD_WEIGHTS["beds"]
    else:
        missing.append("beds")

    # baths
    if record.baths is not None:
        field_score += _FIELD_WEIGHTS["baths"]
    else:
        missing.append("baths")

    # sqft
    if record.sqft is not None and record.sqft > 0:
        field_score += _FIELD_WEIGHTS["sqft"]
    else:
        missing.append("sqft")

    # year_built
    if record.year_built is not None:
        field_score += _FIELD_WEIGHTS["year_built"]
    else:
        missing.append("year_built")

    # lat + lon (both required for neighborhood scoring)
    if record.lat is not None and record.lon is not None:
        field_score += _FIELD_WEIGHTS["lat_lon"]
    else:
        missing.append("lat_lon")

    # property_type — must be a known type
    if record.property_type != PropertyType.UNKNOWN:
        field_score += _FIELD_WEIGHTS["property_type"]
    else:
        missing.append("property_type")

    # days_on_market
    if record.days_on_market is not None:
        field_score += _FIELD_WEIGHTS["days_on_market"]
    else:
        missing.append("days_on_market")

    freshness = _freshness_score(record.scraped_at)
    penalty   = _sanity_penalty(record)
    raw_score = field_score + freshness - penalty
    score     = max(0.0, min(1.0, raw_score))

    return round(score, 4), missing


def annotate(record: PropertyRecord) -> PropertyRecord:
    """
    Return a copy of record with confidence_score and missing_fields populated.
    Called by the normalizer after building each PropertyRecord.
    """
    score, missing = score_confidence(record)
    return record.model_copy(update={"confidence_score": score, "missing_fields": missing})
