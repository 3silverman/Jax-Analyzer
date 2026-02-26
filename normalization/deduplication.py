"""
normalization/deduplication.py

Deduplication logic for PropertyRecord and RentalComp instances.

Two strategies:
  1. Canonical ID dedup — exact match on make_canonical_id(address, zip) hash.
     Catches the same property from Zillow + Rentcast.

  2. Coordinate proximity dedup — properties within 30 m of each other
     (approx 0.00027° lat/lon) are treated as duplicates if their
     canonical IDs differ.  Catches address formatting variants.

The merge strategy: when a duplicate is found, keep the record with the
higher confidence_score; populate missing fields from the lower-confidence
copy before discarding it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import TypeVar

from normalization.schema import PropertyRecord, RentalComp

# Threshold: ~30 metres at JAX latitude (approx 30° N)
# 1 degree latitude ≈ 111 km; 30 m / 111,000 ≈ 0.00027°
_COORD_THRESHOLD_DEG: float = 0.00027

T = TypeVar("T", PropertyRecord, RentalComp)


# ──────────────────────────────────────────────────────────────────────────────
# Coordinate distance helper
# ──────────────────────────────────────────────────────────────────────────────

def _haversine_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Return approximate distance in degrees between two lat/lon pairs.
    Uses simple Euclidean distance on the degree grid (accurate enough for
    the ~30 m dedup threshold near Jacksonville, FL).
    """
    dlat = lat1 - lat2
    dlon = (lon1 - lon2) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.sqrt(dlat ** 2 + dlon ** 2)


# ──────────────────────────────────────────────────────────────────────────────
# Merge helpers
# ──────────────────────────────────────────────────────────────────────────────

def _merge_property_records(primary: PropertyRecord, secondary: PropertyRecord) -> PropertyRecord:
    """
    Return a copy of `primary` enriched with non-None fields from `secondary`.
    `primary` must have equal or higher confidence.
    """
    updates: dict = {}
    for field in ("beds", "baths", "sqft", "year_built", "lat", "lon",
                  "lot_size_sqft", "days_on_market", "list_date"):
        if getattr(primary, field) is None and getattr(secondary, field) is not None:
            updates[field] = getattr(secondary, field)
    # Merge raw dicts (primary wins on key conflicts)
    if secondary.raw:
        merged_raw = {**secondary.raw, **primary.raw}
        updates["raw"] = merged_raw
    return primary.model_copy(update=updates) if updates else primary


def _merge_rental_comps(primary: RentalComp, secondary: RentalComp) -> RentalComp:
    """
    Return a copy of `primary` enriched with non-None fields from `secondary`.
    """
    updates: dict = {}
    for field in ("beds", "baths", "sqft", "lat", "lon", "monthly_rate", "adr"):
        if getattr(primary, field) is None and getattr(secondary, field) is not None:
            updates[field] = getattr(secondary, field)
    if secondary.raw:
        merged_raw = {**secondary.raw, **primary.raw}
        updates["raw"] = merged_raw
    return primary.model_copy(update=updates) if updates else primary


# ──────────────────────────────────────────────────────────────────────────────
# Main dedup function
# ──────────────────────────────────────────────────────────────────────────────

def deduplicate_property_records(records: list[PropertyRecord]) -> list[PropertyRecord]:
    """
    Deduplicate a list of PropertyRecord objects.

    Steps:
      1. Group by canonical_id (exact hash match).
      2. Within each group, pick the record with the highest confidence_score
         and merge any fields that were missing from it.
      3. Perform a second pass using coordinate proximity on surviving records.
         Records within _COORD_THRESHOLD_DEG of each other are merged again.

    Returns a deduplicated list, highest-confidence record wins each group.
    """
    # ── Pass 1: canonical_id grouping ────────────────────────────────────────
    groups: dict[str, list[PropertyRecord]] = defaultdict(list)
    for rec in records:
        groups[rec.canonical_id].append(rec)

    merged: list[PropertyRecord] = []
    for group in groups.values():
        best = max(group, key=lambda r: r.confidence_score)
        for other in group:
            if other is not best:
                best = _merge_property_records(best, other)
        merged.append(best)

    # ── Pass 2: coordinate proximity ─────────────────────────────────────────
    # Only consider records that have both lat and lon
    with_coords = [r for r in merged if r.lat is not None and r.lon is not None]
    no_coords   = [r for r in merged if r.lat is None or r.lon is None]

    used: set[str] = set()
    final: list[PropertyRecord] = []

    for i, rec_a in enumerate(with_coords):
        if rec_a.canonical_id in used:
            continue
        cluster: list[PropertyRecord] = [rec_a]
        for rec_b in with_coords[i + 1:]:
            if rec_b.canonical_id in used:
                continue
            dist = _haversine_deg(
                rec_a.lat, rec_a.lon,  # type: ignore[arg-type]
                rec_b.lat, rec_b.lon,  # type: ignore[arg-type]
            )
            if dist <= _COORD_THRESHOLD_DEG:
                cluster.append(rec_b)
                used.add(rec_b.canonical_id)
        used.add(rec_a.canonical_id)
        best = max(cluster, key=lambda r: r.confidence_score)
        for other in cluster:
            if other is not best:
                best = _merge_property_records(best, other)
        final.append(best)

    final.extend(no_coords)
    return final


def deduplicate_rental_comps(comps: list[RentalComp]) -> list[RentalComp]:
    """
    Deduplicate a list of RentalComp objects.

    Uses canonical_id grouping only (no confidence score on comps; monthly_rate wins).
    Within each group, keep the comp with a non-None monthly_rate; if tied, keep first.
    """
    groups: dict[str, list[RentalComp]] = defaultdict(list)
    for comp in comps:
        groups[comp.canonical_id].append(comp)

    result: list[RentalComp] = []
    for group in groups.values():
        # Prefer comps that have a monthly_rate or adr
        rated = [c for c in group if c.monthly_rate is not None or c.adr is not None]
        primary = rated[0] if rated else group[0]
        for other in group:
            if other is not primary:
                primary = _merge_rental_comps(primary, other)
        result.append(primary)

    return result
