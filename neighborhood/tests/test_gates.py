"""Tests for hard gate evaluator."""

from __future__ import annotations
from datetime import datetime, timezone
import pytest
from neighborhood.crime_grade import CrimeGradeResult
from neighborhood.flood_zone import FloodZoneResult
from neighborhood.gates import evaluate_gates
from normalization.schema import DataSource, PropertyRecord, PropertyType


def _record(zip_code: str = "32204") -> PropertyRecord:
    return PropertyRecord(
        canonical_id="abc123def456",
        source=DataSource.ZILLOW_SALE,
        source_id="1",
        scraped_at=datetime.now(tz=timezone.utc),
        address="100 Test St",
        zip_code=zip_code,
        price=300_000.0,
    )


def _crime(grade: str = "B", passes: bool = True, low_conf: bool = False) -> CrimeGradeResult:
    return CrimeGradeResult(grade=grade, passes_gate=passes, low_confidence=low_conf)


def _flood(zone: str = "X", high_risk: bool = False) -> FloodZoneResult:
    return FloodZoneResult(flood_zone=zone, is_high_risk=high_risk, sfha=False, panel_number="")


class TestEvaluateGates:
    def test_all_pass(self) -> None:
        result = evaluate_gates(_record("32204"), _crime("A"), _flood("X"))
        assert result.passed is True
        assert result.failed_gates == []
        assert result.destination == "underwriting"

    def test_bad_zip_fails(self) -> None:
        result = evaluate_gates(_record("32099"), _crime("A"), _flood("X"))
        assert result.passed is False
        assert any("32099" in r for r in result.failed_gates)
        assert result.destination == "failed_gates_tab"

    def test_crime_grade_c_fails(self) -> None:
        result = evaluate_gates(_record(), _crime("C", False), _flood("X"))
        assert result.passed is False
        assert any("Crime grade" in r for r in result.failed_gates)

    def test_crime_grade_f_fails(self) -> None:
        result = evaluate_gates(_record(), _crime("F", False), _flood("X"))
        assert result.passed is False

    def test_low_confidence_crime_fails(self) -> None:
        result = evaluate_gates(_record(), _crime("UNKNOWN", False, True), _flood("X"))
        assert result.passed is False
        assert any("could not be verified" in r for r in result.failed_gates)

    def test_flood_zone_ae_fails(self) -> None:
        result = evaluate_gates(_record(), _crime("B"), _flood("AE", True))
        assert result.passed is False
        assert any("AE" in r for r in result.failed_gates)

    def test_flood_zone_ve_fails(self) -> None:
        result = evaluate_gates(_record(), _crime("B"), _flood("VE", True))
        assert result.passed is False

    def test_flood_zone_unknown_does_not_fail(self) -> None:
        # Unknown flood zone is a soft warning — does not hard-fail the gate
        result = evaluate_gates(_record(), _crime("A"), _flood("UNKNOWN", False))
        assert result.passed is True

    def test_multiple_failures_reported(self) -> None:
        result = evaluate_gates(_record("99999"), _crime("F", False), _flood("AE", True))
        assert result.passed is False
        assert len(result.failed_gates) >= 3

    def test_all_whitelisted_zips_pass_zip_gate(self) -> None:
        for zip_code in ["32204", "32205", "32206", "32207"]:
            result = evaluate_gates(_record(zip_code), _crime("A"), _flood("X"))
            assert result.passed is True, f"Zip {zip_code} should pass"


