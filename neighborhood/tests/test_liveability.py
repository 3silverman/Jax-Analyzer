"""Tests for liveability index — pure computation, no API calls."""

from __future__ import annotations
import pytest
from neighborhood.flood_zone import FloodZoneResult
from neighborhood.hospital_proximity import HospitalProximityResult
from neighborhood.liveability import compute_liveability


def _flood(zone: str = "X", high_risk: bool = False) -> FloodZoneResult:
    return FloodZoneResult(flood_zone=zone, is_high_risk=high_risk, sfha=False, panel_number="")


def _hospital(score: int = 8, dist: float = 0.2) -> HospitalProximityResult:
    return HospitalProximityResult(
        closest_hospital="St. Vincent's Medical Center",
        distance_miles=dist,
        proximity_score=score,
        all_distances={"St. Vincent's Medical Center": dist, "UF Health Shands Jacksonville": dist + 0.05},
    )


class TestComputeLiveability:
    def test_perfect_score(self) -> None:
        result = compute_liveability(
            walk_score=100,
            hospital=_hospital(score=8, dist=0.1),
            crime_grade="A",
            flood=_flood("X", False),
            visual_score=10,
        )
        assert result["total"] == 100

    def test_zero_walk_score(self) -> None:
        result = compute_liveability(
            walk_score=0,
            hospital=_hospital(score=0, dist=5.0),
            crime_grade="F",
            flood=_flood("AE", True),
            visual_score=0,
        )
        assert result["total"] == 0

    def test_flood_fail_zeroes_flood_pts(self) -> None:
        result = compute_liveability(
            walk_score=50,
            hospital=_hospital(score=0),
            crime_grade="B",
            flood=_flood("AE", True),
        )
        assert result["flood_pts"] == 0
        assert result["flood_passes"] is False

    def test_flood_pass_gives_20_pts(self) -> None:
        result = compute_liveability(
            walk_score=0,
            hospital=_hospital(score=0),
            crime_grade="F",
            flood=_flood("X", False),
            visual_score=0,
        )
        assert result["flood_pts"] == 20

    def test_none_walk_score_gives_0_walk_pts(self) -> None:
        result = compute_liveability(
            walk_score=None,
            hospital=_hospital(score=0),
            crime_grade="F",
            flood=_flood("AE", True),
        )
        assert result["walk_pts"] == 0

    def test_crime_grade_a_gives_30_pts(self) -> None:
        result = compute_liveability(50, _hospital(), "A", _flood(), 5)
        assert result["crime_pts"] == 30

    def test_crime_grade_b_gives_24_pts(self) -> None:
        result = compute_liveability(50, _hospital(), "B", _flood(), 5)
        assert result["crime_pts"] == 24

    def test_crime_grade_unknown_gives_0_pts(self) -> None:
        result = compute_liveability(50, _hospital(), "UNKNOWN", _flood(), 5)
        assert result["crime_pts"] == 0

    def test_walk_50_gives_10_pts(self) -> None:
        result = compute_liveability(50, _hospital(score=0), "F", _flood("AE", True), 0)
        assert result["walk_pts"] == 10

    def test_hospital_score_8_maps_to_20_pts(self) -> None:
        result = compute_liveability(0, _hospital(score=8), "F", _flood("AE", True), 0)
        assert result["hospital_pts"] == 20

    def test_hospital_score_5_maps_to_13_pts(self) -> None:
        result = compute_liveability(0, _hospital(score=5), "F", _flood("AE", True), 0)
        assert result["hospital_pts"] == 13

    def test_total_capped_at_100(self) -> None:
        result = compute_liveability(100, _hospital(8), "A+", _flood(), 10)
        assert result["total"] <= 100

    def test_visual_score_default_5(self) -> None:
        result = compute_liveability(0, _hospital(score=0), "F", _flood("AE", True))
        assert result["visual_pts"] == 5
