"""
scoring/return_score.py

Return Score (0–40) component of the Deal Score.

Inputs: conservative cash flow, DSCR, cash-on-cash return, and is_adu flag.
All scores are based on the CONSERVATIVE CASE only (worst stress test).

Scoring table:
  Cash flow ≥ $1,500/mo → 40 pts base
  Cash flow ≥ $1,000/mo → 32 pts base
  Cash flow ≥   $750/mo → 26 pts base
  Cash flow ≥   $500/mo → 18 pts base
  Cash flow <   $500/mo →  0 pts (disqualified)

  DSCR ≥ 1.4 → +5 pts
  DSCR ≥ 1.2 → +2 pts
  DSCR < 1.2 → disqualified (score = 0)

  CoC ≥ 8%   → +3 pts
  ADU bonus  → +3 pts (SFR with ADU house-hack; ADU provides income diversification)

Maximum: 40 pts (capped).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReturnScore:
    score:        int      # 0–40
    base_pts:     int      # cash-flow component
    dscr_pts:     int      # DSCR bonus
    coc_pts:      int      # CoC bonus
    adu_bonus:    int      # +3 pts for SFR_ADU house-hack strategy
    disqualified: bool     # True if DSCR < 1.2 or cash_flow < $500
    reason:       str      # plain-English explanation


def compute_return_score(
    conservative_monthly_cash_flow: float,
    dscr: float,
    cash_on_cash: float,
    is_adu: bool = False,
) -> ReturnScore:
    """
    Compute the Return Score (0–40).

    Args:
        conservative_monthly_cash_flow: Best strategy worst-case monthly cash flow ($).
        dscr:         Debt Service Coverage Ratio (NOI / annual debt service).
        cash_on_cash: Annual cash-on-cash return as a decimal (e.g. 0.08 = 8%).
        is_adu:       True for SFR_ADU properties (house-hack with ADU income).
                      Grants +3 points for income diversification vs single-unit SFH.

    Returns:
        ReturnScore dataclass.
    """
    cf = conservative_monthly_cash_flow

    # ── DSCR disqualifier ────────────────────────────────────────────────────
    if dscr < 1.2:
        return ReturnScore(
            score=0, base_pts=0, dscr_pts=0, coc_pts=0, adu_bonus=0,
            disqualified=True,
            reason=f"DSCR {dscr:.2f} is below the 1.2 minimum — deal disqualified.",
        )

    # ── Cash flow base points ─────────────────────────────────────────────────
    if cf >= 1_500:
        base_pts = 40
    elif cf >= 1_000:
        base_pts = 32
    elif cf >= 750:
        base_pts = 26
    elif cf >= 500:
        base_pts = 18
    else:
        return ReturnScore(
            score=0, base_pts=0, dscr_pts=0, coc_pts=0, adu_bonus=0,
            disqualified=True,
            reason=f"Conservative cash flow ${cf:,.0f}/mo is below the $500 minimum.",
        )

    # ── DSCR bonus ────────────────────────────────────────────────────────────
    dscr_pts = 5 if dscr >= 1.4 else 2   # already confirmed ≥ 1.2

    # ── CoC bonus ─────────────────────────────────────────────────────────────
    coc_pts = 3 if cash_on_cash >= 0.08 else 0

    # ── ADU bonus ─────────────────────────────────────────────────────────────
    adu_bonus = 3 if is_adu else 0

    score = min(40, base_pts + dscr_pts + coc_pts + adu_bonus)

    parts = [f"${cf:,.0f}/mo cash flow ({base_pts} pts)"]
    parts.append(f"DSCR {dscr:.2f} (+{dscr_pts} pts)")
    if coc_pts:
        parts.append(f"CoC {cash_on_cash:.1%} (+{coc_pts} pts)")
    if adu_bonus:
        parts.append("ADU income diversification (+3 pts)")

    return ReturnScore(
        score        = score,
        base_pts     = base_pts,
        dscr_pts     = dscr_pts,
        coc_pts      = coc_pts,
        adu_bonus    = adu_bonus,
        disqualified = False,
        reason       = "Return Score: " + ", ".join(parts) + f" = {score}/40.",
    )
