"""
neighborhood/liveability.py

Neighborhood Liveability Index (0–100).

Aggregates external scores into a single index for display and deal-card ranking.
Not a hard gate — used to inform scoring and UI.

Component breakdown (max 100):
  Walk Score          0–20 pts  (walk_score / 100 * 20)
  Hospital proximity  0–20 pts  (from hospital_proximity.get_hospital_proximity)
  Crime grade         0–30 pts  (A=30, B=24, C=15, D=8, F=0; UNKNOWN=0)
  Flood zone          0–20 pts  (pass = 20, fail = 0)
  Visual score        0–10 pts  (placeholder default 5; updated when GSV scoring runs)
"""

from __future__ import annotations

from typing import TypedDict

from neighborhood.flood_zone import FloodZoneResult
from neighborhood.hospital_proximity import HospitalProximityResult


# ── Grade → crime points lookup ───────────────────────────────────────────────

_CRIME_POINTS: dict[str, int] = {
    "A+": 30, "A": 30, "A-": 28,
    "B+": 26, "B": 24, "B-": 21,
    "C+": 18, "C": 15, "C-": 12,
    "D+": 10, "D":  8, "D-":  5,
    "F":   0,
}
_CRIME_UNKNOWN = 0


class LiveabilityScore(TypedDict):
    total:              int     # 0–100
    walk_pts:           int     # 0–20
    hospital_pts:       int     # 0–20
    crime_pts:          int     # 0–30
    flood_pts:          int     # 0–20
    visual_pts:         int     # 0–10
    crime_grade:        str     # raw grade string
    walk_score:         int | None
    distance_to_hospital_miles: float
    flood_zone:         str
    flood_passes:       bool


def compute_liveability(
    walk_score: int | None,
    hospital: HospitalProximityResult,
    crime_grade: str,
    flood: FloodZoneResult,
    visual_score: int = 5,
) -> LiveabilityScore:
    """
    Compute the Neighborhood Liveability Index.

    Args:
        walk_score:  Walk Score 0–100 (or None).
        hospital:    HospitalProximityResult from hospital_proximity module.
        crime_grade: Letter grade string, e.g. "B+", "UNKNOWN".
        flood:       FloodZoneResult from flood_zone module.
        visual_score: Google Street View visual quality score 0–10 (default 5).

    Returns:
        LiveabilityScore with total and component breakdown.
    """
    # Walk: 0–20
    walk_pts = int((walk_score or 0) / 100 * 20) if walk_score is not None else 0

    # Hospital: proximity_score already capped 0–8; scale to 0–20
    # Map: 8→20, 5→13, 2→5, 0→0
    hosp_pts_map = {8: 20, 5: 13, 2: 5, 0: 0}
    hospital_pts = hosp_pts_map.get(hospital["proximity_score"], 0)

    # Crime: 0–30
    grade_upper = crime_grade.strip().upper() if crime_grade else "UNKNOWN"
    crime_pts   = _CRIME_POINTS.get(grade_upper, _CRIME_UNKNOWN)

    # Flood: 0–20
    flood_pts  = 0 if flood["is_high_risk"] else 20
    flood_pass = not flood["is_high_risk"]

    # Visual: 0–10
    visual_pts = max(0, min(10, int(visual_score)))

    total = walk_pts + hospital_pts + crime_pts + flood_pts + visual_pts

    return LiveabilityScore(
        total                     = min(100, total),
        walk_pts                  = walk_pts,
        hospital_pts              = hospital_pts,
        crime_pts                 = crime_pts,
        flood_pts                 = flood_pts,
        visual_pts                = visual_pts,
        crime_grade               = grade_upper,
        walk_score                = walk_score,
        distance_to_hospital_miles = hospital["distance_miles"],
        flood_zone                = flood["flood_zone"],
        flood_passes              = flood_pass,
    )
