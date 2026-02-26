#!/usr/bin/env python3
"""
scripts/test_property.py

End-to-end smoke test — Dellwood triplex.
Runs a hardcoded property through the entire pipeline (no live API calls)
and prints the full deal card to the terminal.

Demonstrates comp sourcing priority:
  1. Live comps (passed as RentalComp-like objects) → LIVE_COMPS
  2. Rentcast API (no key needed for smoke test) → PROP_DICT
  3. Hardcoded defaults → DEFAULTS (triggers confidence_penalty)

Usage:
    python scripts/test_property.py
"""

from __future__ import annotations

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

# ── 1. Build a synthetic PropertyRecord ───────────────────────────────────────

from normalization.schema import (
    DataSource, PropertyRecord, PropertyType, RentalComp, RentalStrategy,
    make_canonical_id,
)

DELLWOOD_TRIPLEX = PropertyRecord(
    canonical_id   = make_canonical_id("1234 Dellwood Ave", "32204"),
    source         = DataSource.ZILLOW_SALE,
    source_id      = "smoke_test_001",
    scraped_at     = datetime.now(tz=timezone.utc),
    address        = "1234 Dellwood Ave",
    city           = "Jacksonville",
    state          = "FL",
    zip_code       = "32204",
    lat            = 30.3204,
    lon            = -81.6636,
    price          = 410_000.0,
    beds           = 6,
    baths          = 3.0,
    sqft           = 2_400.0,
    year_built     = 1928,
    property_type  = PropertyType.TRIPLEX,
    num_units      = 3,
    days_on_market = 18,
)

# ── 2. Neighborhood (no live APIs — use mocked data) ──────────────────────────

from neighborhood.flood_zone import FloodZoneResult
from neighborhood.crime_grade import CrimeGradeResult
from neighborhood.hospital_proximity import get_hospital_proximity
from neighborhood.liveability import compute_liveability
from neighborhood.gates import evaluate_gates
from neighborhood.street_view import get_street_view_urls

flood  = FloodZoneResult(flood_zone="X", is_high_risk=False, sfha=False, panel_number="12031C0306H")
crime  = CrimeGradeResult(grade="B+", passes_gate=True, low_confidence=False)
hospital = get_hospital_proximity(DELLWOOD_TRIPLEX.lat, DELLWOOD_TRIPLEX.lon)
liveability = compute_liveability(
    walk_score  = 72,
    hospital    = hospital,
    crime_grade = crime["grade"],
    flood       = flood,
    visual_score = 7,
)
gate_result = evaluate_gates(DELLWOOD_TRIPLEX, crime, flood)
sv_urls = get_street_view_urls(
    DELLWOOD_TRIPLEX.lat, DELLWOOD_TRIPLEX.lon, DELLWOOD_TRIPLEX.address
)

# ── 3. Live VA rate ────────────────────────────────────────────────────────────
# Attempts a real FRED fetch; gracefully falls back to 7.5% if offline.

from ingestion.va_rates import fetch_va_rate, VARate

va_rate: VARate = fetch_va_rate()

# ── 4. Synthetic live comps (simulate normalization output) ───────────────────
# Represents comparable rentals in 32204 from Zillow/Rentcast.
# Each comp is a 2BR unit (~$1,100/mo LTR, ~$1,800/mo MTR furnished).

def _make_comp(strategy: RentalStrategy, monthly_rate: float, beds: int) -> RentalComp:
    return RentalComp(
        canonical_id    = make_canonical_id(f"comp-{strategy}-{beds}-{monthly_rate}", "32204"),
        source          = DataSource.ZILLOW_RENTAL,
        source_id       = f"smoke_comp_{strategy}_{beds}",
        scraped_at      = datetime.now(tz=timezone.utc),
        address         = f"Comp {strategy} Unit",
        city            = "Jacksonville",
        state           = "FL",
        zip_code        = "32204",
        lat             = 30.3210,
        lon             = -81.6640,
        beds            = beds,
        baths           = 1.0,
        sqft            = 800.0,
        rental_strategy = strategy,
        monthly_rate    = monthly_rate,
        adr             = None,
        utilities_included = False,
    )

# 5 LTR comps — 2BR units at ~$1,100/mo
ltr_comps = [
    _make_comp(RentalStrategy.LTR, 1_050.0, 2),
    _make_comp(RentalStrategy.LTR, 1_100.0, 2),
    _make_comp(RentalStrategy.LTR, 1_125.0, 2),
    _make_comp(RentalStrategy.LTR, 1_150.0, 2),
    _make_comp(RentalStrategy.LTR, 1_200.0, 2),
]

# 5 MTR comps — 2BR furnished at ~$1,800/mo
mtr_comps = [
    _make_comp(RentalStrategy.MTR, 1_700.0, 2),
    _make_comp(RentalStrategy.MTR, 1_800.0, 2),
    _make_comp(RentalStrategy.MTR, 1_850.0, 2),
    _make_comp(RentalStrategy.MTR, 1_900.0, 2),
    _make_comp(RentalStrategy.MTR, 1_950.0, 2),
]

all_comps = ltr_comps + mtr_comps
comps_count = len(all_comps)

# ── 5. Underwriting ────────────────────────────────────────────────────────────

from underwriting.calculator import underwrite, Scenario, RentSource

prop_dict = {
    "purchase_price":        DELLWOOD_TRIPLEX.price,
    "num_units":             DELLWOOD_TRIPLEX.num_units,
    "zip_code":              DELLWOOD_TRIPLEX.zip_code,
    "beds":                  DELLWOOD_TRIPLEX.beds,
    "interest_rate":         va_rate.rate,          # live or fallback VA rate
    "insurance_annual":      DELLWOOD_TRIPLEX.price * 0.005,
    "ltr_vacancy_rate":      0.08,
    "mtr_vacancy_rate":      0.10,
    "str_vacancy_rate":      0.25,
    "ltr_mgmt_rate":         0.08,
    "ltr_maintenance_rate":  0.01,
    "ltr_capex_rate":        0.005,
    "va_funding_fee_pct":    0.0215,
    "str_adr":               175.0,    # $175/night STR ADR estimate
}

# Pass live comps → underwrite() selects top 5 by beds, computes medians
uw = underwrite(prop_dict, comps=all_comps)

# ── 6. Conservative cash flows ────────────────────────────────────────────────

worst_ltr = uw.ltr.worst_case_cash_flow
worst_mtr = uw.mtr.worst_case_cash_flow
worst_str = uw.str_.worst_case_cash_flow
best_cf   = max(worst_ltr, worst_mtr, worst_str)
best_base = uw.best_strategy(Scenario.BASE)

# ── 7. Scoring ────────────────────────────────────────────────────────────────

from scoring.deal_scorer import score_deal

ds = score_deal(
    conservative_monthly_cash_flow = best_cf,
    dscr            = best_base.dscr,
    cash_on_cash    = best_base.cash_on_cash,
    year_built      = DELLWOOD_TRIPLEX.year_built,
    flood_high_risk = flood["is_high_risk"],
    purchase_price  = DELLWOOD_TRIPLEX.price,
    raw_confidence  = 0.90,
    scraped_at      = DELLWOOD_TRIPLEX.scraped_at,
    comps_count     = comps_count,
    address         = DELLWOOD_TRIPLEX.address,
    num_units       = DELLWOOD_TRIPLEX.num_units,
    strategy_validated = True,
    confidence_penalty = uw.confidence_penalty,
)

# ── 8. Refi scenario ──────────────────────────────────────────────────────────

from underwriting.calculator import _monthly_payment
refi_pmt = _monthly_payment(uw.loan_amount, 0.055, 30)

# ── 9. Print deal card ────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD  = "\033[1m"
GREEN = "\033[32m"
RED   = "\033[31m"
CYAN  = "\033[36m"
YEL   = "\033[33m"

def c(text, color): return f"{color}{text}{RESET}"

print()
print("=" * 72)
print(c("  JAX ANALYZER — DEAL CARD (SMOKE TEST)", BOLD))
print("=" * 72)
print()

# Address + basics
print(c(f"  {DELLWOOD_TRIPLEX.address}, Jacksonville FL {DELLWOOD_TRIPLEX.zip_code}", BOLD))
print(f"  {DELLWOOD_TRIPLEX.property_type.value.title()} · {DELLWOOD_TRIPLEX.num_units} units · "
      f"Built {DELLWOOD_TRIPLEX.year_built} · {DELLWOOD_TRIPLEX.sqft:,.0f} sqft")
print(f"  List price: {c(f'${DELLWOOD_TRIPLEX.price:,.0f}', BOLD)} · {DELLWOOD_TRIPLEX.days_on_market}d on market")
print()

# VA Rate banner
rate_color = GREEN if not va_rate.is_stale and not va_rate.is_fallback else YEL
rate_label = {
    "fred": "LIVE (FRED/PMMS)",
    "cache": "CACHED",
    "fallback": "FALLBACK — hardcoded 7.50%",
}.get(va_rate.source, va_rate.source.upper())
print(c("  VA RATE", BOLD))
print(f"  {c(f'{va_rate.rate:.3%}', rate_color)}  [{rate_label}]", end="")
if va_rate.is_stale:
    print(f"  {c('⚠ Stale (>48h old)', YEL)}", end="")
print()
print()

# Comp source banner
comp_colors = {
    RentSource.LIVE_COMPS: GREEN,
    RentSource.PROP_DICT:  YEL,
    RentSource.DEFAULTS:   RED,
}
comp_labels = {
    RentSource.LIVE_COMPS: "LIVE COMPS  — median of top-5 matched by bed count",
    RentSource.PROP_DICT:  "RENTCAST / MANUAL — caller pre-filled rates",
    RentSource.DEFAULTS:   "HARDCODED DEFAULTS — no comp or Rentcast data",
}
comp_col = comp_colors.get(uw.rent_source, RESET)
print(c("  COMP SOURCE", BOLD))
print(f"  {c(comp_labels.get(uw.rent_source, str(uw.rent_source)), comp_col)}")
if uw.rent_source == RentSource.LIVE_COMPS:
    ltr_total = uw.ltr.base.revenue.gross_annual_revenue / 12
    mtr_total = uw.mtr.base.revenue.gross_annual_revenue / 12
    print(f"  {comps_count} comps used  ·  "
          f"LTR ${ltr_total:,.0f}/mo total  ·  MTR ${mtr_total:,.0f}/mo total")
if uw.confidence_penalty:
    print(f"  {c('⚠ confidence_penalty=True — Confidence Score capped at 19, no HP alert possible', RED)}")
print()

# Score
score_color = GREEN if ds.deal_score >= 60 else (YEL if ds.deal_score >= 40 else RED)
alert_tag   = c("  🔥 HIGH PRIORITY ALERT", RED) if ds.is_high_priority else ""
print(c(f"  DEAL SCORE: {ds.deal_score}/100", score_color) + alert_tag)
print(f"  Return: {ds.return_score.score}/40  |  Risk: {ds.risk_score.score}/30  |  Confidence: {ds.confidence_score.score}/30")
if ds.confidence_score.data_penalty:
    print(f"  {c('  (Confidence capped at 19 — no comp data)', YEL)}")
print()
print(f"  {ds.why_scored_high}")
print()

# Strategy ranking
print(c("  STRATEGIES (Conservative / Worst Stress Case)", BOLD))
rows = sorted([
    ("LTR", worst_ltr, uw.ltr.base.dscr, uw.ltr.base.cash_on_cash),
    ("MTR", worst_mtr, uw.mtr.base.dscr, uw.mtr.base.cash_on_cash),
    ("STR", worst_str, uw.str_.base.dscr, uw.str_.base.cash_on_cash),
], key=lambda r: r[1], reverse=True)
for i, (name, cf, dscr, coc) in enumerate(rows, 1):
    cf_c = GREEN if cf >= 500 else RED
    print(f"  #{i} {name}: {c(f'${cf:,.0f}/mo', cf_c)} · DSCR {dscr:.2f} · CoC {coc:.1%}")
print()

# VA Loan snapshot
print(c("  VA LOAN SNAPSHOT", BOLD))
print(f"  Purchase price:    ${DELLWOOD_TRIPLEX.price:,.0f}")
print(f"  Funding fee (2.15%): ${uw.loan_amount - DELLWOOD_TRIPLEX.price:,.0f}")
print(f"  Loan amount:       ${uw.loan_amount:,.0f}")
print(f"  Rate used:         {va_rate.rate:.3%}  [{rate_label}]")
print(f"  Monthly P&I:       ${uw.monthly_payment:,.0f}")
tax_mo = DELLWOOD_TRIPLEX.price * 0.0077 / 12
ins_mo = DELLWOOD_TRIPLEX.price * 0.005 / 12
total_piti = uw.monthly_payment + tax_mo + ins_mo
print(f"  Total PITI:        ${total_piti:,.0f}/mo (P&I + tax + insurance)")
print(f"  Cash to close:     ${uw.total_cash_invested:,.0f} (closing costs; 0% down)")
print(f"  Refi at 5.5%:      ${refi_pmt:,.0f}/mo P&I")
print()

# Stress tests
print(c("  STRESS TESTS (LTR base strategy)", BOLD))
for sc in list(Scenario):
    if sc.value == "base":
        continue
    res = uw.ltr.scenarios[sc]
    cf_c = GREEN if res.monthly_cash_flow >= 0 else RED
    print(f"  {res.scenario_label:<30} {c(f'${res.monthly_cash_flow:,.0f}/mo', cf_c)} · DSCR {res.dscr:.2f}")
print()

# Neighborhood
print(c("  NEIGHBORHOOD SCORES", BOLD))
grade_c = GREEN if crime["grade"] in ["A+","A","A-","B+","B","B-"] else RED
print(f"  Crime Grade:       {c(crime['grade'], grade_c)}")
print(f"  Walk Score:        72/100")
flood_c = RED if flood["is_high_risk"] else GREEN
print(f"  Flood Zone:        {c(flood['flood_zone'], flood_c)}")
print(f"  Hospital dist:     {hospital['distance_miles']:.2f} mi to {hospital['closest_hospital']}")
print(f"  Liveability Index: {liveability['total']}/100")
print()

# Gates
gate_c = GREEN if gate_result.passed else RED
print(c(f"  HARD GATES: {'ALL PASSED ✓' if gate_result.passed else 'FAILED ✗'}", gate_c))
if gate_result.failed_gates:
    for reason in gate_result.failed_gates:
        print(f"    ✗ {reason}")
print()

# Risk flags
if ds.risk_score.risk_flags:
    print(c("  RISK FLAGS", RED))
    for flag in ds.risk_score.risk_flags:
        print(f"  ⚠  {flag}")
    print()

# URLs
print(c("  LINKS", BOLD))
print(f"  Street View: {sv_urls['street_view_image_url'][:70]}...")
print(f"  Google Maps: {sv_urls['google_maps_link']}")
print()
print("=" * 72)
print(c("  Smoke test complete — all pipeline stages ran successfully.", GREEN))
print("=" * 72)
print()
