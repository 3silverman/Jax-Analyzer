"""
Tests for ingestion/airbnb_comps.py — pure-logic tests (no real Apify calls).

All tests mock _run_actor so no APIFY_TOKEN is required.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ingestion.airbnb_comps import (
    StrCompResult,
    _calc_revenue,
    _classify_confidence,
    _estimate_occupancy_from_reviews,
    _extract_adr,
    _filter_outliers,
    _process_comps,
    get_str_comps,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_comp(price: float, reviews: int | None = 20, address: str = "") -> dict:
    """Build a minimal raw Airbnb actor item."""
    c: dict = {"price": price}
    if reviews is not None:
        c["numberOfReviews"] = reviews
    if address:
        c["address"] = address
    return c


def _make_comps(prices: list[float], reviews: int = 25) -> list[dict]:
    return [_make_comp(p, reviews) for p in prices]


# ── _estimate_occupancy_from_reviews ──────────────────────────────────────────

class TestEstimateOccupancy:
    def test_high_reviews_gives_max_occ(self):
        occ = _estimate_occupancy_from_reviews({"numberOfReviews": 200})
        assert occ == 0.75

    def test_low_reviews_gives_min_occ(self):
        occ = _estimate_occupancy_from_reviews({"numberOfReviews": 3})
        assert occ == 0.40

    def test_mid_reviews_interpolates(self):
        occ = _estimate_occupancy_from_reviews({"numberOfReviews": 52})  # midpoint 5..100
        assert 0.40 < occ < 0.75

    def test_missing_reviews_returns_none(self):
        assert _estimate_occupancy_from_reviews({}) is None

    def test_accepts_reviews_key(self):
        occ = _estimate_occupancy_from_reviews({"reviews": 100})
        assert occ == 0.75

    def test_accepts_reviewCount_key(self):
        occ = _estimate_occupancy_from_reviews({"reviewCount": 5})
        assert occ == 0.40

    def test_invalid_value_returns_none(self):
        assert _estimate_occupancy_from_reviews({"numberOfReviews": "N/A"}) is None


# ── _extract_adr ──────────────────────────────────────────────────────────────

class TestExtractAdr:
    def test_price_key(self):
        assert _extract_adr({"price": 120.0}) == 120.0

    def test_pricePerNight_key(self):
        assert _extract_adr({"pricePerNight": 95.0}) == 95.0

    def test_nested_amount_dict(self):
        assert _extract_adr({"price": {"amount": 110, "currency": "USD"}}) == 110.0

    def test_zero_price_skipped(self):
        # zero price should not be returned; try basePrice fallback
        assert _extract_adr({"price": 0, "basePrice": 80}) == 80.0

    def test_missing_all_keys_returns_none(self):
        assert _extract_adr({"listing_id": "abc"}) is None

    def test_invalid_value_falls_through(self):
        assert _extract_adr({"price": "ask"}) is None


# ── _filter_outliers ──────────────────────────────────────────────────────────

class TestFilterOutliers:
    def test_removes_extreme_high(self):
        # Use enough tightly-clustered values so the extreme outlier clearly > 2σ
        adrs = [100.0, 110.0, 105.0, 95.0, 102.0, 108.0, 103.0, 1000.0]
        filtered = _filter_outliers(adrs)
        assert 1000.0 not in filtered
        assert all(v <= 150 for v in filtered)

    def test_keeps_all_when_few_items(self):
        adrs = [100.0, 200.0]
        assert _filter_outliers(adrs) == adrs

    def test_empty_list(self):
        assert _filter_outliers([]) == []

    def test_uniform_list_unchanged(self):
        adrs = [100.0, 100.0, 100.0]
        assert _filter_outliers(adrs) == adrs


# ── _classify_confidence ──────────────────────────────────────────────────────

class TestClassifyConfidence:
    def test_high_confidence(self):
        assert _classify_confidence(12, 15, 0.70) == "HIGH"

    def test_medium_confidence_enough_comps(self):
        assert _classify_confidence(7, 5, 0.45) == "MEDIUM"

    def test_low_confidence_too_few_comps(self):
        assert _classify_confidence(3, 20, 0.70) == "LOW"

    def test_high_requires_all_criteria(self):
        # Enough comps + reviews, but low occupancy → MEDIUM (falls to MEDIUM if ≥5)
        assert _classify_confidence(12, 15, 0.40) == "MEDIUM"


# ── _calc_revenue ─────────────────────────────────────────────────────────────

class TestCalcRevenue:
    def test_basic_calculation(self):
        gross, net = _calc_revenue(median_adr=100.0, occupancy=0.75)
        # gross = 100 * 0.75 * 30.4 = 2280.0
        assert gross == pytest.approx(2280.0, abs=1.0)
        # net = 2280 * 0.85 - (125 * 0.75 * 4) = 1938 - 375 = 1563
        assert net == pytest.approx(1563.0, abs=1.0)

    def test_zero_occupancy_yields_zero_gross(self):
        gross, net = _calc_revenue(100.0, 0.0)
        assert gross == 0.0

    def test_net_lower_than_gross(self):
        gross, net = _calc_revenue(120.0, 0.70)
        assert net < gross


# ── _process_comps ────────────────────────────────────────────────────────────

class TestProcessComps:
    def test_empty_items_returns_low_confidence(self):
        result = _process_comps([])
        assert result.comp_count == 0
        assert result.median_adr is None
        assert result.confidence == "LOW"
        assert result.str_validated is False

    def test_enough_comps_validated(self):
        items = _make_comps([100, 110, 120, 105, 115, 108], reviews=30)
        result = _process_comps(items)
        assert result.comp_count >= 5
        assert result.str_validated is True
        assert result.median_adr is not None
        assert result.gross_monthly is not None
        assert result.net_monthly is not None

    def test_outlier_removed(self):
        # 1000 is a clear outlier among sub-150 prices
        items = _make_comps([100, 110, 120, 105, 115, 108, 1000], reviews=30)
        result = _process_comps(items)
        assert result.median_adr is not None
        assert result.median_adr < 200  # outlier excluded

    def test_median_adr_correct(self):
        # Sorted: 100,110,120 → median = 110
        items = _make_comps([100, 120, 110], reviews=50)
        result = _process_comps(items)
        # comp_count may be < 5 so no validation, but ADR should be ~110
        assert result.median_adr == pytest.approx(110.0, abs=5.0)

    def test_no_prices_yields_zero_comps(self):
        items = [{"numberOfReviews": 20}] * 5  # no price key
        result = _process_comps(items)
        assert result.comp_count == 0

    def test_sample_addresses_populated(self):
        items = [_make_comp(100, 20, f"Apt {i}, Jacksonville FL") for i in range(6)]
        result = _process_comps(items)
        assert len(result.sample_addresses) <= 5

    def test_occupancy_uses_default_when_no_reviews(self):
        items = [{"price": 100}, {"price": 110}, {"price": 105},
                 {"price": 108}, {"price": 112}, {"price": 95}]
        result = _process_comps(items)
        assert result.estimated_occupancy == pytest.approx(0.60, abs=0.01)


# ── Smoke test: 1BD in 32204 ─────────────────────────────────────────────────

class TestGetStrComps:
    """
    Smoke test for 1BD unit in zip 32204.
    Mocks _run_actor to return realistic-ish Airbnb data for Jacksonville.
    """

    _MOCK_ITEMS = [
        {"price": 89,  "numberOfReviews": 45, "address": "111 Riverside Ave, Jacksonville, FL 32204"},
        {"price": 95,  "numberOfReviews": 62, "address": "220 Oak St, Jacksonville, FL 32204"},
        {"price": 110, "numberOfReviews": 33, "address": "305 Herschel St, Jacksonville, FL 32204"},
        {"price": 105, "numberOfReviews": 51, "address": "417 St Johns Ave, Jacksonville, FL 32204"},
        {"price": 98,  "numberOfReviews": 28, "address": "509 Avondale Ave, Jacksonville, FL 32204"},
        {"price": 120, "numberOfReviews": 75, "address": "612 Post St, Jacksonville, FL 32204"},
        {"price": 88,  "numberOfReviews": 19, "address": "734 Talbot Ave, Jacksonville, FL 32204"},
        {"price": 102, "numberOfReviews": 40, "address": "840 Gilmore St, Jacksonville, FL 32204"},
    ]

    def test_smoke_1bd_32204(self):
        with patch("ingestion.airbnb_comps._run_actor", return_value=self._MOCK_ITEMS):
            result = get_str_comps(zip_code="32204", bedrooms=1)

        assert isinstance(result, StrCompResult)
        # 8 comps, none are outliers → all pass filter
        assert result.comp_count == 8
        assert result.str_validated is True   # ≥ 5
        assert result.median_adr is not None
        assert 85 <= result.median_adr <= 125, f"Unexpected median ADR: {result.median_adr}"
        assert result.estimated_occupancy is not None
        assert 0.40 <= result.estimated_occupancy <= 0.75
        assert result.gross_monthly is not None and result.gross_monthly > 0
        assert result.net_monthly is not None and result.net_monthly > 0
        assert result.confidence in ("HIGH", "MEDIUM", "LOW")
        # Should be MEDIUM (8 comps, median reviews ~40, occ ~60%)
        assert result.confidence in ("HIGH", "MEDIUM")

    def test_smoke_reports_comp_count(self):
        with patch("ingestion.airbnb_comps._run_actor", return_value=self._MOCK_ITEMS):
            result = get_str_comps(zip_code="32204", bedrooms=1)
        print(
            f"\n[STR Smoke] zip=32204 beds=1 | "
            f"comps={result.comp_count} | "
            f"ADR=${result.median_adr:.0f} | "
            f"occ={result.estimated_occupancy:.0%} | "
            f"gross=${result.gross_monthly:.0f}/mo | "
            f"net=${result.net_monthly:.0f}/mo | "
            f"conf={result.confidence}"
        )
        assert result.comp_count >= 5

    def test_apify_error_returns_empty_result(self):
        with patch("ingestion.airbnb_comps._run_actor", return_value=[]):
            result = get_str_comps(zip_code="32204", bedrooms=1)
        assert result.comp_count == 0
        assert result.str_validated is False
        assert result.median_adr is None
