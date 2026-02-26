"""
underwriting/calculator.py

Pure math underwriting engine for VA loan multifamily investing in Jacksonville (Duval County), FL.
Zero API calls. Zero DB access.

Entry point:
    result = underwrite(property_dict)

Returns an UnderwritingResult with LTR, MTR, and STR StrategyResults, each containing a base
case and five individual stress-test scenarios.

Property dict keys (snake_case; all optional except purchase_price):

    # Acquisition — VA Loan
    purchase_price          float   required
    va_funding_fee_pct      float   default 0.0215  (first use; pass 0.033 for subsequent)
    interest_rate           float   default 0.075
    loan_term_years         int     default 30
    closing_costs_pct       float   default 0.03
    rehab_cost              float   default 0.0
    num_units               int     default 1       (for per-unit MTR/STR expense scaling)

    # LTR — Long-Term Rental
    monthly_rent            float   total gross rent (all units combined)
    ltr_vacancy_rate        float   default 0.08
    ltr_mgmt_rate           float   default 0.08
    ltr_maintenance_rate    float   default 0.01    (% of purchase_price)
    ltr_capex_rate          float   default 0.005   (% of purchase_price)

    # MTR — Medium-Term Rental (furnished, corporate/travel-nurse, 1–6 month stays)
    mtr_monthly_rate        float   total furnished rent (all units combined)
    mtr_vacancy_rate        float   default 0.10
    mtr_mgmt_rate           float   default 0.10
    mtr_utilities_per_unit  float   default 150.0   ($/mo/unit — utilities + internet)
    mtr_furnishing_per_unit float   default 50.0    ($/mo/unit — furnishing amortization)
    mtr_avg_stay_months     float   default 3.0     (average stay length for turnover calc)
    mtr_turnover_cost       float   default 200.0   ($/stay — cleaning + restock)

    # STR — Short-Term Rental (Airbnb / VRBO)
    str_adr                 float   average daily rate
    str_vacancy_rate        float   default 0.25    (occupancy = 1 − vacancy_rate = 0.75)
    str_platform_fee_pct    float   default 0.15    (includes all management)
    str_cleaning_per_turn   float   default 125.0
    str_avg_stay_nights     float   default 3.5
    str_maintenance_rate    float   default 0.015   (% of purchase_price — higher wear)
    str_capex_rate          float   default 0.005   (% of purchase_price)

    # Shared operating expenses
    property_tax_annual     float   default 0.77% of purchase_price (Duval County)
    insurance_annual        float   default 0.50% of purchase_price
    hoa_monthly             float   default 0.0
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Enums & labels
# ──────────────────────────────────────────────────────────────────────────────

class Strategy(str, Enum):
    LTR = "LTR"
    MTR = "MTR"
    STR = "STR"


class Scenario(str, Enum):
    BASE             = "base"
    STRESS_RATE      = "stress_rate"       # interest rate +1%
    STRESS_INSURANCE = "stress_insurance"  # insurance cost +50%
    STRESS_VACANCY   = "stress_vacancy"    # vacancy +20 percentage points
    STRESS_TAX       = "stress_tax"        # property tax +25%
    STRESS_REPAIR    = "stress_repair"     # $8,000 year-1 one-time repair


SCENARIO_LABELS: dict[Scenario, str] = {
    Scenario.BASE:             "Base Case",
    Scenario.STRESS_RATE:      "Rate Shock (+1%)",
    Scenario.STRESS_INSURANCE: "Insurance Spike (+50%)",
    Scenario.STRESS_VACANCY:   "Vacancy Shock (+20 pts)",
    Scenario.STRESS_TAX:       "Tax Reassessment (+25%)",
    Scenario.STRESS_REPAIR:    "Year-1 Major Repair ($8,000)",
}


# ──────────────────────────────────────────────────────────────────────────────
# Stress-test modifiers (one axis each)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _StressMod:
    rate_delta:      float = 0.0    # additive to interest_rate
    insurance_mult:  float = 1.0    # multiplier on insurance_annual
    vacancy_delta:   float = 0.0    # additive to vacancy rate (absolute pp)
    tax_mult:        float = 1.0    # multiplier on property_tax_annual
    one_time_repair: float = 0.0    # subtracted from annual_cash_flow (year-1 cost)


_MODS: dict[Scenario, _StressMod] = {
    Scenario.BASE:             _StressMod(),
    Scenario.STRESS_RATE:      _StressMod(rate_delta=0.01),
    Scenario.STRESS_INSURANCE: _StressMod(insurance_mult=1.50),
    Scenario.STRESS_VACANCY:   _StressMod(vacancy_delta=0.20),
    Scenario.STRESS_TAX:       _StressMod(tax_mult=1.25),
    Scenario.STRESS_REPAIR:    _StressMod(one_time_repair=8_000.0),
}


# ──────────────────────────────────────────────────────────────────────────────
# Result data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ExpenseDetail:
    property_tax:    float
    insurance:       float
    hoa:             float
    maintenance:     float
    capex:           float
    property_mgmt:   float
    vacancy_loss:    float
    utilities:       float = 0.0
    furnishing_amort: float = 0.0
    turnover:        float = 0.0
    platform_fee:    float = 0.0
    cleaning:        float = 0.0
    one_time_repair: float = 0.0   # STRESS_REPAIR only; excluded from NOI calc

    @property
    def operating_total(self) -> float:
        """All recurring cash opex (excludes one_time_repair; used for NOI)."""
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
            + self.turnover
            + self.platform_fee
            + self.cleaning
        )


@dataclass
class RevenueDetail:
    gross_annual_revenue: float
    vacancy_loss:         float
    effective_gross_income: float


@dataclass
class CashFlowResult:
    strategy:       Strategy
    scenario:       Scenario
    scenario_label: str

    # Capital stack
    purchase_price:      float
    loan_amount:         float        # includes VA funding fee
    total_cash_invested: float        # closing costs + rehab (0% down VA loan)

    # Revenue
    revenue: RevenueDetail

    # Expenses
    expenses: ExpenseDetail

    # P&L
    noi:                 float   # EGI − operating_total (excl. one_time_repair)
    annual_debt_service: float
    annual_cash_flow:    float   # NOI − debt service − one_time_repair
    monthly_cash_flow:   float

    # Return metrics
    cap_rate:    float   # NOI / purchase_price
    cash_on_cash: float  # annual_cash_flow / total_cash_invested
    grm:         float   # purchase_price / gross_annual_revenue
    dscr:        float   # NOI / annual_debt_service

    # STR-specific
    break_even_occupancy: float | None = None  # occupancy needed to cover all costs
    seasonality_flag:     bool = False


@dataclass
class StrategyResult:
    strategy:  Strategy
    scenarios: dict[Scenario, CashFlowResult] = field(default_factory=dict)

    @property
    def base(self) -> CashFlowResult:
        return self.scenarios[Scenario.BASE]

    @property
    def worst_case_cash_flow(self) -> float:
        """Minimum monthly cash flow across all stress scenarios (used for scoring)."""
        return min(r.monthly_cash_flow for r in self.scenarios.values())


@dataclass
class UnderwritingResult:
    purchase_price:      float
    loan_amount:         float
    total_cash_invested: float
    monthly_payment:     float   # base-case P&I
    annual_debt_service: float   # base-case

    ltr:  StrategyResult
    mtr:  StrategyResult
    str_: StrategyResult   # str is a built-in; attribute named str_

    @property
    def all_results(self) -> list[CashFlowResult]:
        results: list[CashFlowResult] = []
        for strat in (self.ltr, self.mtr, self.str_):
            results.extend(strat.scenarios.values())
        return results

    def best_strategy(self, scenario: Scenario = Scenario.BASE) -> CashFlowResult:
        """Return the highest CoC strategy for a given scenario."""
        return max(
            [self.ltr.scenarios[scenario],
             self.mtr.scenarios[scenario],
             self.str_.scenarios[scenario]],
            key=lambda r: r.cash_on_cash,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    """Standard amortising mortgage payment (P&I only)."""
    if annual_rate == 0:
        return principal / (years * 12)
    r = annual_rate / 12
    n = years * 12
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def _safe_div(n: float, d: float, default: float = 0.0) -> float:
    return default if d == 0 else n / d


def _get(prop: dict[str, Any], key: str, default: Any) -> Any:
    v = prop.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return v


# ──────────────────────────────────────────────────────────────────────────────
# Per-strategy calculators
# ──────────────────────────────────────────────────────────────────────────────

def _ltr(
    prop: dict[str, Any],
    purchase_price: float,
    loan_amount: float,
    total_cash_invested: float,
    base_rate: float,
    loan_term: int,
    base_tax: float,
    base_insurance: float,
    hoa_annual: float,
    scenario: Scenario,
) -> CashFlowResult:
    mod = _MODS[scenario]

    rate      = base_rate + mod.rate_delta
    tax       = base_tax      * mod.tax_mult
    insurance = base_insurance * mod.insurance_mult

    monthly_rent   = _get(prop, "monthly_rent", 0.0)
    vacancy_rate   = min(_get(prop, "ltr_vacancy_rate",   0.08) + mod.vacancy_delta, 0.95)
    mgmt_rate      = _get(prop, "ltr_mgmt_rate",          0.08)
    maintenance    = purchase_price * _get(prop, "ltr_maintenance_rate", 0.01)
    capex          = purchase_price * _get(prop, "ltr_capex_rate",       0.005)

    gross     = monthly_rent * 12
    vac_loss  = gross * vacancy_rate
    egi       = gross - vac_loss
    mgmt_fee  = egi * mgmt_rate

    expenses = ExpenseDetail(
        property_tax  = tax,
        insurance     = insurance,
        hoa           = hoa_annual,
        maintenance   = maintenance,
        capex         = capex,
        property_mgmt = mgmt_fee,
        vacancy_loss  = vac_loss,
        one_time_repair = mod.one_time_repair,
    )

    noi     = egi - expenses.operating_total + vac_loss  # vacancy already removed from EGI
    # Correct: NOI = EGI − all cash opex *except* vacancy (which is embedded in EGI)
    cash_opex = (
        expenses.property_tax + expenses.insurance + expenses.hoa
        + expenses.maintenance + expenses.capex + expenses.property_mgmt
    )
    noi = egi - cash_opex

    pmt      = _monthly_payment(loan_amount, rate, loan_term)
    ann_ds   = pmt * 12
    ann_cf   = noi - ann_ds - mod.one_time_repair
    mo_cf    = ann_cf / 12

    return CashFlowResult(
        strategy       = Strategy.LTR,
        scenario       = scenario,
        scenario_label = SCENARIO_LABELS[scenario],
        purchase_price      = purchase_price,
        loan_amount         = loan_amount,
        total_cash_invested = total_cash_invested,
        revenue  = RevenueDetail(gross, vac_loss, egi),
        expenses = expenses,
        noi                 = noi,
        annual_debt_service = ann_ds,
        annual_cash_flow    = ann_cf,
        monthly_cash_flow   = mo_cf,
        cap_rate    = _safe_div(noi, purchase_price),
        cash_on_cash= _safe_div(ann_cf, total_cash_invested),
        grm         = _safe_div(purchase_price, gross),
        dscr        = _safe_div(noi, ann_ds),
    )


def _mtr(
    prop: dict[str, Any],
    purchase_price: float,
    loan_amount: float,
    total_cash_invested: float,
    base_rate: float,
    loan_term: int,
    base_tax: float,
    base_insurance: float,
    hoa_annual: float,
    scenario: Scenario,
) -> CashFlowResult:
    mod = _MODS[scenario]

    rate      = base_rate + mod.rate_delta
    tax       = base_tax      * mod.tax_mult
    insurance = base_insurance * mod.insurance_mult

    num_units     = int(_get(prop, "num_units", 1))
    mtr_monthly   = _get(prop, "mtr_monthly_rate", 0.0)
    vacancy_rate  = min(_get(prop, "mtr_vacancy_rate",        0.10) + mod.vacancy_delta, 0.95)
    mgmt_rate     = _get(prop, "mtr_mgmt_rate",               0.10)
    maintenance   = purchase_price * _get(prop, "ltr_maintenance_rate", 0.01)
    capex         = purchase_price * _get(prop, "ltr_capex_rate",       0.005)
    util_pu       = _get(prop, "mtr_utilities_per_unit",  150.0)
    furn_pu       = _get(prop, "mtr_furnishing_per_unit",  50.0)
    avg_stay_mo   = max(_get(prop, "mtr_avg_stay_months",    3.0), 0.5)
    turnover_cost = _get(prop, "mtr_turnover_cost",         200.0)

    gross    = mtr_monthly * 12
    vac_loss = gross * vacancy_rate
    egi      = gross - vac_loss
    mgmt_fee = egi * mgmt_rate

    stays_per_year  = (12 / avg_stay_mo) * num_units
    turnover_annual = stays_per_year * turnover_cost
    util_annual     = util_pu * num_units * 12
    furn_annual     = furn_pu * num_units * 12

    expenses = ExpenseDetail(
        property_tax    = tax,
        insurance       = insurance,
        hoa             = hoa_annual,
        maintenance     = maintenance,
        capex           = capex,
        property_mgmt   = mgmt_fee,
        vacancy_loss    = vac_loss,
        utilities       = util_annual,
        furnishing_amort= furn_annual,
        turnover        = turnover_annual,
        one_time_repair = mod.one_time_repair,
    )

    cash_opex = (
        expenses.property_tax + expenses.insurance + expenses.hoa
        + expenses.maintenance + expenses.capex + expenses.property_mgmt
        + expenses.utilities + expenses.furnishing_amort + expenses.turnover
    )
    noi    = egi - cash_opex
    pmt    = _monthly_payment(loan_amount, rate, loan_term)
    ann_ds = pmt * 12
    ann_cf = noi - ann_ds - mod.one_time_repair
    mo_cf  = ann_cf / 12

    return CashFlowResult(
        strategy       = Strategy.MTR,
        scenario       = scenario,
        scenario_label = SCENARIO_LABELS[scenario],
        purchase_price      = purchase_price,
        loan_amount         = loan_amount,
        total_cash_invested = total_cash_invested,
        revenue  = RevenueDetail(gross, vac_loss, egi),
        expenses = expenses,
        noi                 = noi,
        annual_debt_service = ann_ds,
        annual_cash_flow    = ann_cf,
        monthly_cash_flow   = mo_cf,
        cap_rate    = _safe_div(noi, purchase_price),
        cash_on_cash= _safe_div(ann_cf, total_cash_invested),
        grm         = _safe_div(purchase_price, gross),
        dscr        = _safe_div(noi, ann_ds),
    )


def _str(
    prop: dict[str, Any],
    purchase_price: float,
    loan_amount: float,
    total_cash_invested: float,
    base_rate: float,
    loan_term: int,
    base_tax: float,
    base_insurance: float,
    hoa_annual: float,
    scenario: Scenario,
) -> CashFlowResult:
    mod = _MODS[scenario]

    rate      = base_rate + mod.rate_delta
    tax       = base_tax      * mod.tax_mult
    insurance = base_insurance * mod.insurance_mult

    adr              = _get(prop, "str_adr",             0.0)
    base_vacancy     = _get(prop, "str_vacancy_rate",    0.25)
    vacancy_rate     = min(base_vacancy + mod.vacancy_delta, 0.95)
    occupancy        = 1.0 - vacancy_rate
    platform_fee_pct = _get(prop, "str_platform_fee_pct", 0.15)
    cleaning_pt      = _get(prop, "str_cleaning_per_turn", 125.0)
    avg_stay_nights  = max(_get(prop, "str_avg_stay_nights", 3.5), 1.0)
    maintenance      = purchase_price * _get(prop, "str_maintenance_rate", 0.015)
    capex            = purchase_price * _get(prop, "str_capex_rate",       0.005)

    occupied_nights  = 365.0 * occupancy
    gross            = adr * occupied_nights
    # STR models vacancy via occupancy; no separate vacancy_loss line
    egi              = gross

    turns_per_year   = occupied_nights / avg_stay_nights
    platform_fee_ann = gross * platform_fee_pct
    cleaning_annual  = turns_per_year * cleaning_pt

    expenses = ExpenseDetail(
        property_tax    = tax,
        insurance       = insurance,
        hoa             = hoa_annual,
        maintenance     = maintenance,
        capex           = capex,
        property_mgmt   = 0.0,      # platform fees cover management
        vacancy_loss    = 0.0,      # absorbed in occupancy rate
        platform_fee    = platform_fee_ann,
        cleaning        = cleaning_annual,
        one_time_repair = mod.one_time_repair,
    )

    cash_opex = (
        expenses.property_tax + expenses.insurance + expenses.hoa
        + expenses.maintenance + expenses.capex
        + expenses.platform_fee + expenses.cleaning
    )
    noi    = egi - cash_opex
    pmt    = _monthly_payment(loan_amount, rate, loan_term)
    ann_ds = pmt * 12
    ann_cf = noi - ann_ds - mod.one_time_repair
    mo_cf  = ann_cf / 12

    # Break-even occupancy: what fraction of nights covers all costs + debt service?
    # Solve: adr × occ_be × 365 × (1 − platform_fee_pct) − cleaning_per_night × 365 × occ_be
    #        = fixed_opex + ann_ds
    # where cleaning_per_night = cleaning_pt / avg_stay_nights
    cleaning_per_night = cleaning_pt / avg_stay_nights
    net_per_night      = adr * (1.0 - platform_fee_pct) - cleaning_per_night
    fixed_opex         = (
        tax + insurance + hoa_annual + maintenance + capex
    )
    if net_per_night > 0:
        be_nights = _safe_div(fixed_opex + ann_ds, net_per_night)
        break_even_occ = min(be_nights / 365.0, 1.0)
    else:
        break_even_occ = 1.0

    return CashFlowResult(
        strategy       = Strategy.STR,
        scenario       = scenario,
        scenario_label = SCENARIO_LABELS[scenario],
        purchase_price      = purchase_price,
        loan_amount         = loan_amount,
        total_cash_invested = total_cash_invested,
        revenue  = RevenueDetail(gross, 0.0, egi),
        expenses = expenses,
        noi                 = noi,
        annual_debt_service = ann_ds,
        annual_cash_flow    = ann_cf,
        monthly_cash_flow   = mo_cf,
        cap_rate    = _safe_div(noi, purchase_price),
        cash_on_cash= _safe_div(ann_cf, total_cash_invested),
        grm         = _safe_div(purchase_price, gross),
        dscr        = _safe_div(noi, ann_ds),
        break_even_occupancy = break_even_occ,
        seasonality_flag     = True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def underwrite(prop: dict[str, Any]) -> UnderwritingResult:
    """
    Underwrite a property across LTR, MTR, and STR strategies with a base case
    and five individual stress-test scenarios each.

    Args:
        prop: Property dictionary. See module docstring for full key list.
              Uses VA loan defaults (0% down, 2.15% funding fee rolled into loan).

    Returns:
        UnderwritingResult containing StrategyResults for LTR, MTR, STR.

    Raises:
        ValueError: If purchase_price is missing or ≤ 0.
    """
    purchase_price: float = _get(prop, "purchase_price", None)
    if not purchase_price or purchase_price <= 0:
        raise ValueError("property dict must include a positive 'purchase_price'")

    va_fee_pct   = _get(prop, "va_funding_fee_pct", 0.0215)
    base_rate    = _get(prop, "interest_rate",       0.075)
    loan_term    = int(_get(prop, "loan_term_years", 30))
    closing_pct  = _get(prop, "closing_costs_pct",  0.03)
    rehab        = _get(prop, "rehab_cost",           0.0)

    # VA loan: 0% down, funding fee rolled into loan balance
    funding_fee          = purchase_price * va_fee_pct
    loan_amount          = purchase_price + funding_fee
    closing_costs        = purchase_price * closing_pct
    total_cash_invested  = closing_costs + rehab   # no down payment

    # Base shared opex (stressed per-scenario inside each calc)
    base_tax       = _get(prop, "property_tax_annual", purchase_price * 0.0077)
    base_insurance = _get(prop, "insurance_annual",    purchase_price * 0.005)
    hoa_annual     = _get(prop, "hoa_monthly", 0.0) * 12

    common = dict(
        prop                = prop,
        purchase_price      = purchase_price,
        loan_amount         = loan_amount,
        total_cash_invested = total_cash_invested,
        base_rate           = base_rate,
        loan_term           = loan_term,
        base_tax            = base_tax,
        base_insurance      = base_insurance,
        hoa_annual          = hoa_annual,
    )

    ltr_scenarios  = {s: _ltr(**common, scenario=s) for s in Scenario}
    mtr_scenarios  = {s: _mtr(**common, scenario=s) for s in Scenario}
    str_scenarios  = {s: _str(**common, scenario=s) for s in Scenario}

    base_pmt = _monthly_payment(loan_amount, base_rate, loan_term)

    return UnderwritingResult(
        purchase_price      = purchase_price,
        loan_amount         = loan_amount,
        total_cash_invested = total_cash_invested,
        monthly_payment     = base_pmt,
        annual_debt_service = base_pmt * 12,
        ltr  = StrategyResult(Strategy.LTR, ltr_scenarios),
        mtr  = StrategyResult(Strategy.MTR, mtr_scenarios),
        str_ = StrategyResult(Strategy.STR, str_scenarios),
    )
