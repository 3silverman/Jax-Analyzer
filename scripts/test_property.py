#!/usr/bin/env python3
"""
scripts/test_property.py

End-to-end smoke test — Dellwood triplex.
Runs a hardcoded property through the entire pipeline (no live API calls)
and prints the full deal card to the terminal.

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

from normalization.schema import DataSource, PropertyRecord, PropertyType, make_canonical_id

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

# ── 3. Rental comp estimates (Riverside triplex market) ───────────────────────
# LTR: ~$1,100/unit, MTR: ~$1,800/unit/mo (furnished, near hospitals)

MONTHLY_LTR_RENT = 1_100 * 3    # $3,300/mo gross (all 3 units)
MTR_MONTHLY_RATE = 1_800 * 3    # $5,400/mo gross (MTR furnished)

# ── 4. Underwriting ───────────────────────────────────────────────────────────

from underwriting.calculator import underwrite, Scenario

prop_dict = {
    "purchase_price":        DELLWOOD_TRIPLEX.price,
    "num_units":             DELLWOOD_TRIPLEX.num_units,
    "interest_rate":         0.075,
    "insurance_annual":      DELLWOOD_TRIPLEX.price * 0.005,
    "ltr_vacancy_rate":      0.08,
    "mtr_vacancy_rate":      0.10,
    "str_vacancy_rate":      0.25,
    "ltr_mgmt_rate":         0.08,
    "ltr_maintenance_rate":  0.01,
    "ltr_capex_rate":        0.005,
    "va_funding_fee_pct":    0.0215,
    "monthly_rent":          MONTHLY_LTR_RENT,
    "mtr_monthly_rate":      MTR_MONTHLY_RATE,
    "str_adr":               175.0,    # $175/night STR ADR estimate
}
uw = underwrite(prop_dict)

# ── 5. Conservative cash flows ────────────────────────────────────────────────

worst_ltr = uw.ltr.worst_case_cash_flow
worst_mtr = uw.mtr.worst_case_cash_flow
worst_str = uw.str_.worst_case_cash_flow
best_cf   = max(worst_ltr, worst_mtr, worst_str)
best_base = uw.best_strategy(Scenario.BASE)

# ── 6. Scoring ────────────────────────────────────────────────────────────────

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
    comps_count     = 8,
    address         = DELLWOOD_TRIPLEX.address,
    num_units       = DELLWOOD_TRIPLEX.num_units,
    strategy_validated = True,
)

# ── 7. Refi scenario ──────────────────────────────────────────────────────────

from underwriting.calculator import _monthly_payment
refi_pmt = _monthly_payment(uw.loan_amount, 0.055, 30)

# ── 8. Print deal card ────────────────────────────────────────────────────────

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

# Score
score_color = GREEN if ds.deal_score >= 60 else (YEL if ds.deal_score >= 40 else RED)
alert_tag   = c("  🔥 HIGH PRIORITY ALERT", RED) if ds.is_high_priority else ""
print(c(f"  DEAL SCORE: {ds.deal_score}/100", score_color) + alert_tag)
print(f"  Return: {ds.return_score.score}/40  |  Risk: {ds.risk_score.score}/30  |  Confidence: {ds.confidence_score.score}/30")
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
