"""
underwriting/calculator.py

Pure math underwriting engine — zero API calls, zero DB access.

Entry point:
    result = underwrite(property_dict)

Returns an UnderwritingResult containing cash-flow analysis for LTR, MTR, and
STR strategies, each evaluated across five stress-test scenarios.

Property dict keys (all optional with sensible defaults):
    # Acquisition
    purchase_price          float   required
    down_payment_pct        float   default 0.20
    interest_rate           float   default 0.075
    loan_term_years         int     default 30
    closing_costs_pct       float   default 0.03
    rehab_cost              float   default 0.0

    # LTR — Long-Term Rental
    monthly_rent            float   required for LTR
    vacancy_rate            float   default 0.08
    property_mgmt_rate      float   default 0.10

    # MTR — Medium-Term Rental (furnished, 1-6 month stays)
    mtr_monthly_rate        float   required for MTR (furnished monthly rent)
    mtr_vacancy_rate        float   default 0.15
    mtr_mgmt_rate           float   default 0.15
    mtr_utilities_monthly   float   default 200.0
    mtr_furnishing_monthly  float   default 125.0

    # STR — Short-Term Rental (Airbnb / VRBO)
    str_adr                 float   required for STR (average daily rate)
    str_occupancy           float   default 0.65
    str_platform_fee_pct    float   default 0.03
    str_cleaning_per_stay   float   default 150.0
    str_avg_stay_nights     float   default 3.5
    str_mgmt_rate           float   default 0.25
    str_utilities_monthly   float   default 275.0
    str_furnishing_monthly  float   default 200.0

    # Operating expenses (shared)
    property_tax_annual     float   default 1.1% of purchase_price
    insurance_annual        float   default 0.5% of purchase_price
    hoa_monthly             float   default 0.0
    maintenance_rate        float   default 0.01  (% of purchase_price)
    capex_rate              float   default 0.01  (% of purchase_price)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Enums & constants
# ──────────────────────────────────────────────────────────────────────────────

class Strategy(str, Enum):
    LTR = "LTR"
    MTR = "MTR"
    STR = "STR"


class Scenario(str, Enum):
    BASE = "base"
    RATE_SHOCK = "rate_shock"           # +200 bps on interest rate
    RENT_DECLINE = "rent_decline"       # gross revenue −15 %
    VACANCY_SURGE = "vacancy_surge"     # vacancy / occupancy stressed +50 %
    FULL_STRESS = "full_stress"         # rate +100 bps + revenue −10 % + vacancy +5 pts


SCENARIO_LABELS = {
    Scenario.BASE: "Base Case",
    Scenario.RATE_SHOCK: "Rate Shock (+200 bps)",
    Scenario.RENT_DECLINE: "Rent Decline (−15%)",
    Scenario.VACANCY_SURGE: "Vacancy Surge (+50% of vacancy rate)",
    Scenario.FULL_STRESS: "Full Stress (rate +100 bps, revenue −10%, vacancy +5 pts)",
}


# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ExpenseDetail:
    property_tax: float
    insurance: float
    hoa: float
    maintenance: float
    capex: float
    property_mgmt: float
    vacancy_loss: float
    utilities: float = 0.0
    furnishing_amort: float = 0.0
    platform_fee: float = 0.0
    cleaning: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.property_tax
            + self.insurance
            + self.hoa
            + self.maintenance
            + self.capex
            + self.property_mgmt
            + self.vacancy_loss
            + self.utilities
            + self.furnishing_amort
            + self.platform_fee
            + self.cleaning
        )


@dataclass
class RevenueDetail:
    gross_annual_revenue: float
    vacancy_loss: float
    effective_gross_income: float


@dataclass
class CashFlowResult:
    strategy: Strategy
    scenario: Scenario
    scenario_label: str

    # Capital stack
    purchase_price: float
    loan_amount: float
    total_cash_invested: float   # down payment + closing costs + rehab

    # Revenue
    revenue: RevenueDetail

    # Expenses
    expenses: ExpenseDetail

    # P&L
    noi: float                   # NOI = EGI − operating expenses (excl. debt service)
    annual_debt_service: float
    annual_cash_flow: float
    monthly_cash_flow: float

    # Return metrics
    cap_rate: float              # NOI / purchase_price
    cash_on_cash: float          # annual_cash_flow / total_cash_invested
    grm: float                   # purchase_price / gross_annual_revenue
    dscr: float                  # NOI / annual_debt_service (0 if no debt)

    # STR-specific (None for LTR/MTR)
    break_even_occupancy: float | None = None


@dataclass
class StrategyResult:
    strategy: Strategy
    scenarios: dict[Scenario, CashFlowResult] = field(default_factory=dict)

    @property
    def base(self) -> CashFlowResult:
        return self.scenarios[Scenario.BASE]


@dataclass
class UnderwritingResult:
    purchase_price: float
    total_cash_invested: float
    loan_amount: float
    monthly_payment: float
    annual_debt_service: float

    ltr: StrategyResult
    mtr: StrategyResult
    str_: StrategyResult    # str is a built-in; use str_

    @property
    def all_results(self) -> list[CashFlowResult]:
        results = []
        for strat in (self.ltr, self.mtr, self.str_):
            results.extend(strat.scenarios.values())
        return results

    def best_strategy(self, scenario: Scenario = Scenario.BASE) -> CashFlowResult:
        candidates = [
            self.ltr.scenarios[scenario],
            self.mtr.scenarios[scenario],
            self.str_.scenarios[scenario],
        ]
        return max(candidates, key=lambda r: r.cash_on_cash)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    """Standard amortising mortgage payment (P&I)."""
    if annual_rate == 0:
        return principal / (years * 12)
    r = annual_rate / 12
    n = years * 12
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    return numerator / denominator


def _get(prop: dict[str, Any], key: str, default: Any) -> Any:
    val = prop.get(key)
    return default if (val is None or (isinstance(val, float) and math.isnan(val))) else val


# ──────────────────────────────────────────────────────────────────────────────
# Scenario modifiers
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class _ScenarioMod:
    rate_delta: float = 0.0        # additive bps expressed as decimal (0.02 = +200 bps)
    revenue_mult: float = 1.0      # multiplier applied to gross revenue
    vacancy_delta: float = 0.0     # additive delta to vacancy rate / occupancy haircut


_SCENARIO_MODS: dict[Scenario, _ScenarioMod] = {
    Scenario.BASE:          _ScenarioMod(),
    Scenario.RATE_SHOCK:    _ScenarioMod(rate_delta=0.02),
    Scenario.RENT_DECLINE:  _ScenarioMod(revenue_mult=0.85),
    Scenario.VACANCY_SURGE: _ScenarioMod(vacancy_delta=0.50),   # vacancy rate × 1.5
    Scenario.FULL_STRESS:   _ScenarioMod(rate_delta=0.01, revenue_mult=0.90, vacancy_delta=0.05),
}


# ──────────────────────────────────────────────────────────────────────────────
# Per-strategy calculators
# ──────────────────────────────────────────────────────────────────────────────

def _calc_ltr(
    prop: dict[str, Any],
    purchase_price: float,
    loan_amount: float,
    total_cash_invested: float,
    base_rate: float,
    loan_term: int,
    shared_opex: dict[str, float],
    scenario: Scenario,
) -> CashFlowResult:
    mod = _SCENARIO_MODS[scenario]
    rate = base_rate + mod.rate_delta

    monthly_rent = _get(prop, "monthly_rent", 0.0) * mod.revenue_mult
    base_vacancy = _get(prop, "vacancy_rate", 0.08)
    # Vacancy surge: multiply vacancy rate by 1.5, then add fixed delta
    vacancy_rate = min(base_vacancy * (1 + mod.vacancy_delta) + mod.vacancy_delta * 0.0, 0.50)
    # Simpler: for VACANCY_SURGE delta=0.50 means vacancy rate × 1.5 (50 % increase)
    if scenario == Scenario.VACANCY_SURGE:
        vacancy_rate = min(base_vacancy * 1.50, 0.50)
    elif scenario == Scenario.FULL_STRESS:
        vacancy_rate = min(base_vacancy + 0.05, 0.50)

    mgmt_rate = _get(prop, "property_mgmt_rate", 0.10)

    gross_annual = monthly_rent * 12
    vacancy_loss = gross_annual * vacancy_rate
    egi = gross_annual - vacancy_loss

    mgmt_fee = egi * mgmt_rate

    expenses = ExpenseDetail(
        property_tax=shared_opex["property_tax"],
        insurance=shared_opex["insurance"],
        hoa=shared_opex["hoa"],
        maintenance=shared_opex["maintenance"],
        capex=shared_opex["capex"],
        property_mgmt=mgmt_fee,
        vacancy_loss=vacancy_loss,
    )

    noi = egi - (expenses.total - vacancy_loss)  # vacancy already out of EGI
    # Re-derive correctly: NOI = EGI − cash operating expenses (excl. vacancy since EGI is net of vacancy)
    cash_opex = (
        expenses.property_tax
        + expenses.insurance
        + expenses.hoa
        + expenses.maintenance
        + expenses.capex
        + expenses.property_mgmt
    )
    noi = egi - cash_opex

    pmt = _monthly_payment(loan_amount, rate, loan_term)
    annual_ds = pmt * 12
    annual_cf = noi - annual_ds
    monthly_cf = annual_cf / 12

    return CashFlowResult(
        strategy=Strategy.LTR,
        scenario=scenario,
        scenario_label=SCENARIO_LABELS[scenario],
        purchase_price=purchase_price,
        loan_amount=loan_amount,
        total_cash_invested=total_cash_invested,
        revenue=RevenueDetail(
            gross_annual_revenue=gross_annual,
            vacancy_loss=vacancy_loss,
            effective_gross_income=egi,
        ),
        expenses=expenses,
        noi=noi,
        annual_debt_service=annual_ds,
        annual_cash_flow=annual_cf,
        monthly_cash_flow=monthly_cf,
        cap_rate=_safe_divide(noi, purchase_price),
        cash_on_cash=_safe_divide(annual_cf, total_cash_invested),
        grm=_safe_divide(purchase_price, gross_annual),
        dscr=_safe_divide(noi, annual_ds),
        break_even_occupancy=None,
    )


def _calc_mtr(
    prop: dict[str, Any],
    purchase_price: float,
    loan_amount: float,
    total_cash_invested: float,
    base_rate: float,
    loan_term: int,
    shared_opex: dict[str, float],
    scenario: Scenario,
) -> CashFlowResult:
    mod = _SCENARIO_MODS[scenario]
    rate = base_rate + mod.rate_delta

    mtr_monthly = _get(prop, "mtr_monthly_rate", 0.0) * mod.revenue_mult
    base_vacancy = _get(prop, "mtr_vacancy_rate", 0.15)
    if scenario == Scenario.VACANCY_SURGE:
        vacancy_rate = min(base_vacancy * 1.50, 0.60)
    elif scenario == Scenario.FULL_STRESS:
        vacancy_rate = min(base_vacancy + 0.05, 0.60)
    else:
        vacancy_rate = base_vacancy

    mgmt_rate = _get(prop, "mtr_mgmt_rate", 0.15)
    utilities_monthly = _get(prop, "mtr_utilities_monthly", 200.0)
    furnishing_monthly = _get(prop, "mtr_furnishing_monthly", 125.0)

    gross_annual = mtr_monthly * 12
    vacancy_loss = gross_annual * vacancy_rate
    egi = gross_annual - vacancy_loss

    mgmt_fee = egi * mgmt_rate

    expenses = ExpenseDetail(
        property_tax=shared_opex["property_tax"],
        insurance=shared_opex["insurance"] * 1.25,  # landlord rider
        hoa=shared_opex["hoa"],
        maintenance=shared_opex["maintenance"],
        capex=shared_opex["capex"],
        property_mgmt=mgmt_fee,
        vacancy_loss=vacancy_loss,
        utilities=utilities_monthly * 12,
        furnishing_amort=furnishing_monthly * 12,
    )

    cash_opex = (
        expenses.property_tax
        + expenses.insurance
        + expenses.hoa
        + expenses.maintenance
        + expenses.capex
        + expenses.property_mgmt
        + expenses.utilities
        + expenses.furnishing_amort
    )
    noi = egi - cash_opex

    pmt = _monthly_payment(loan_amount, rate, loan_term)
    annual_ds = pmt * 12
    annual_cf = noi - annual_ds
    monthly_cf = annual_cf / 12

    return CashFlowResult(
        strategy=Strategy.MTR,
        scenario=scenario,
        scenario_label=SCENARIO_LABELS[scenario],
        purchase_price=purchase_price,
        loan_amount=loan_amount,
        total_cash_invested=total_cash_invested,
        revenue=RevenueDetail(
            gross_annual_revenue=gross_annual,
            vacancy_loss=vacancy_loss,
            effective_gross_income=egi,
        ),
        expenses=expenses,
        noi=noi,
        annual_debt_service=annual_ds,
        annual_cash_flow=annual_cf,
        monthly_cash_flow=monthly_cf,
        cap_rate=_safe_divide(noi, purchase_price),
        cash_on_cash=_safe_divide(annual_cf, total_cash_invested),
        grm=_safe_divide(purchase_price, gross_annual),
        dscr=_safe_divide(noi, annual_ds),
        break_even_occupancy=None,
    )


def _calc_str(
    prop: dict[str, Any],
    purchase_price: float,
    loan_amount: float,
    total_cash_invested: float,
    base_rate: float,
    loan_term: int,
    shared_opex: dict[str, float],
    scenario: Scenario,
) -> CashFlowResult:
    mod = _SCENARIO_MODS[scenario]
    rate = base_rate + mod.rate_delta

    adr = _get(prop, "str_adr", 0.0) * mod.revenue_mult
    base_occupancy = _get(prop, "str_occupancy", 0.65)
    # Vacancy surge for STR: reduce occupancy
    if scenario == Scenario.VACANCY_SURGE:
        occupancy = max(base_occupancy * (1 - 0.33), 0.20)   # ≈ −33% of occupancy
    elif scenario == Scenario.FULL_STRESS:
        occupancy = max(base_occupancy - 0.05, 0.20)
    else:
        occupancy = base_occupancy

    platform_fee_pct = _get(prop, "str_platform_fee_pct", 0.03)
    cleaning_per_stay = _get(prop, "str_cleaning_per_stay", 150.0)
    avg_stay_nights = _get(prop, "str_avg_stay_nights", 3.5)
    mgmt_rate = _get(prop, "str_mgmt_rate", 0.25)
    utilities_monthly = _get(prop, "str_utilities_monthly", 275.0)
    furnishing_monthly = _get(prop, "str_furnishing_monthly", 200.0)

    occupied_nights = 365 * occupancy
    gross_annual = adr * occupied_nights
    # STR has no separate vacancy line; occupancy already bakes it in
    egi = gross_annual

    stays_per_year = occupied_nights / avg_stay_nights if avg_stay_nights > 0 else 0
    cleaning_annual = stays_per_year * cleaning_per_stay
    platform_fee_annual = gross_annual * platform_fee_pct
    mgmt_fee = gross_annual * mgmt_rate  # STR mgmt is % of gross (not EGI)

    expenses = ExpenseDetail(
        property_tax=shared_opex["property_tax"],
        insurance=shared_opex["insurance"] * 1.50,   # STR endorsement
        hoa=shared_opex["hoa"],
        maintenance=shared_opex["maintenance"] * 1.50,  # higher wear
        capex=shared_opex["capex"],
        property_mgmt=mgmt_fee,
        vacancy_loss=0.0,   # absorbed into occupancy rate
        utilities=utilities_monthly * 12,
        furnishing_amort=furnishing_monthly * 12,
        platform_fee=platform_fee_annual,
        cleaning=cleaning_annual,
    )

    cash_opex = (
        expenses.property_tax
        + expenses.insurance
        + expenses.hoa
        + expenses.maintenance
        + expenses.capex
        + expenses.property_mgmt
        + expenses.utilities
        + expenses.furnishing_amort
        + expenses.platform_fee
        + expenses.cleaning
    )
    noi = egi - cash_opex

    pmt = _monthly_payment(loan_amount, rate, loan_term)
    annual_ds = pmt * 12
    annual_cf = noi - annual_ds
    monthly_cf = annual_cf / 12

    # Break-even occupancy: what occupancy is needed to cover all cash costs?
    # cash_opex_fixed (not occupancy-dependent) + (adr × occ × variable_pct) = annual_ds
    # Solve: adr × 365 × occ_be × (1 - variable_pct) = cash_opex_fixed + annual_ds
    variable_pct = platform_fee_pct + mgmt_rate  # costs that scale with revenue
    fixed_opex = (
        expenses.property_tax
        + expenses.insurance
        + expenses.hoa
        + expenses.maintenance
        + expenses.capex
        + expenses.utilities
        + expenses.furnishing_amort
    )
    # cleaning also scales with occupancy; approximate via per-stay cost
    cleaning_per_night = _safe_divide(cleaning_per_stay, avg_stay_nights)
    # Revenue per night after variable costs: adr × (1 − variable_pct) − cleaning_per_night
    net_revenue_per_night = adr * (1 - variable_pct) - cleaning_per_night
    if net_revenue_per_night > 0:
        be_nights = _safe_divide(fixed_opex + annual_ds, net_revenue_per_night)
        break_even_occ = min(be_nights / 365, 1.0)
    else:
        break_even_occ = 1.0

    return CashFlowResult(
        strategy=Strategy.STR,
        scenario=scenario,
        scenario_label=SCENARIO_LABELS[scenario],
        purchase_price=purchase_price,
        loan_amount=loan_amount,
        total_cash_invested=total_cash_invested,
        revenue=RevenueDetail(
            gross_annual_revenue=gross_annual,
            vacancy_loss=0.0,
            effective_gross_income=egi,
        ),
        expenses=expenses,
        noi=noi,
        annual_debt_service=annual_ds,
        annual_cash_flow=annual_cf,
        monthly_cash_flow=monthly_cf,
        cap_rate=_safe_divide(noi, purchase_price),
        cash_on_cash=_safe_divide(annual_cf, total_cash_invested),
        grm=_safe_divide(purchase_price, gross_annual),
        dscr=_safe_divide(noi, annual_ds),
        break_even_occupancy=break_even_occ,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def underwrite(prop: dict[str, Any]) -> UnderwritingResult:
    """
    Underwrite a property across LTR, MTR, and STR strategies with five
    stress-test scenarios each.

    Args:
        prop: Property dictionary. See module docstring for full key list.

    Returns:
        UnderwritingResult containing StrategyResult objects for LTR, MTR, STR.

    Raises:
        ValueError: If purchase_price is missing or ≤ 0.
    """
    purchase_price: float = _get(prop, "purchase_price", None)
    if not purchase_price or purchase_price <= 0:
        raise ValueError("property dict must include a positive 'purchase_price'")

    down_pct = _get(prop, "down_payment_pct", 0.20)
    base_rate = _get(prop, "interest_rate", 0.075)
    loan_term = int(_get(prop, "loan_term_years", 30))
    closing_pct = _get(prop, "closing_costs_pct", 0.03)
    rehab = _get(prop, "rehab_cost", 0.0)

    down_payment = purchase_price * down_pct
    loan_amount = purchase_price - down_payment
    closing_costs = purchase_price * closing_pct
    total_cash_invested = down_payment + closing_costs + rehab

    # Shared annual operating expenses (strategy-agnostic base)
    shared_opex = {
        "property_tax": _get(prop, "property_tax_annual", purchase_price * 0.011),
        "insurance":    _get(prop, "insurance_annual",    purchase_price * 0.005),
        "hoa":          _get(prop, "hoa_monthly", 0.0) * 12,
        "maintenance":  purchase_price * _get(prop, "maintenance_rate", 0.01),
        "capex":        purchase_price * _get(prop, "capex_rate", 0.01),
    }

    def _run_all_scenarios(calc_fn):
        result = {}
        for scenario in Scenario:
            result[scenario] = calc_fn(
                prop=prop,
                purchase_price=purchase_price,
                loan_amount=loan_amount,
                total_cash_invested=total_cash_invested,
                base_rate=base_rate,
                loan_term=loan_term,
                shared_opex=shared_opex,
                scenario=scenario,
            )
        return result

    ltr_scenarios = _run_all_scenarios(_calc_ltr)
    mtr_scenarios = _run_all_scenarios(_calc_mtr)
    str_scenarios = _run_all_scenarios(_calc_str)

    # Base-case payment (for top-level summary)
    base_pmt = _monthly_payment(loan_amount, base_rate, loan_term)

    return UnderwritingResult(
        purchase_price=purchase_price,
        total_cash_invested=total_cash_invested,
        loan_amount=loan_amount,
        monthly_payment=base_pmt,
        annual_debt_service=base_pmt * 12,
        ltr=StrategyResult(strategy=Strategy.LTR, scenarios=ltr_scenarios),
        mtr=StrategyResult(strategy=Strategy.MTR, scenarios=mtr_scenarios),
        str_=StrategyResult(strategy=Strategy.STR, scenarios=str_scenarios),
    )
