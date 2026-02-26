"""
scoring/deal_scorer.py

Combines Return Score + Risk Score + Confidence Score → Deal Score (0–100).
Applies high-priority alert logic and generates plain-English explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from scoring.confidence_score import ConfidenceScore, compute_confidence_score
from scoring.return_score import ReturnScore, compute_return_score
from scoring.risk_score import RiskScore, compute_risk_score


@dataclass
class DealScore:
    """Full scoring breakdown for a single deal."""
    deal_score:         int                # 0–100
    return_score:       ReturnScore
    risk_score:         RiskScore
    confidence_score:   ConfidenceScore

    is_high_priority:   bool              # True if all alert conditions met
    alert_reasons:      list[str]         # why alert was/wasn't triggered

    why_scored_high:    str               # 2–3 sentence plain-English explanation
    strategy_validated: bool              # at least one strategy has comps backing it

    scored_at:          datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ── High-priority alert conditions (ALL must be true) ─────────────────────────
#   - Conservative cash flow ≥ $500/mo
#   - DSCR ≥ 1.2
#   - Risk Score ≥ 18/30
#   - Confidence Score ≥ 20/30
#   - At least one strategy validated with comps

def _check_alert(
    cf: float,
    dscr: float,
    risk: RiskScore,
    conf: ConfidenceScore,
    strategy_validated: bool,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    passed  = True

    if cf < 500:
        reasons.append(f"Cash flow ${cf:,.0f}/mo < $500 minimum")
        passed = False
    if dscr < 1.2:
        reasons.append(f"DSCR {dscr:.2f} < 1.2 minimum")
        passed = False
    if risk.score < 18:
        reasons.append(f"Risk Score {risk.score}/30 < 18 minimum")
        passed = False
    if conf.score < 20:
        reasons.append(f"Confidence Score {conf.score}/30 < 20 minimum")
        passed = False
    if not strategy_validated:
        reasons.append("No rental strategy has been validated with comps")
        passed = False

    return passed, reasons


def _generate_explanation(
    deal_score: int,
    return_s: ReturnScore,
    risk_s: RiskScore,
    conf_s: ConfidenceScore,
    cf: float,
    dscr: float,
    is_high_priority: bool,
) -> str:
    """Generate a 2–3 sentence plain-English deal summary."""
    parts: list[str] = []

    if deal_score >= 75:
        parts.append(
            f"This property scores {deal_score}/100, placing it in the top tier of analyzed deals."
        )
    elif deal_score >= 60:
        parts.append(
            f"This property scores {deal_score}/100 — a solid deal worth closer review."
        )
    else:
        parts.append(
            f"This property scores {deal_score}/100 — marginal; proceed with caution."
        )

    if not return_s.disqualified:
        parts.append(
            f"Conservative cash flow of ${cf:,.0f}/mo with a DSCR of {dscr:.2f} "
            f"provides a meaningful debt-service buffer."
        )

    if risk_s.risk_flags:
        flag_summary = "; ".join(risk_s.risk_flags[:2])
        parts.append(f"Key risks to review: {flag_summary}.")
    elif is_high_priority:
        parts.append(
            "All hard alert conditions are met: strong cash flow, solid DSCR, "
            "low risk profile, and validated comps."
        )

    return " ".join(parts)


def score_deal(
    # Return inputs
    conservative_monthly_cash_flow: float,
    dscr: float,
    cash_on_cash: float,
    # Risk inputs
    year_built: int | None,
    flood_high_risk: bool,
    purchase_price: float,
    zip_median_price: float | None = None,
    all_month_to_month: bool = False,
    no_inspection_contingency: bool = False,
    # Confidence inputs
    raw_confidence: float = 0.5,
    scraped_at: datetime | None = None,
    comps_count: int = 0,
    address: str | None = None,
    num_units: int | None = None,
    strategy_validated: bool = False,
) -> DealScore:
    """
    Compute the full Deal Score for a property.

    Args:
        conservative_monthly_cash_flow: Best strategy worst-case monthly cash flow.
        dscr:             Debt Service Coverage Ratio (conservative case).
        cash_on_cash:     Cash-on-cash return as decimal.
        year_built:       Year property was built (or None).
        flood_high_risk:  True if in AE/VE flood zone.
        purchase_price:   Property purchase price.
        zip_median_price: Median price for the zip (for appraisal gap check).
        all_month_to_month: All existing tenants are month-to-month.
        no_inspection_contingency: Buyer waiving inspection.
        raw_confidence:   Normalization confidence float 0.0–1.0.
        scraped_at:       When the listing was scraped (defaults to now).
        comps_count:      Number of rental comps available.
        address:          Property address string.
        num_units:        Number of units.
        strategy_validated: At least one strategy backed by comps.

    Returns:
        DealScore with full component breakdown and alert status.
    """
    if scraped_at is None:
        scraped_at = datetime.now(tz=timezone.utc)

    return_s = compute_return_score(conservative_monthly_cash_flow, dscr, cash_on_cash)
    risk_s   = compute_risk_score(
        year_built, flood_high_risk, purchase_price,
        zip_median_price, all_month_to_month, no_inspection_contingency,
    )
    conf_s   = compute_confidence_score(
        raw_confidence, scraped_at, comps_count,
        purchase_price, address, num_units,
    )

    deal_score = min(100, return_s.score + risk_s.score + conf_s.score)

    is_hp, alert_reasons = _check_alert(
        conservative_monthly_cash_flow, dscr, risk_s, conf_s, strategy_validated
    )

    explanation = _generate_explanation(
        deal_score, return_s, risk_s, conf_s,
        conservative_monthly_cash_flow, dscr, is_hp,
    )

    return DealScore(
        deal_score         = deal_score,
        return_score       = return_s,
        risk_score         = risk_s,
        confidence_score   = conf_s,
        is_high_priority   = is_hp,
        alert_reasons      = alert_reasons,
        why_scored_high    = explanation,
        strategy_validated = strategy_validated,
    )
