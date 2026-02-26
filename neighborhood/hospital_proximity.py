"""
neighborhood/hospital_proximity.py

Straight-line distance calculation from property to Jacksonville hospital anchors.
Used to score MTR (travel-nurse / corporate) rental demand.

No API calls — pure math using the Haversine formula.
"""

from __future__ import annotations

import math
from typing import TypedDict


# ── Hospital anchor coordinates ───────────────────────────────────────────────

HOSPITALS: list[dict] = [
    {
        "name": "St. Vincent's Medical Center",
        "lat":  30.3322,
        "lon": -81.6557,
    },
    {
        "name": "UF Health Shands Jacksonville",
        "lat":  30.3274,
        "lon": -81.6557,
    },
]


class HospitalProximityResult(TypedDict):
    closest_hospital:  str          # name of nearest hospital
    distance_miles:    float        # straight-line miles to nearest hospital
    proximity_score:   int          # 0, 2, 5, or 8 MTR bonus points
    all_distances:     dict[str, float]  # name → miles for all anchors


# ── Haversine helper ──────────────────────────────────────────────────────────

_EARTH_RADIUS_MILES = 3_958.8


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in miles between two lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


# ── Scoring table ─────────────────────────────────────────────────────────────

def _proximity_score(distance_miles: float) -> int:
    """Return MTR proximity bonus points per CLAUDE.md scoring table."""
    if distance_miles <= 0.30:
        return 8
    if distance_miles <= 0.75:
        return 5
    if distance_miles <= 1.50:
        return 2
    return 0


# ── Public API ────────────────────────────────────────────────────────────────

def get_hospital_proximity(lat: float, lon: float) -> HospitalProximityResult:
    """
    Calculate straight-line distances from a property to JAX hospital anchors
    and return the MTR proximity bonus score.

    Args:
        lat: Property latitude.
        lon: Property longitude.

    Returns:
        HospitalProximityResult with closest hospital name, distance, and score.
    """
    distances: dict[str, float] = {}
    for hospital in HOSPITALS:
        dist = _haversine_miles(lat, lon, hospital["lat"], hospital["lon"])
        distances[hospital["name"]] = round(dist, 4)

    closest_name = min(distances, key=lambda k: distances[k])
    closest_dist = distances[closest_name]
    score        = _proximity_score(closest_dist)

    return HospitalProximityResult(
        closest_hospital = closest_name,
        distance_miles   = round(closest_dist, 3),
        proximity_score  = score,
        all_distances    = distances,
    )
