"""Tests for risk_score.py"""

from __future__ import annotations
import pytest
from scoring.risk_score import compute_risk_score


class TestRiskScore:
    def test_new_build_no_deductions_gives_30(self) -> None:
        r = compute_risk_score(year_built=2015, flood_high_risk=False, purchase_price=300_000)
        assert r.score == 30
        assert r.deductions == []

    def test_pre_1950_deducts_5(self) -> None:
        r = compute_risk_score(year_built=1940, flood_high_risk=False, purchase_price=300_000)
        assert r.score == 25
        assert any(5 == pts for _, pts in r.deductions)

    def test_1950_to_1979_deducts_3(self) -> None:
        r = compute_risk_score(year_built=1965, flood_high_risk=False, purchase_price=300_000)
        assert r.score == 27

    def test_flood_ae_deducts_10(self) -> None:
        r = compute_risk_score(year_built=2010, flood_high_risk=True, purchase_price=300_000)
        assert r.score == 20

    def test_appraisal_gap_risk_deducts_5(self) -> None:
        # Price is 20% above median — triggers the deduction
        r = compute_risk_score(
            year_built=2000, flood_high_risk=False,
            purchase_price=360_000, zip_median_price=300_000
        )
        assert r.score == 25  # 30 - 5

    def test_no_appraisal_gap_within_15_pct(self) -> None:
        r = compute_risk_score(
            year_built=2000, flood_high_risk=False,
            purchase_price=340_000, zip_median_price=300_000  # 13.3% above
        )
        assert r.score == 30

    def test_month_to_month_deducts_2(self) -> None:
        r = compute_risk_score(
            year_built=2000, flood_high_risk=False,
            purchase_price=300_000, all_month_to_month=True
        )
        assert r.score == 28

    def test_no_inspection_deducts_3(self) -> None:
        r = compute_risk_score(
            year_built=2000, flood_high_risk=False,
            purchase_price=300_000, no_inspection_contingency=True
        )
        assert r.score == 27

    def test_all_deductions_combined_floors_at_0(self) -> None:
        r = compute_risk_score(
            year_built=1930,
            flood_high_risk=True,
            purchase_price=500_000,
            zip_median_price=300_000,
            all_month_to_month=True,
            no_inspection_contingency=True,
        )
        assert r.score >= 0

    def test_none_year_built_no_age_deduction(self) -> None:
        r = compute_risk_score(year_built=None, flood_high_risk=False, purchase_price=300_000)
        assert r.score == 30  # no age penalty when unknown

    def test_risk_flags_populated_for_old_property(self) -> None:
        r = compute_risk_score(year_built=1935, flood_high_risk=False, purchase_price=300_000)
        assert len(r.risk_flags) >= 1
        assert any("1935" in f for f in r.risk_flags)
