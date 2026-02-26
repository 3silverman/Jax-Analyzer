"""
Tests for underwriting/calculator.py

Covers:
- VA loan capital stack
- LTR / MTR / STR base-case math
- All six scenarios present per strategy
- Each stress test modifies only its target axis
- Cross-scenario and cross-strategy invariants
- Edge cases

Run:  pytest underwriting/tests/test_calculator.py -v
"""

import math
import pytest

from underwriting.calculator import (
    CashFlowResult,
    Scenario,
    Strategy,
    UnderwritingResult,
    _monthly_payment,
    underwrite,
)

# ──────────────────────────────────────────────────────────────────────────────
# Shared fixture — a realistic Jacksonville duplex
# ──────────────────────────────────────────────────────────────────────────────

SAMPLE_PROP = {
    # Acquisition
    "purchase_price":       380_000,
    "va_funding_fee_pct":   0.0215,
    "interest_rate":        0.075,
    "loan_term_years":      30,
    "closing_costs_pct":    0.03,
    "rehab_cost":           5_000,
    "num_units":            2,

    # LTR — $1,350/unit × 2
    "monthly_rent":         2_700,
    "ltr_vacancy_rate":     0.08,
    "ltr_mgmt_rate":        0.08,
    "ltr_maintenance_rate": 0.01,
    "ltr_capex_rate":       0.005,

    # MTR — $2,100/unit × 2 furnished
    "mtr_monthly_rate":         4_200,
    "mtr_vacancy_rate":         0.10,
    "mtr_mgmt_rate":            0.10,
    "mtr_utilities_per_unit":   150.0,
    "mtr_furnishing_per_unit":  50.0,
    "mtr_avg_stay_months":      3.0,
    "mtr_turnover_cost":        200.0,

    # STR — $165 ADR, 75% occupancy (25% vacancy)
    "str_adr":               165.0,
    "str_vacancy_rate":      0.25,
    "str_platform_fee_pct":  0.15,
    "str_cleaning_per_turn": 125.0,
    "str_avg_stay_nights":   3.5,
    "str_maintenance_rate":  0.015,
    "str_capex_rate":        0.005,

    # Shared opex
    "property_tax_annual":  2_926,      # 0.77% × 380k
    "insurance_annual":     1_900,      # 0.50% × 380k
    "hoa_monthly":          0,
}


@pytest.fixture
def result() -> UnderwritingResult:
    return underwrite(SAMPLE_PROP)


# ──────────────────────────────────────────────────────────────────────────────
# VA Loan capital stack
# ──────────────────────────────────────────────────────────────────────────────

class TestVALoanCapitalStack:
    def test_funding_fee_rolled_into_loan(self, result):
        # loan = purchase_price + 2.15% funding fee
        expected_loan = 380_000 * (1 + 0.0215)
        assert result.loan_amount == pytest.approx(expected_loan, rel=1e-4)

    def test_no_down_payment(self, result):
        # total_cash_invested = closing costs + rehab only
        expected = 380_000 * 0.03 + 5_000
        assert result.total_cash_invested == pytest.approx(expected, rel=1e-4)

    def test_loan_greater_than_purchase_price(self, result):
        assert result.loan_amount > result.purchase_price

    def test_monthly_payment_positive(self, result):
        assert result.monthly_payment > 0

    def test_annual_debt_service_12x_monthly(self, result):
        assert result.annual_debt_service == pytest.approx(result.monthly_payment * 12, rel=1e-6)

    def test_monthly_payment_known_value(self):
        # $387,170 at 7.5% for 30 years ≈ roughly knowable
        loan = 380_000 * 1.0215
        pmt  = _monthly_payment(loan, 0.075, 30)
        assert pmt == pytest.approx(loan * 0.006992, rel=0.002)   # ~0.699% of balance/mo


# ──────────────────────────────────────────────────────────────────────────────
# LTR base case
# ──────────────────────────────────────────────────────────────────────────────

class TestLTRBase:
    @pytest.fixture(autouse=True)
    def setup(self, result):
        self.r = result.ltr.base

    def test_strategy_tag(self):
        assert self.r.strategy == Strategy.LTR

    def test_scenario_tag(self):
        assert self.r.scenario == Scenario.BASE

    def test_gross_revenue(self):
        assert self.r.revenue.gross_annual_revenue == pytest.approx(2_700 * 12, rel=1e-4)

    def test_vacancy_loss_8pct(self):
        assert self.r.revenue.vacancy_loss == pytest.approx(2_700 * 12 * 0.08, rel=1e-4)

    def test_egi_equals_gross_minus_vacancy(self):
        rev = self.r.revenue
        assert rev.effective_gross_income == pytest.approx(
            rev.gross_annual_revenue - rev.vacancy_loss, rel=1e-6
        )

    def test_noi_positive(self):
        # A viable LTR deal should have positive NOI
        assert self.r.noi > 0

    def test_cash_flow_equals_noi_minus_debt(self):
        assert self.r.annual_cash_flow == pytest.approx(
            self.r.noi - self.r.annual_debt_service, rel=1e-6
        )

    def test_monthly_cf_consistency(self):
        assert self.r.monthly_cash_flow == pytest.approx(self.r.annual_cash_flow / 12, rel=1e-6)

    def test_cap_rate_positive(self):
        assert self.r.cap_rate > 0

    def test_grm_in_reasonable_range(self):
        assert 5 < self.r.grm < 30

    def test_property_tax_uses_duval_rate(self):
        assert self.r.expenses.property_tax == pytest.approx(2_926, rel=1e-4)

    def test_no_str_fields(self):
        assert self.r.break_even_occupancy is None
        assert self.r.seasonality_flag is False


# ──────────────────────────────────────────────────────────────────────────────
# MTR base case
# ──────────────────────────────────────────────────────────────────────────────

class TestMTRBase:
    @pytest.fixture(autouse=True)
    def setup(self, result):
        self.r = result.mtr.base

    def test_strategy_tag(self):
        assert self.r.strategy == Strategy.MTR

    def test_gross_revenue(self):
        assert self.r.revenue.gross_annual_revenue == pytest.approx(4_200 * 12, rel=1e-4)

    def test_vacancy_10pct(self):
        assert self.r.revenue.vacancy_loss == pytest.approx(4_200 * 12 * 0.10, rel=1e-4)

    def test_utilities_per_unit(self):
        # 2 units × $150/mo × 12
        assert self.r.expenses.utilities == pytest.approx(2 * 150 * 12, rel=1e-4)

    def test_furnishing_amort_per_unit(self):
        # 2 units × $50/mo × 12
        assert self.r.expenses.furnishing_amort == pytest.approx(2 * 50 * 12, rel=1e-4)

    def test_turnover_cost(self):
        # 2 units × (12 mo / 3 mo avg stay) × $200 = 2 × 4 × $200 = $1,600
        assert self.r.expenses.turnover == pytest.approx(2 * 4 * 200, rel=1e-4)

    def test_higher_gross_than_ltr(self, result):
        assert self.r.revenue.gross_annual_revenue > result.ltr.base.revenue.gross_annual_revenue


# ──────────────────────────────────────────────────────────────────────────────
# STR base case
# ──────────────────────────────────────────────────────────────────────────────

class TestSTRBase:
    @pytest.fixture(autouse=True)
    def setup(self, result):
        self.r = result.str_.base

    def test_strategy_tag(self):
        assert self.r.strategy == Strategy.STR

    def test_gross_revenue_adr_times_occupied_nights(self):
        # 75% occupancy = 365 × 0.75 = 273.75 nights
        expected = 165.0 * 365 * 0.75
        assert self.r.revenue.gross_annual_revenue == pytest.approx(expected, rel=1e-4)

    def test_no_vacancy_line(self):
        assert self.r.revenue.vacancy_loss == 0.0

    def test_platform_fee_15pct(self):
        gross = 165.0 * 365 * 0.75
        assert self.r.expenses.platform_fee == pytest.approx(gross * 0.15, rel=1e-4)

    def test_cleaning_cost(self):
        occupied_nights  = 365 * 0.75
        turns_per_year   = occupied_nights / 3.5
        expected_cleaning = turns_per_year * 125.0
        assert self.r.expenses.cleaning == pytest.approx(expected_cleaning, rel=1e-4)

    def test_maintenance_1point5pct(self):
        assert self.r.expenses.maintenance == pytest.approx(380_000 * 0.015, rel=1e-4)

    def test_seasonality_flag_set(self):
        assert self.r.seasonality_flag is True

    def test_break_even_occupancy_is_float(self):
        assert isinstance(self.r.break_even_occupancy, float)

    def test_break_even_between_0_and_1(self):
        # At $165 ADR, 75% occupancy the deal may or may not cash-flow;
        # break-even must be a valid probability ≤ 1.0
        assert 0 < self.r.break_even_occupancy <= 1.0

    def test_break_even_below_occupancy_when_profitable_str(self):
        profitable = {**SAMPLE_PROP, "str_adr": 260, "purchase_price": 280_000,
                      "property_tax_annual": 2_156, "insurance_annual": 1_400}
        r = underwrite(profitable)
        base = r.str_.base
        assert base.noi > 0, "fixture must be profitable"
        assert base.break_even_occupancy < (1 - profitable["str_vacancy_rate"])


# ──────────────────────────────────────────────────────────────────────────────
# All six scenarios present for each strategy
# ──────────────────────────────────────────────────────────────────────────────

class TestAllScenariosPresent:
    def test_ltr_six_scenarios(self, result):
        assert set(result.ltr.scenarios.keys()) == set(Scenario)

    def test_mtr_six_scenarios(self, result):
        assert set(result.mtr.scenarios.keys()) == set(Scenario)

    def test_str_six_scenarios(self, result):
        assert set(result.str_.scenarios.keys()) == set(Scenario)


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 1 — Rate Shock (+1%)
# ──────────────────────────────────────────────────────────────────────────────

class TestStressRate:
    def test_higher_debt_service(self, result):
        base_ds   = result.ltr.scenarios[Scenario.BASE].annual_debt_service
        stress_ds = result.ltr.scenarios[Scenario.STRESS_RATE].annual_debt_service
        assert stress_ds > base_ds

    def test_worse_cash_flow_all_strategies(self, result):
        for strat in (result.ltr, result.mtr, result.str_):
            assert (strat.scenarios[Scenario.STRESS_RATE].annual_cash_flow
                    < strat.scenarios[Scenario.BASE].annual_cash_flow)

    def test_revenue_unchanged(self, result):
        base_rev   = result.ltr.scenarios[Scenario.BASE].revenue.gross_annual_revenue
        stress_rev = result.ltr.scenarios[Scenario.STRESS_RATE].revenue.gross_annual_revenue
        assert base_rev == pytest.approx(stress_rev, rel=1e-6)

    def test_opex_unchanged(self, result):
        base_opex   = result.ltr.scenarios[Scenario.BASE].expenses.operating_total
        stress_opex = result.ltr.scenarios[Scenario.STRESS_RATE].expenses.operating_total
        assert base_opex == pytest.approx(stress_opex, rel=1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 2 — Insurance Spike (+50%)
# ──────────────────────────────────────────────────────────────────────────────

class TestStressInsurance:
    def test_insurance_multiplied_1point5x(self, result):
        base_ins   = result.ltr.scenarios[Scenario.BASE].expenses.insurance
        stress_ins = result.ltr.scenarios[Scenario.STRESS_INSURANCE].expenses.insurance
        assert stress_ins == pytest.approx(base_ins * 1.50, rel=1e-4)

    def test_worse_noi(self, result):
        assert (result.ltr.scenarios[Scenario.STRESS_INSURANCE].noi
                < result.ltr.scenarios[Scenario.BASE].noi)

    def test_debt_service_unchanged(self, result):
        base_ds   = result.ltr.scenarios[Scenario.BASE].annual_debt_service
        stress_ds = result.ltr.scenarios[Scenario.STRESS_INSURANCE].annual_debt_service
        assert base_ds == pytest.approx(stress_ds, rel=1e-6)

    def test_revenue_unchanged(self, result):
        base_rev   = result.ltr.scenarios[Scenario.BASE].revenue.gross_annual_revenue
        stress_rev = result.ltr.scenarios[Scenario.STRESS_INSURANCE].revenue.gross_annual_revenue
        assert base_rev == pytest.approx(stress_rev, rel=1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 3 — Vacancy Shock (+20 pp)
# ──────────────────────────────────────────────────────────────────────────────

class TestStressVacancy:
    def test_ltr_vacancy_increases_by_20pp(self, result):
        base_vac   = result.ltr.scenarios[Scenario.BASE].revenue.vacancy_loss
        stress_vac = result.ltr.scenarios[Scenario.STRESS_VACANCY].revenue.vacancy_loss
        # At gross = 2700×12 = 32400: base_vac = 32400×0.08, stress = 32400×0.28
        gross = 2_700 * 12
        assert base_vac   == pytest.approx(gross * 0.08, rel=1e-4)
        assert stress_vac == pytest.approx(gross * 0.28, rel=1e-4)

    def test_str_occupancy_drops_20pp(self, result):
        # STR base: 75% occ (25% vacancy); stress: 55% occ (45% vacancy)
        base_gross   = result.str_.scenarios[Scenario.BASE].revenue.gross_annual_revenue
        stress_gross = result.str_.scenarios[Scenario.STRESS_VACANCY].revenue.gross_annual_revenue
        expected_base   = 165.0 * 365 * 0.75
        expected_stress = 165.0 * 365 * 0.55
        assert base_gross   == pytest.approx(expected_base,   rel=1e-4)
        assert stress_gross == pytest.approx(expected_stress, rel=1e-4)

    def test_worse_cash_flow_all_strategies(self, result):
        for strat in (result.ltr, result.mtr, result.str_):
            assert (strat.scenarios[Scenario.STRESS_VACANCY].annual_cash_flow
                    < strat.scenarios[Scenario.BASE].annual_cash_flow)

    def test_debt_service_unchanged(self, result):
        base_ds   = result.ltr.scenarios[Scenario.BASE].annual_debt_service
        stress_ds = result.ltr.scenarios[Scenario.STRESS_VACANCY].annual_debt_service
        assert base_ds == pytest.approx(stress_ds, rel=1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 4 — Tax Reassessment (+25%)
# ──────────────────────────────────────────────────────────────────────────────

class TestStressTax:
    def test_tax_multiplied_1point25x(self, result):
        base_tax   = result.ltr.scenarios[Scenario.BASE].expenses.property_tax
        stress_tax = result.ltr.scenarios[Scenario.STRESS_TAX].expenses.property_tax
        assert stress_tax == pytest.approx(base_tax * 1.25, rel=1e-4)

    def test_worse_noi(self, result):
        assert (result.ltr.scenarios[Scenario.STRESS_TAX].noi
                < result.ltr.scenarios[Scenario.BASE].noi)

    def test_revenue_and_debt_service_unchanged(self, result):
        for field_ in ("gross_annual_revenue",):
            base = getattr(result.ltr.scenarios[Scenario.BASE].revenue, field_)
            stress = getattr(result.ltr.scenarios[Scenario.STRESS_TAX].revenue, field_)
            assert base == pytest.approx(stress, rel=1e-6)
        base_ds   = result.ltr.scenarios[Scenario.BASE].annual_debt_service
        stress_ds = result.ltr.scenarios[Scenario.STRESS_TAX].annual_debt_service
        assert base_ds == pytest.approx(stress_ds, rel=1e-6)

    def test_insurance_unchanged(self, result):
        base_ins   = result.ltr.scenarios[Scenario.BASE].expenses.insurance
        stress_ins = result.ltr.scenarios[Scenario.STRESS_TAX].expenses.insurance
        assert base_ins == pytest.approx(stress_ins, rel=1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# Stress test 5 — Year-1 Major Repair ($8,000)
# ──────────────────────────────────────────────────────────────────────────────

class TestStressRepair:
    def test_cash_flow_reduced_by_8000(self, result):
        base_cf   = result.ltr.scenarios[Scenario.BASE].annual_cash_flow
        stress_cf = result.ltr.scenarios[Scenario.STRESS_REPAIR].annual_cash_flow
        assert base_cf - stress_cf == pytest.approx(8_000, abs=1.0)

    def test_noi_unchanged(self, result):
        # Major repair hits cash flow, not NOI (it's a capital event, not operating expense)
        base_noi   = result.ltr.scenarios[Scenario.BASE].noi
        stress_noi = result.ltr.scenarios[Scenario.STRESS_REPAIR].noi
        assert base_noi == pytest.approx(stress_noi, rel=1e-6)

    def test_debt_service_unchanged(self, result):
        base_ds   = result.ltr.scenarios[Scenario.BASE].annual_debt_service
        stress_ds = result.ltr.scenarios[Scenario.STRESS_REPAIR].annual_debt_service
        assert base_ds == pytest.approx(stress_ds, rel=1e-6)

    def test_one_time_repair_stored_in_expenses(self, result):
        assert result.ltr.scenarios[Scenario.STRESS_REPAIR].expenses.one_time_repair == 8_000.0

    def test_repair_same_for_all_strategies(self, result):
        for strat in (result.ltr, result.mtr, result.str_):
            base_cf   = strat.scenarios[Scenario.BASE].annual_cash_flow
            stress_cf = strat.scenarios[Scenario.STRESS_REPAIR].annual_cash_flow
            assert base_cf - stress_cf == pytest.approx(8_000, abs=1.0)


# ──────────────────────────────────────────────────────────────────────────────
# Stress tests are axis-isolated (one thing changes per test)
# ──────────────────────────────────────────────────────────────────────────────

class TestStressIsolation:
    """Each stress test should change ONLY its target axis."""

    def test_rate_stress_does_not_change_insurance(self, result):
        b = result.ltr.scenarios[Scenario.BASE].expenses.insurance
        s = result.ltr.scenarios[Scenario.STRESS_RATE].expenses.insurance
        assert b == pytest.approx(s, rel=1e-6)

    def test_insurance_stress_does_not_change_tax(self, result):
        b = result.ltr.scenarios[Scenario.BASE].expenses.property_tax
        s = result.ltr.scenarios[Scenario.STRESS_INSURANCE].expenses.property_tax
        assert b == pytest.approx(s, rel=1e-6)

    def test_tax_stress_does_not_change_vacancy(self, result):
        b = result.ltr.scenarios[Scenario.BASE].revenue.vacancy_loss
        s = result.ltr.scenarios[Scenario.STRESS_TAX].revenue.vacancy_loss
        assert b == pytest.approx(s, rel=1e-6)

    def test_vacancy_stress_does_not_change_insurance(self, result):
        b = result.ltr.scenarios[Scenario.BASE].expenses.insurance
        s = result.ltr.scenarios[Scenario.STRESS_VACANCY].expenses.insurance
        assert b == pytest.approx(s, rel=1e-6)

    def test_repair_stress_does_not_change_noi(self, result):
        b = result.ltr.scenarios[Scenario.BASE].noi
        s = result.ltr.scenarios[Scenario.STRESS_REPAIR].noi
        assert b == pytest.approx(s, rel=1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# Metric invariants across all 18 results (3 strategies × 6 scenarios)
# ──────────────────────────────────────────────────────────────────────────────

class TestMetricInvariants:
    def test_no_nan_in_any_result(self, result):
        for r in result.all_results:
            for attr in ("annual_cash_flow", "noi", "cap_rate", "cash_on_cash", "dscr", "grm"):
                assert not math.isnan(getattr(r, attr)), f"{r.strategy}/{r.scenario}: {attr} is NaN"

    def test_purchase_price_consistent(self, result):
        for r in result.all_results:
            assert r.purchase_price == 380_000

    def test_total_cash_invested_consistent(self, result):
        expected = 380_000 * 0.03 + 5_000
        for r in result.all_results:
            assert r.total_cash_invested == pytest.approx(expected, rel=1e-4)

    def test_monthly_equals_annual_divided_12(self, result):
        for r in result.all_results:
            assert r.monthly_cash_flow == pytest.approx(r.annual_cash_flow / 12, rel=1e-6)

    def test_dscr_equals_noi_over_debt(self, result):
        for r in result.all_results:
            if r.annual_debt_service > 0:
                assert r.dscr == pytest.approx(r.noi / r.annual_debt_service, rel=1e-6)

    def test_best_strategy_returns_a_result(self, result):
        best = result.best_strategy()
        assert isinstance(best, CashFlowResult)

    def test_worst_case_leq_base(self, result):
        for strat in (result.ltr, result.mtr, result.str_):
            assert strat.worst_case_cash_flow <= strat.base.monthly_cash_flow


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

    def test_zero_rent_gives_zero_ltr_revenue(self):
        r = underwrite({**SAMPLE_PROP, "monthly_rent": 0})
        assert r.ltr.base.revenue.gross_annual_revenue == 0.0

    def test_zero_interest_rate_computes_principal_only(self):
        r = underwrite({**SAMPLE_PROP, "interest_rate": 0.0})
        # P&I should be loan / (30×12)
        expected_monthly = r.loan_amount / 360
        assert r.monthly_payment == pytest.approx(expected_monthly, rel=1e-4)

    def test_minimal_dict_uses_all_defaults(self):
        r = underwrite({"purchase_price": 300_000, "monthly_rent": 2_000})
        assert r.ltr.base.revenue.gross_annual_revenue == pytest.approx(2_000 * 12)
        # VA defaults: 2.15% funding fee
        assert r.loan_amount == pytest.approx(300_000 * 1.0215, rel=1e-4)
        # Duval County tax default
        assert r.ltr.base.expenses.property_tax == pytest.approx(300_000 * 0.0077, rel=1e-4)

    def test_subsequent_va_use_funding_fee(self):
        r = underwrite({"purchase_price": 300_000, "va_funding_fee_pct": 0.033, "monthly_rent": 2_000})
        assert r.loan_amount == pytest.approx(300_000 * 1.033, rel=1e-4)

    def test_vacancy_capped_at_95pct_under_shock(self):
        # Base vacancy 80% + 20pp shock = 100%, should cap at 95%
        r = underwrite({**SAMPLE_PROP, "ltr_vacancy_rate": 0.80})
        stress_vac = r.ltr.scenarios[Scenario.STRESS_VACANCY].revenue.vacancy_loss
        gross      = 2_700 * 12
        assert stress_vac == pytest.approx(gross * 0.95, rel=1e-4)

    def test_monthly_payment_helper_known_value(self):
        # $240k at 7.5% for 30 years ≈ $1,678.51/mo
        pmt = _monthly_payment(240_000, 0.075, 30)
        assert pmt == pytest.approx(1_678.51, abs=1.0)
