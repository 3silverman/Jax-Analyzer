"""
ingestion/tests/test_pipeline_integration.py

End-to-end integration test for the analysis pipeline.

Validates normalization → neighborhood gates → underwriting → scoring → deal cards
using realistic Apify fixture data, without hitting any live APIs or the database.

Run with:
    pytest ingestion/tests/test_pipeline_integration.py -v
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ingestion.pipeline import run_pipeline
from ingestion.run_scan import ScanResult
from neighborhood.crime_grade import CrimeGradeResult
from neighborhood.flood_zone import FloodZoneResult
from normalization.normalizer import normalize_zillow_listing, normalize_zillow_rental
from normalization.schema import PropertyType

FIXTURES = Path(__file__).parent / "fixtures"

# ── Shared mock return values ─────────────────────────────────────────────────

_PASS_CRIME = CrimeGradeResult(grade="B+", passes_gate=True, low_confidence=False)
_FAIL_CRIME = CrimeGradeResult(grade="D",  passes_gate=False, low_confidence=False)
_PASS_FLOOD = FloodZoneResult(flood_zone="X", is_high_risk=False, sfha=False, panel_number="")
_FAIL_FLOOD = FloodZoneResult(flood_zone="AE", is_high_risk=True, sfha=True, panel_number="12057C0306H")

_SV_URLS = {
    "street_view_image_url": "https://maps.googleapis.com/test_sv",
    "satellite_view_url":    "https://google.com/maps/test_sat",
    "google_maps_link":      "https://google.com/maps/test_link",
}


def _make_va_rate(rate: float = 0.0675) -> MagicMock:
    va = MagicMock()
    va.rate       = rate
    va.is_stale   = False
    va.source     = "test_fixture"
    va.fetched_at = datetime.now(tz=timezone.utc)
    return va


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def raw_for_sale() -> list[dict]:
    return json.loads((FIXTURES / "apify_for_sale.json").read_text())


@pytest.fixture(scope="module")
def raw_rentals() -> list[dict]:
    return json.loads((FIXTURES / "apify_rentals.json").read_text())


@pytest.fixture(scope="module")
def scan_result(raw_for_sale, raw_rentals) -> ScanResult:
    result = ScanResult()
    result.listings  = [normalize_zillow_listing(r) for r in raw_for_sale]
    result.ltr_comps = [normalize_zillow_rental(r)  for r in raw_rentals]
    return result


# ── Pipeline runner helper ────────────────────────────────────────────────────

def _run_pipeline(
    scan: ScanResult,
    crime: CrimeGradeResult = _PASS_CRIME,
    flood: FloodZoneResult  = _PASS_FLOOD,
) -> dict:
    """Run the pipeline with all external calls mocked."""

    async def _async_crime(zip_code: str) -> CrimeGradeResult:
        return crime

    with (
        patch("ingestion.pipeline.get_crime_grade",        return_value=crime),
        patch("ingestion.pipeline.get_cached_crime_grade", side_effect=_async_crime),
        patch("ingestion.pipeline.get_flood_zone",         return_value=flood),
        patch("ingestion.pipeline.get_street_view_urls",   return_value=_SV_URLS),
        patch("ingestion.pipeline.fetch_va_rate",          return_value=_make_va_rate()),
        patch("ingestion.pipeline._try_rentcast_cached",   return_value=([], None)),
        patch("ingestion.pipeline._try_airbnb_str_comps",  return_value=(None, None)),
        patch("ingestion.pipeline._try_redfin_zip_median", return_value=None),
    ):
        return run_pipeline(scan)


# ── Normalization tests ───────────────────────────────────────────────────────

class TestNormalization:
    """Verify that raw Apify JSON is correctly converted to PropertyRecord objects."""

    def test_price_parsed_from_formatted_string(self, raw_for_sale):
        r = normalize_zillow_listing(raw_for_sale[0])
        assert r.price == 299_000.0

    def test_price_parsed_for_all_fixtures(self, raw_for_sale):
        for raw in raw_for_sale:
            rec = normalize_zillow_listing(raw)
            assert rec.price > 0, f"price=0 for {raw['addressStreet']}"

    def test_address_and_zip_extracted(self, raw_for_sale):
        r = normalize_zillow_listing(raw_for_sale[0])
        assert "Riverside" in r.address
        assert r.zip_code == "32204"

    def test_canonical_id_is_deterministic(self, raw_for_sale):
        r1 = normalize_zillow_listing(raw_for_sale[0])
        r2 = normalize_zillow_listing(raw_for_sale[0])
        assert r1.canonical_id == r2.canonical_id
        assert len(r1.canonical_id) == 12

    def test_multifamily_inferred_as_duplex(self, raw_for_sale):
        """homeType=MULTI_FAMILY → duplex (2 units) by default."""
        r = normalize_zillow_listing(raw_for_sale[0])
        assert r.property_type == PropertyType.DUPLEX
        assert r.num_units == 2

    def test_sfr_inferred_correctly(self, raw_for_sale):
        sfr_raw = next(r for r in raw_for_sale if r["hdpData"]["homeInfo"]["homeType"] == "SINGLE_FAMILY")
        r = normalize_zillow_listing(sfr_raw)
        assert r.property_type == PropertyType.SFR
        assert r.num_units == 1

    def test_lat_lon_populated(self, raw_for_sale):
        r = normalize_zillow_listing(raw_for_sale[0])
        assert r.lat is not None and r.lon is not None
        # Jacksonville area
        assert 30.0 < r.lat < 30.6
        assert -82.0 < r.lon < -81.4

    def test_year_built_populated(self, raw_for_sale):
        r = normalize_zillow_listing(raw_for_sale[0])
        assert r.year_built == 1958

    def test_days_on_market_populated(self, raw_for_sale):
        r = normalize_zillow_listing(raw_for_sale[0])
        assert r.days_on_market == 14

    def test_in_target_zip(self, raw_for_sale):
        for raw in raw_for_sale:
            r = normalize_zillow_listing(raw)
            assert r.in_target_zip, f"Expected {r.zip_code} to be in whitelist"

    def test_rental_comp_normalized(self, raw_rentals):
        comp = normalize_zillow_rental(raw_rentals[0])
        assert comp.monthly_rate == 1300.0
        assert comp.beds == 2
        assert comp.zip_code == "32204"

    def test_rental_comps_all_have_positive_rate(self, raw_rentals):
        for raw in raw_rentals:
            comp = normalize_zillow_rental(raw)
            assert comp.monthly_rate and comp.monthly_rate > 0, (
                f"monthly_rate missing for {raw['addressStreet']}"
            )


# ── Gate tests ────────────────────────────────────────────────────────────────

class TestGates:
    """Verify neighborhood gate logic rejects the right properties."""

    def test_all_pass_with_good_crime_and_flood(self, scan_result):
        result = _run_pipeline(scan_result, crime=_PASS_CRIME, flood=_PASS_FLOOD)
        # No gate rejections — all properties are scored (some may land in review
        # rather than inbox if their deal_score is 40–59 at current market rates).
        assert len(result["rejected"]) == 0
        assert result["scan_summary"]["passed_gates"] == len(scan_result.listings)

    def test_bad_crime_grade_rejects_property(self, scan_result):
        """Mocking D crime grade for every zip → all rejected."""
        result = _run_pipeline(scan_result, crime=_FAIL_CRIME, flood=_PASS_FLOOD)
        assert len(result["inbox"]) == 0
        assert len(result["rejected"]) == len(scan_result.listings)
        for r in result["rejected"]:
            assert "crime" in " ".join(r["failed_gates"]).lower()

    def test_high_risk_flood_rejects_property(self, scan_result):
        """AE flood zone → all rejected."""
        result = _run_pipeline(scan_result, crime=_PASS_CRIME, flood=_FAIL_FLOOD)
        assert len(result["inbox"]) == 0
        assert len(result["rejected"]) == len(scan_result.listings)
        for r in result["rejected"]:
            assert "flood" in " ".join(r["failed_gates"]).lower()

    def test_rejected_record_has_required_fields(self, scan_result):
        result = _run_pipeline(scan_result, crime=_FAIL_CRIME, flood=_PASS_FLOOD)
        required = {"canonical_id", "address", "zip_code", "price", "failed_gates"}
        for r in result["rejected"]:
            missing = required - set(r.keys())
            assert not missing, f"Rejected record missing: {missing}"


# ── Underwriting and scoring tests ────────────────────────────────────────────

class TestScoringAndUnderwriting:
    """Verify that scored deals have valid scores and plausible financials."""

    @pytest.fixture(scope="class")
    def good_result(self, scan_result):
        return _run_pipeline(scan_result, crime=_PASS_CRIME, flood=_PASS_FLOOD)

    def test_all_properties_scored(self, good_result, scan_result):
        # All gate-passing properties are scored regardless of which bucket they land in.
        assert good_result["scan_summary"]["passed_gates"] == len(scan_result.listings)

    def test_deal_score_in_valid_range(self, good_result):
        for card in good_result["inbox"]:
            score = card["deal_score"]
            assert 0 <= score <= 100, f"deal_score={score} out of range for {card['address']}"

    def test_component_scores_sum_to_deal_score(self, good_result):
        for card in good_result["inbox"]:
            total = card["return_score"] + card["risk_score"] + card["confidence_score_pts"]
            assert total == card["deal_score"], (
                f"Component scores {total} ≠ deal_score {card['deal_score']} "
                f"for {card['address']}"
            )

    def test_strategies_present(self, good_result):
        for card in good_result["inbox"]:
            assert len(card["strategies"]) >= 1, f"No strategies for {card['address']}"

    def test_strategy_cash_flows_are_finite(self, good_result):
        for card in good_result["inbox"]:
            for strat in card["strategies"]:
                cf = strat["cash_flow"]
                assert isinstance(cf, (int, float)), f"cash_flow not numeric: {cf}"
                assert math.isfinite(cf), f"cash_flow is nan/inf: {cf}"

    def test_va_loan_fields_present(self, good_result):
        # VA loan fields are flat keys on the card, not nested under 'va_loan'.
        for card in good_result["inbox"]:
            assert "loan_amount" in card,     f"Missing loan_amount for {card['address']}"
            assert "monthly_payment" in card, f"Missing monthly_payment for {card['address']}"
            assert "total_piti" in card,      f"Missing total_piti for {card['address']}"
            assert "funding_fee" in card,     f"Missing funding_fee for {card['address']}"
            # VA loan = 0% down, so funding_fee > 0 and < purchase price
            assert card["funding_fee"] > 0, f"Expected positive funding_fee for {card['address']}"

    def test_stress_tests_present(self, good_result):
        # stress_tests is a list of dicts with 'label', 'cash_flow', 'dscr' keys.
        for card in good_result["inbox"]:
            tests = card.get("stress_tests", [])
            assert isinstance(tests, list) and len(tests) > 0, (
                f"stress_tests missing or empty for {card['address']}"
            )
            labels = {t["label"] for t in tests}
            assert any("Rate Shock" in lbl for lbl in labels), (
                f"Rate Shock test missing for {card['address']}: {labels}"
            )
            assert any("Vacancy" in lbl for lbl in labels), (
                f"Vacancy Shock test missing for {card['address']}: {labels}"
            )

    def test_neighborhood_fields_in_card(self, good_result):
        for card in good_result["inbox"]:
            assert card.get("crime_grade") == "B+"
            assert card.get("flood_zone") == "X"
            assert card.get("flood_high_risk") is False

    def test_affordable_duplex_reaches_inbox(self, scan_result):
        """The $159,900 duplex in 32206 should cash-flow enough to reach inbox (≥60)."""
        result = _run_pipeline(scan_result, crime=_PASS_CRIME, flood=_PASS_FLOOD)
        inbox_addresses = {c["address"] for c in result["inbox"]}
        assert "312 King Rd" in inbox_addresses, (
            f"Affordable duplex not in inbox. Inbox addresses: {inbox_addresses}\n"
            f"All scored: {result['scan_summary']['passed_gates']}"
        )


# ── Deal card schema test ─────────────────────────────────────────────────────

class TestDealCardSchema:
    """Verify every inbox card contains all required display fields."""

    # VA loan fields are flat on the card (not nested under 'va_loan').
    REQUIRED_KEYS = {
        "address", "zip_code", "price", "num_units", "property_type",
        "deal_score", "return_score", "risk_score", "confidence_score_pts",
        "is_high_priority",
        "crime_grade", "flood_zone", "flood_high_risk",
        "hospital_dist_miles", "closest_hospital",
        "strategies", "top_strategy", "top_cash_flow",
        "loan_amount", "monthly_payment", "total_piti", "funding_fee",
        "stress_tests",
        "street_view_url", "satellite_url",
        "why_scored_high", "risk_flags",
    }

    @pytest.fixture(scope="class")
    def inbox_cards(self, scan_result):
        return _run_pipeline(scan_result, crime=_PASS_CRIME, flood=_PASS_FLOOD)["inbox"]

    def test_all_required_keys_present(self, inbox_cards):
        for card in inbox_cards:
            missing = self.REQUIRED_KEYS - set(card.keys())
            assert not missing, (
                f"Card for '{card.get('address')}' missing fields: {missing}"
            )

    def test_street_view_url_is_string(self, inbox_cards):
        for card in inbox_cards:
            assert isinstance(card["street_view_url"], str)

    def test_risk_flags_is_list(self, inbox_cards):
        for card in inbox_cards:
            assert isinstance(card["risk_flags"], list)


# ── Scan summary test ─────────────────────────────────────────────────────────

class TestScanSummary:
    def test_summary_counts_match(self, scan_result):
        result = _run_pipeline(scan_result, crime=_PASS_CRIME, flood=_PASS_FLOOD)
        summary = result["scan_summary"]
        assert summary["total_scanned"] == len(scan_result.listings)
        # With no gate rejections: every listing is scored (passed_gates == total_scanned).
        assert summary["passed_gates"] + len(result["rejected"]) == summary["total_scanned"]
        assert summary["alerts"] == len(result["alerts"])

    def test_va_rate_is_reasonable(self, scan_result):
        result = _run_pipeline(scan_result, crime=_PASS_CRIME, flood=_PASS_FLOOD)
        rate = result["scan_summary"]["va_rate_pct"]
        assert 3.0 < rate < 12.0, f"VA rate {rate}% looks wrong"

    def test_no_errors_on_clean_run(self, scan_result):
        result = _run_pipeline(scan_result, crime=_PASS_CRIME, flood=_PASS_FLOOD)
        assert result["scan_summary"]["errors"] == []
