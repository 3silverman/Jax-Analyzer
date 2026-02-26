"""
scoring/risk_score.py

Risk Score (0–30) component of the Deal Score.

Starts at 30 and deducts points for risk factors.
Floor is 0.

Deduction table:
  Year built < 1950           → -5 pts
  Year built 1950–1979        → -3 pts
  Flood zone AE/VE            → -10 pts
  VA appraisal gap risk       → -5 pts  (price > 15% above zip median)
  All tenants month-to-month  → -2 pts
  No inspection contingency   → -3 pts
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskScore:
    score:         int        # 0–30
    deductions:    list[tuple[str, int]]  # (reason, points_lost)
    risk_flags:    list[str]  # human-readable flags for the deal card


def compute_risk_score(
    year_built: int | None,
    flood_high_risk: bool,
    purchase_price: float,
    zip_median_price: float | None = None,
    all_month_to_month: bool = False,
    no_inspection_contingency: bool = False,
) -> RiskScore:
    """
    Compute the Risk Score (0–30).

    Args:
        year_built:                Year the property was built (or None).
        flood_high_risk:           True if flood zone is AE/VE (should already be gate-failed,
                                   but included for completeness).
        purchase_price:            Agreed purchase price.
        zip_median_price:          Median sale price for the zip code. If None, appraisal
                                   gap check is skipped.
        all_month_to_month:        True if all existing tenants are month-to-month.
        no_inspection_contingency: True if buyer is waiving inspection.

    Returns:
        RiskScore dataclass.
    """
    deductions: list[tuple[str, int]] = []
    flags:      list[str]             = []

    # ── Age of property ───────────────────────────────────────────────────────
    if year_built is not None:
        if year_built < 1950:
            deductions.append(("Built before 1950 — aging systems risk", 5))
            flags.append(f"Built {year_built}: high deferred maintenance risk (pre-1950 systems)")
        elif year_built < 1980:
            deductions.append(("Built 1950–1979 — moderate aging risk", 3))
            flags.append(f"Built {year_built}: moderate system age (1950–1979)")

    # ── Flood zone ────────────────────────────────────────────────────────────
    if flood_high_risk:
        deductions.append(("High-risk flood zone (AE/VE)", 10))
        flags.append("High-risk flood zone: insurance costs elevated, financing difficult")

    # ── VA appraisal gap risk ─────────────────────────────────────────────────
    if zip_median_price and zip_median_price > 0:
        gap_ratio = (purchase_price - zip_median_price) / zip_median_price
        if gap_ratio > 0.15:
            pct = gap_ratio * 100
            deductions.append((f"Price {pct:.0f}% above zip median — VA appraisal gap risk", 5))
            flags.append(
                f"Purchase price is {pct:.0f}% above zip median — "
                "VA appraisal may come in low, requiring price renegotiation"
            )

    # ── Tenant situation ──────────────────────────────────────────────────────
    if all_month_to_month:
        deductions.append(("All tenants month-to-month", 2))
        flags.append("All existing tenants are month-to-month — turnover risk")

    # ── Inspection contingency ────────────────────────────────────────────────
    if no_inspection_contingency:
        deductions.append(("No inspection contingency", 3))
        flags.append("Buyer waiving inspection contingency — hidden defect risk")

    total_deducted = sum(pts for _, pts in deductions)
    score = max(0, 30 - total_deducted)

    return RiskScore(
        score      = score,
        deductions = deductions,
        risk_flags = flags,
    )
