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
class StrategyMetrics:
    """Per-strategy underwriting inputs for deal scoring."""
    cash_flow: float      # worst-case monthly cash flow (across all stress tests)
    dscr: float           # base-case DSCR
    cash_on_cash: float   # base-case cash-on-cash return


@dataclass
class DealScore:
    """Full scoring breakdown for a single deal."""
    deal_score:         int                # 0–100
    return_score:       ReturnScore
    risk_score:         RiskScore
    confidence_score:   ConfidenceScore

    winning_strategy:   str               # "LTR", "MTR", or "STR"
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
    winning_strategy: str,
) -> str:
    """Generate a 2–3 sentence plain-English deal summary."""
    parts: list[str] = []

    strategy_label = {"LTR": "long-term rental", "MTR": "medium-term rental", "STR": "short-term rental"}.get(winning_strategy, winning_strategy)

    if deal_score >= 75:
        parts.append(
            f"This property scores {deal_score}/100 as a {strategy_label}, "
            f"placing it in the top tier of analyzed deals."
        )
    elif deal_score >= 60:
        parts.append(
            f"This property scores {deal_score}/100 — a solid {strategy_label} deal worth closer review."
        )
    else:
        parts.append(
            f"This property scores {deal_score}/100 as a {strategy_label} — marginal; proceed with caution."
        )

    if not return_s.disqualified:
        parts.append(
            f"Conservative cash flow of ${cf:,.0f}/mo with a DSCR of {dscr:.2f} "
            f"provides a meaningful debt-service buffer."
        )

    if return_s.adu_bonus:
        parts.append("ADU income diversification adds resilience vs single-unit SFH.")

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
    # Per-strategy return inputs — all three evaluated; highest-scoring wins
    ltr: StrategyMetrics,
    mtr: StrategyMetrics,
    str_: StrategyMetrics,
    # Risk inputs (property-level, same for all strategies)
    year_built: int | None,
    flood_high_risk: bool,
    purchase_price: float,
    zip_median_price: float | None = None,
    all_month_to_month: bool = False,
    no_inspection_contingency: bool = False,
    # Confidence inputs (property-level, same for all strategies)
    raw_confidence: float = 0.5,
    scraped_at: datetime | None = None,
    comps_count: int = 0,
    address: str | None = None,
    num_units: int | None = None,
    strategy_validated: bool = False,
    confidence_penalty: bool = False,
    # Property type (for SFH differentiation)
    property_type: str | None = None,
) -> DealScore:
    """
    Compute the full Deal Score for a property by evaluating LTR, MTR, and STR
    independently, then returning the score of the highest-scoring strategy.

    Risk and Confidence scores are property-level and computed once.
    Return Score is computed per strategy; the strategy with the highest
    combined deal score wins and is labelled on the returned DealScore.

    Args:
        ltr:              LTR worst-case cash flow, base DSCR, base CoC.
        mtr:              MTR worst-case cash flow, base DSCR, base CoC.
        str_:             STR worst-case cash flow, base DSCR, base CoC.
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
        confidence_penalty: If True, Confidence Score is hard-capped at 19.
        property_type:    PropertyType string (e.g. "sfr", "sfr_adu", "duplex").

    Returns:
        DealScore for the winning strategy, with winning_strategy labelled.
    """
    if scraped_at is None:
        scraped_at = datetime.now(tz=timezone.utc)

    pt = (property_type or "").lower()
    is_adu = pt == "sfr_adu"

    # Risk and Confidence are property-level — compute once
    risk_s = compute_risk_score(
        year_built, flood_high_risk, purchase_price,
        zip_median_price, all_month_to_month, no_inspection_contingency,
        property_type=property_type,
    )
    conf_s = compute_confidence_score(
        raw_confidence, scraped_at, comps_count,
        purchase_price, address, num_units,
        confidence_penalty=confidence_penalty,
    )

    # Evaluate each strategy independently; pick the one with the highest deal score
    candidates = [
        ("LTR", ltr),
        ("MTR", mtr),
        ("STR", str_),
    ]

    best_name:     str        = "LTR"
    best_score:    int        = -1
    best_return_s: ReturnScore | None = None
    best_metrics:  StrategyMetrics | None = None

    for name, metrics in candidates:
        return_s  = compute_return_score(metrics.cash_flow, metrics.dscr, metrics.cash_on_cash, is_adu=is_adu)
        ds        = min(100, return_s.score + risk_s.score + conf_s.score)
        if ds > best_score:
            best_score    = ds
            best_name     = name
            best_return_s = return_s
            best_metrics  = metrics

    assert best_return_s is not None and best_metrics is not None  # always set after loop

    is_hp, alert_reasons = _check_alert(
        best_metrics.cash_flow, best_metrics.dscr, risk_s, conf_s, strategy_validated
    )

    explanation = _generate_explanation(
        best_score, best_return_s, risk_s, conf_s,
        best_metrics.cash_flow, best_metrics.dscr, is_hp,
        winning_strategy=best_name,
    )

    return DealScore(
        deal_score        = best_score,
        return_score      = best_return_s,
        risk_score        = risk_s,
        confidence_score  = conf_s,
        winning_strategy  = best_name,
        is_high_priority  = is_hp,
        alert_reasons     = alert_reasons,
        why_scored_high   = explanation,
        strategy_validated = strategy_validated,
    )
