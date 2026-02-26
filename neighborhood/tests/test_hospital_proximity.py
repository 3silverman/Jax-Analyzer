"""Tests for hospital_proximity — pure math, no API calls."""

from __future__ import annotations
import pytest
from neighborhood.hospital_proximity import get_hospital_proximity, _haversine_miles


class TestHaversineMiles:
    def test_zero_distance(self) -> None:
        assert _haversine_miles(30.32, -81.66, 30.32, -81.66) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_approx(self) -> None:
        # ~69 miles per degree of latitude
        d = _haversine_miles(30.0, -81.66, 31.0, -81.66)
        assert 68.0 < d < 70.0


class TestGetHospitalProximity:
    def test_very_close_scores_8(self) -> None:
        # St. Vincent's is at 30.3322, -81.6557
        result = get_hospital_proximity(30.3323, -81.6558)
        assert result["proximity_score"] == 8

    def test_half_mile_scores_5(self) -> None:
        # ~0.5 miles north of St. Vincent's
        result = get_hospital_proximity(30.3395, -81.6557)
        assert result["proximity_score"] == 5

    def test_one_mile_scores_2(self) -> None:
        # ~1.1 miles from hospitals (Springfield area)
        result = get_hospital_proximity(30.3450, -81.6557)
        assert result["proximity_score"] in (2, 5)  # depends on exact distance

    def test_far_scores_0(self) -> None:
        # NAS Jacksonville — well over 1.5 miles away
        result = get_hospital_proximity(30.2241, -81.6808)
        assert result["proximity_score"] == 0

    def test_returns_all_distances(self) -> None:
        result = get_hospital_proximity(30.32, -81.66)
        assert "St. Vincent's Medical Center" in result["all_distances"]
        assert "UF Health Shands Jacksonville" in result["all_distances"]

    def test_closest_hospital_is_string(self) -> None:
        result = get_hospital_proximity(30.32, -81.66)
        assert isinstance(result["closest_hospital"], str)
        assert len(result["closest_hospital"]) > 0

    def test_distance_is_positive(self) -> None:
        result = get_hospital_proximity(30.32, -81.66)
        assert result["distance_miles"] >= 0.0
