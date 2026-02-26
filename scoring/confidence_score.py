"""
scoring/confidence_score.py

Confidence Score (0–30) component of the Deal Score.

Maps the 0.0–1.0 confidence float from normalization/ to 0–30 points,
then applies bonuses/penalties for comp availability, scrape freshness,
and critical missing fields.

Bonus/penalty table (applied after base mapping):
  +5 if rental comps count ≥ 5
  +3 if scrape age < 24 hours
  -5 if any critical field is missing (price, address, unit count)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ConfidenceScore:
    score:        int     # 0–30
    base_pts:     int     # mapped from normalization confidence float
    comp_bonus:   int     # 0 or +5
    fresh_bonus:  int     # 0 or +3
    field_penalty: int    # 0 or -5
    missing_critical: list[str]


def compute_confidence_score(
    raw_confidence: float,
    scraped_at: datetime,
    comps_count: int = 0,
    price: float | None = None,
    address: str | None = None,
    num_units: int | None = None,
) -> ConfidenceScore:
    """
    Compute the Confidence Score (0–30).

    Args:
        raw_confidence: Normalization confidence float 0.0–1.0.
        scraped_at:     UTC datetime when the property was scraped.
        comps_count:    Number of validated rental comps available.
        price:          Property purchase price (None = missing).
        address:        Property address string (None/empty = missing).
        num_units:      Number of units (None = missing).

    Returns:
        ConfidenceScore dataclass.
    """
    # ── Base: map 0.0–1.0 → 0–22 pts (leave room for bonuses up to 30) ──────
    base_pts = min(22, round(raw_confidence * 22))

    # ── Comps bonus ───────────────────────────────────────────────────────────
    comp_bonus = 5 if comps_count >= 5 else 0

    # ── Freshness bonus ───────────────────────────────────────────────────────
    now = datetime.now(tz=timezone.utc)
    if scraped_at.tzinfo is None:
        scraped_at = scraped_at.replace(tzinfo=timezone.utc)
    age_hours  = (now - scraped_at).total_seconds() / 3600
    fresh_bonus = 3 if age_hours < 24 else 0

    # ── Critical field penalty ────────────────────────────────────────────────
    missing_critical: list[str] = []
    if not price or price <= 0:
        missing_critical.append("price")
    if not address or not address.strip():
        missing_critical.append("address")
    if num_units is None:
        missing_critical.append("num_units")
    field_penalty = 5 if missing_critical else 0

    raw   = base_pts + comp_bonus + fresh_bonus - field_penalty
    score = max(0, min(30, raw))

    return ConfidenceScore(
        score          = score,
        base_pts       = base_pts,
        comp_bonus     = comp_bonus,
        fresh_bonus    = fresh_bonus,
        field_penalty  = field_penalty,
        missing_critical = missing_critical,
    )
