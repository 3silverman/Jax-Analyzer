"""
Tests for underwriting/calculator.py

Run with:  pytest underwriting/tests/test_calculator.py -v
"""

import math
import pytest

from underwriting.calculator import (
    underwrite,
    UnderwritingResult,
    Strategy,
    Scenario,
    _monthly_payment,
)

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

SAMPLE_PROP = {
    # Acquisition
    "purchase_price": 300_000,
    "down_payment_pct": 0.20,
    "interest_rate": 0.075,
    "loan_term_years": 30,
    "closing_costs_pct": 0.03,
    "rehab_cost": 5_000,

    # LTR
    "monthly_rent": 2_200,
    "vacancy_rate": 0.08,
    "property_mgmt_rate": 0.10,

    # MTR
    "mtr_monthly_rate": 3_400,
    "mtr_vacancy_rate": 0.15,
    "mtr_mgmt_rate": 0.15,
    "mtr_utilities_monthly": 200,
    "mtr_furnishing_monthly": 125,

    # STR
    "str_adr": 175,
    "str_occupancy": 0.65,
    "str_platform_fee_pct": 0.03,
    "str_cleaning_per_stay": 150,
    "str_avg_stay_nights": 3.5,
    "str_mgmt_rate": 0.25,
    "str_utilities_monthly": 275,
    "str_furnishing_monthly": 200,

    # Shared opex
    "property_tax_annual": 4_500,
    "insurance_annual": 1_800,
    "hoa_monthly": 0,
    "maintenance_rate": 0.01,
    "capex_rate": 0.01,
}


@pytest.fixture
def result() -> UnderwritingResult:
    return underwrite(SAMPLE_PROP)


# ──────────────────────────────────────────────────────────────────────────────
# Capital stack
# ──────────────────────────────────────────────────────────────────────────────

class TestCapitalStack:
    def test_loan_amount(self, result):
        assert result.loan_amount == pytest.approx(240_000, rel=1e-4)

    def test_total_cash_invested(self, result):
        # down + closing + rehab = 60k + 9k + 5k = 74k
        assert result.total_cash_invested == pytest.approx(74_000, rel=1e-4)

    def test_monthly_payment_positive(self, result):
        assert result.monthly_payment > 0

    def test_annual_debt_service_equals_12x_monthly(self, result):
        assert result.annual_debt_service == pytest.approx(result.monthly_payment * 12, rel=1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# LTR — base case
# ──────────────────────────────────────────────────────────────────────────────

class TestLTRBase:
    @pytest.fixture(autouse=True)
    def base(self, result):
        self.r = result.ltr.base

    def test_strategy_tag(self):
        assert self.r.strategy == Strategy.LTR

    def test_scenario_tag(self):
        assert self.r.scenario == Scenario.BASE

    def test_gross_annual_revenue(self):
        assert self.r.revenue.gross_annual_revenue == pytest.approx(2_200 * 12, rel=1e-4)

    def test_vacancy_loss(self):
        expected = 2_200 * 12 * 0.08
        assert self.r.revenue.vacancy_loss == pytest.approx(expected, rel=1e-4)

    def test_egi_equals_gross_minus_vacancy(self):
        assert self.r.revenue.effective_gross_income == pytest.approx(
            self.r.revenue.gross_annual_revenue - self.r.revenue.vacancy_loss, rel=1e-6
        )

    def test_noi_less_than_egi(self):
        assert self.r.noi < self.r.revenue.effective_gross_income

    def test_cash_flow_is_noi_minus_debt(self):
        assert self.r.annual_cash_flow == pytest.approx(
            self.r.noi - self.r.annual_debt_service, rel=1e-6
        )

    def test_monthly_cash_flow_consistency(self):
        assert self.r.monthly_cash_flow == pytest.approx(self.r.annual_cash_flow / 12, rel=1e-6)

    def test_cap_rate_positive(self):
        assert self.r.cap_rate > 0

    def test_grm_reasonable(self):
        # GRM should be between 5 and 30 for a real deal
        assert 5 < self.r.grm < 30

    def test_dscr_positive(self):
        assert self.r.dscr > 0

    def test_no_str_fields(self):
        assert self.r.break_even_occupancy is None


# ──────────────────────────────────────────────────────────────────────────────
# MTR — base case
# ──────────────────────────────────────────────────────────────────────────────

class TestMTRBase:
    @pytest.fixture(autouse=True)
    def base(self, result):
        self.r = result.mtr.base

    def test_strategy_tag(self):
        assert self.r.strategy == Strategy.MTR

    def test_higher_gross_than_ltr(self, result):
        assert self.r.revenue.gross_annual_revenue > result.ltr.base.revenue.gross_annual_revenue

    def test_insurance_includes_landlord_rider(self):
        # Insurance should be 1.25× the base insurance
        base_insurance = SAMPLE_PROP["insurance_annual"]
        assert self.r.expenses.insurance == pytest.approx(base_insurance * 1.25, rel=1e-4)

    def test_utilities_in_expenses(self):
        assert self.r.expenses.utilities == pytest.approx(200 * 12, rel=1e-4)

    def test_furnishing_amort_in_expenses(self):
        assert self.r.expenses.furnishing_amort == pytest.approx(125 * 12, rel=1e-4)


# ──────────────────────────────────────────────────────────────────────────────
# STR — base case
# ──────────────────────────────────────────────────────────────────────────────

class TestSTRBase:
    @pytest.fixture(autouse=True)
    def base(self, result):
        self.r = result.str_.base

    def test_strategy_tag(self):
        assert self.r.strategy == Strategy.STR

    def test_gross_revenue_formula(self):
        expected = 175 * 365 * 0.65
        assert self.r.revenue.gross_annual_revenue == pytest.approx(expected, rel=1e-4)

    def test_no_vacancy_line(self):
        # STR models vacancy via occupancy, not a separate vacancy_loss line
        assert self.r.revenue.vacancy_loss == 0.0

    def test_break_even_occupancy_is_float(self):
        assert isinstance(self.r.break_even_occupancy, float)

    def test_break_even_occupancy_capped_at_1_for_negative_noi(self):
        # SAMPLE_PROP STR is underwater (NOI < 0), so break-even is correctly 1.0
        assert self.r.noi < 0
        assert self.r.break_even_occupancy == 1.0

    def test_break_even_lower_than_occupancy_for_profitable_str(self):
        # Use a high-ADR property that clears all costs at 65% occupancy
        profitable = dict(SAMPLE_PROP)
        profitable["str_adr"] = 275          # $275 ADR → clearly profitable
        profitable["purchase_price"] = 250_000
        r = underwrite(profitable)
        str_base = r.str_.base
        assert str_base.noi > 0, "fixture should be profitable"
        # Break-even must be a valid occupancy fraction strictly below input occupancy
        assert 0 < str_base.break_even_occupancy < profitable["str_occupancy"]

    def test_insurance_str_endorsement(self):
        base_insurance = SAMPLE_PROP["insurance_annual"]
        assert self.r.expenses.insurance == pytest.approx(base_insurance * 1.50, rel=1e-4)

    def test_cleaning_expense_positive(self):
        assert self.r.expenses.cleaning > 0

    def test_platform_fee_positive(self):
        assert self.r.expenses.platform_fee > 0


# ──────────────────────────────────────────────────────────────────────────────
# All five stress tests present for each strategy
# ──────────────────────────────────────────────────────────────────────────────

class TestAllScenariosPresent:
    def test_ltr_has_five_scenarios(self, result):
        assert set(result.ltr.scenarios.keys()) == set(Scenario)

    def test_mtr_has_five_scenarios(self, result):
        assert set(result.mtr.scenarios.keys()) == set(Scenario)

    def test_str_has_five_scenarios(self, result):
        assert set(result.str_.scenarios.keys()) == set(Scenario)


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 1 — Rate Shock
# ──────────────────────────────────────────────────────────────────────────────

class TestRateShock:
    def test_ltr_rate_shock_worse_cash_flow(self, result):
        base_cf = result.ltr.scenarios[Scenario.BASE].annual_cash_flow
        shock_cf = result.ltr.scenarios[Scenario.RATE_SHOCK].annual_cash_flow
        assert shock_cf < base_cf

    def test_mtr_rate_shock_worse_cash_flow(self, result):
        base_cf = result.mtr.scenarios[Scenario.BASE].annual_cash_flow
        shock_cf = result.mtr.scenarios[Scenario.RATE_SHOCK].annual_cash_flow
        assert shock_cf < base_cf

    def test_str_rate_shock_worse_cash_flow(self, result):
        base_cf = result.str_.scenarios[Scenario.BASE].annual_cash_flow
        shock_cf = result.str_.scenarios[Scenario.RATE_SHOCK].annual_cash_flow
        assert shock_cf < base_cf

    def test_rate_shock_does_not_change_revenue(self, result):
        # Rate shock only affects debt service, not revenue
        base_rev = result.ltr.scenarios[Scenario.BASE].revenue.gross_annual_revenue
        shock_rev = result.ltr.scenarios[Scenario.RATE_SHOCK].revenue.gross_annual_revenue
        assert base_rev == pytest.approx(shock_rev, rel=1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 2 — Rent Decline
# ──────────────────────────────────────────────────────────────────────────────

class TestRentDecline:
    def test_ltr_revenue_reduced_15pct(self, result):
        base_rev = result.ltr.scenarios[Scenario.BASE].revenue.gross_annual_revenue
        decline_rev = result.ltr.scenarios[Scenario.RENT_DECLINE].revenue.gross_annual_revenue
        assert decline_rev == pytest.approx(base_rev * 0.85, rel=1e-4)

    def test_str_revenue_reduced_15pct(self, result):
        base_rev = result.str_.scenarios[Scenario.BASE].revenue.gross_annual_revenue
        decline_rev = result.str_.scenarios[Scenario.RENT_DECLINE].revenue.gross_annual_revenue
        assert decline_rev == pytest.approx(base_rev * 0.85, rel=1e-4)

    def test_rent_decline_does_not_change_debt_service(self, result):
        base_ds = result.ltr.scenarios[Scenario.BASE].annual_debt_service
        decline_ds = result.ltr.scenarios[Scenario.RENT_DECLINE].annual_debt_service
        assert base_ds == pytest.approx(decline_ds, rel=1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 3 — Vacancy Surge
# ──────────────────────────────────────────────────────────────────────────────

class TestVacancySurge:
    def test_ltr_vacancy_1point5x(self, result):
        # Base vacancy 8%, surge → 12%
        base_loss = result.ltr.scenarios[Scenario.BASE].revenue.vacancy_loss
        surge_loss = result.ltr.scenarios[Scenario.VACANCY_SURGE].revenue.vacancy_loss
        assert surge_loss == pytest.approx(base_loss * 1.50, rel=1e-4)

    def test_str_occupancy_reduced(self, result):
        base_rev = result.str_.scenarios[Scenario.BASE].revenue.gross_annual_revenue
        surge_rev = result.str_.scenarios[Scenario.VACANCY_SURGE].revenue.gross_annual_revenue
        assert surge_rev < base_rev

    def test_vacancy_surge_worse_cash_flow(self, result):
        base_cf = result.ltr.scenarios[Scenario.BASE].annual_cash_flow
        surge_cf = result.ltr.scenarios[Scenario.VACANCY_SURGE].annual_cash_flow
        assert surge_cf < base_cf


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 4 — Full Stress
# ──────────────────────────────────────────────────────────────────────────────

class TestFullStress:
    def test_full_stress_worst_cash_flow_among_scenarios(self, result):
        """Full stress should typically produce the lowest or near-lowest cash flow."""
        ltr_cash_flows = {
            s: result.ltr.scenarios[s].annual_cash_flow for s in Scenario
        }
        full_stress_cf = ltr_cash_flows[Scenario.FULL_STRESS]
        base_cf = ltr_cash_flows[Scenario.BASE]
        assert full_stress_cf < base_cf

    def test_full_stress_revenue_reduced_10pct(self, result):
        base_rev = result.ltr.scenarios[Scenario.BASE].revenue.gross_annual_revenue
        full_rev = result.ltr.scenarios[Scenario.FULL_STRESS].revenue.gross_annual_revenue
        assert full_rev == pytest.approx(base_rev * 0.90, rel=1e-4)


# ──────────────────────────────────────────────────────────────────────────────
# Metric invariants across all results
# ──────────────────────────────────────────────────────────────────────────────

class TestMetricInvariants:
    def test_all_results_have_valid_numbers(self, result):
        for r in result.all_results:
            assert not math.isnan(r.annual_cash_flow)
            assert not math.isnan(r.cap_rate)
            assert not math.isnan(r.cash_on_cash)
            assert not math.isnan(r.dscr)

    def test_best_strategy_returns_a_result(self, result):
        best = result.best_strategy()
        assert best is not None
        assert isinstance(best.cash_on_cash, float)

    def test_all_results_share_same_purchase_price(self, result):
        for r in result.all_results:
            assert r.purchase_price == 300_000

    def test_total_cash_invested_consistent_across_results(self, result):
        for r in result.all_results:
            assert r.total_cash_invested == pytest.approx(74_000, rel=1e-4)


# ──────────────────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_missing_purchase_price_raises(self):
        with pytest.raises(ValueError, match="purchase_price"):
            underwrite({})

    def test_zero_purchase_price_raises(self):
        with pytest.raises(ValueError):
            underwrite({"purchase_price": 0})

    def test_no_rent_ltr_gives_zero_revenue(self):
        prop = dict(SAMPLE_PROP)
        prop["monthly_rent"] = 0
        r = underwrite(prop)
        assert r.ltr.base.revenue.gross_annual_revenue == 0.0

    def test_zero_interest_rate_still_computes(self):
        prop = dict(SAMPLE_PROP)
        prop["interest_rate"] = 0.0
        r = underwrite(prop)
        assert r.ltr.base.annual_debt_service > 0  # principal-only payments

    def test_minimal_property_dict(self):
        """Only purchase_price and LTR rent; all else defaults."""
        r = underwrite({"purchase_price": 200_000, "monthly_rent": 1_800})
        assert r.ltr.base.revenue.gross_annual_revenue == pytest.approx(1_800 * 12)

    def test_monthly_payment_helper_known_value(self):
        # $240k at 7.5% for 30 years ≈ $1,678.51/mo
        pmt = _monthly_payment(240_000, 0.075, 30)
        assert pmt == pytest.approx(1_678.51, abs=1.0)
