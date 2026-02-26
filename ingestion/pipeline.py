"""
ingestion/pipeline.py

Full analysis pipeline: ingestion → neighborhood → underwriting → scoring → delivery.

Called by the daily scan job after raw data is collected. Accepts a ScanResult
and returns a list of deal dicts ready for the UI / DB.
"""

from __future__ import annotations

from typing import Any

import structlog

from ingestion.run_scan import ScanResult
from ingestion.va_rates import fetch_va_rate
from neighborhood.crime_grade import get_crime_grade
from neighborhood.flood_zone import get_flood_zone
from neighborhood.gates import evaluate_gates
from neighborhood.hospital_proximity import get_hospital_proximity
from neighborhood.liveability import compute_liveability
from neighborhood.street_view import get_street_view_urls
from neighborhood.walk_score import get_walk_score
from normalization.schema import PropertyRecord, RentalComp
from scoring.deal_scorer import score_deal
from scoring.ranker import rank_deals
from underwriting.calculator import underwrite, Scenario

logger = structlog.get_logger(__name__)


# ── Comp lookup helpers ────────────────────────────────────────────────────────

def _ltr_comps_for(record: PropertyRecord, ltr_comps: list[RentalComp]) -> list[RentalComp]:
    """Return LTR comps in the same zip code."""
    return [c for c in ltr_comps if c.zip_code == record.zip_code]


def _mtr_comps_for(record: PropertyRecord, mtr_comps: list[RentalComp]) -> list[RentalComp]:
    """Return MTR comps in the same zip code."""
    return [c for c in mtr_comps if c.zip_code == record.zip_code]


def _median_rate(comps: list[RentalComp]) -> float | None:
    rates = sorted(c.monthly_rate for c in comps if c.monthly_rate and c.monthly_rate > 0)
    if not rates:
        return None
    mid = len(rates) // 2
    return rates[mid]


# ── Rentcast fallback ──────────────────────────────────────────────────────────

def _try_rentcast_rates(
    record: PropertyRecord,
    assumptions: dict,
) -> tuple[float | None, float | None]:
    """
    Attempt to fetch zip-level rent estimates from Rentcast as a fallback
    when no live comps are available.

    Returns:
        (ltr_rate_total, mtr_rate_total) or (None, None) on failure.
        Rates are total for all units combined.
    """
    try:
        import os
        from ingestion.rentcast_client import RentcastClient, RentcastError

        api_key = os.environ.get("RENTCAST_API_KEY", "")
        if not api_key:
            return None, None

        client = RentcastClient(api_key=api_key)
        num_units = record.num_units or 1

        try:
            estimate = client.get_rent_estimate(
                address=record.address,
                property_type=str(record.property_type),
                bedrooms=record.beds,
                bathrooms=record.baths,
            )
            ltr_rate = estimate.get("rent") or estimate.get("rentRangeLow")
            if ltr_rate:
                return float(ltr_rate) * num_units, None
        except RentcastError as e:
            logger.warning("rentcast_fallback_error", address=record.address, error=str(e))

        return None, None

    except Exception as exc:
        logger.warning("rentcast_fallback_unexpected", address=record.address, error=str(exc))
        return None, None


# ── Neighborhood enrichment ────────────────────────────────────────────────────

def _enrich_neighborhood(record: PropertyRecord, walkscore_key: str = "", maps_key: str = "") -> dict:
    """
    Run all neighborhood APIs for a single property.
    Catches all errors gracefully — missing data degrades confidence, not pipeline.
    """
    lat  = record.lat  or 30.32
    lon  = record.lon  or -81.66
    addr = record.address

    # Walk Score
    ws_result = None
    try:
        if walkscore_key:
            ws_result = get_walk_score(addr, lat, lon, api_key=walkscore_key)
    except Exception as e:
        logger.warning("walk_score_error", address=addr, error=str(e))

    walk_score = ws_result["walk_score"] if ws_result else None

    # Flood zone
    try:
        flood = get_flood_zone(lat, lon)
    except Exception as e:
        logger.warning("flood_zone_error", address=addr, error=str(e))
        from neighborhood.flood_zone import FloodZoneResult
        flood = FloodZoneResult(flood_zone="UNKNOWN", is_high_risk=False, sfha=False, panel_number="")

    # Crime grade
    try:
        crime = get_crime_grade(record.zip_code)
    except Exception as e:
        logger.warning("crime_grade_error", zip_code=record.zip_code, error=str(e))
        from neighborhood.crime_grade import CrimeGradeResult
        crime = CrimeGradeResult(grade="UNKNOWN", passes_gate=False, low_confidence=True)

    # Hospital proximity
    hospital = get_hospital_proximity(lat, lon)

    # Liveability
    liveability = compute_liveability(walk_score, hospital, crime["grade"], flood)

    # Street View URLs (no live call — just URL generation)
    sv_urls = get_street_view_urls(lat, lon, addr, api_key=maps_key)

    # Gate evaluation
    gate_result = evaluate_gates(record, crime, flood)

    return {
        "walk_score":       walk_score,
        "flood_zone":       flood["flood_zone"],
        "flood_high_risk":  flood["is_high_risk"],
        "crime_grade":      crime["grade"],
        "crime_low_confidence": crime["low_confidence"],
        "hospital_dist_miles": hospital["distance_miles"],
        "closest_hospital": hospital["closest_hospital"],
        "proximity_score":  hospital["proximity_score"],
        "liveability_score": liveability["total"],
        "street_view_url":  sv_urls["street_view_image_url"],
        "satellite_url":    sv_urls["satellite_view_url"],
        "maps_link":        sv_urls["google_maps_link"],
        "passed_gates":     gate_result.passed,
        "failed_gates":     gate_result.failed_gates,
        "liveability":      liveability,
    }


# ── Underwriting helper ────────────────────────────────────────────────────────

def _build_prop_dict(
    record: PropertyRecord,
    assumptions: dict,
) -> dict[str, Any]:
    """
    Build the base underwriting input dict from a PropertyRecord.

    Note: monthly_rent and mtr_monthly_rate are NOT injected here;
    they are resolved inside underwrite() from the comps parameter.
    zip_code and beds are included so the comp-selector inside underwrite()
    can filter by zip and match by bed count.
    """
    num_units = record.num_units or 1

    return {
        "purchase_price":    record.price,
        "num_units":         num_units,
        "zip_code":          record.zip_code,
        "beds":              record.beds or 0,
        "interest_rate":     assumptions.get("interest_rate", 0.075),
        "insurance_annual":  record.price * assumptions.get("insurance_pct", 0.005),
        "ltr_vacancy_rate":  assumptions.get("ltr_vacancy", 0.08),
        "mtr_vacancy_rate":  assumptions.get("mtr_vacancy", 0.10),
        "str_vacancy_rate":  assumptions.get("str_vacancy", 0.25),
        "ltr_mgmt_rate":     assumptions.get("mgmt_rate", 0.08),
        "ltr_maintenance_rate": assumptions.get("maintenance_rate", 0.01),
        "ltr_capex_rate":    assumptions.get("capex_rate", 0.005),
        "va_funding_fee_pct": assumptions.get("va_funding_fee_pct", 0.0215),
    }


# ── Deal card builder ──────────────────────────────────────────────────────────

def _build_deal_card(
    record: PropertyRecord,
    neighborhood: dict,
    uw_result,
    deal_score,
    comps_count: int,
) -> dict[str, Any]:
    """Assemble the full deal card dict for the UI and DB."""
    base_ltr = uw_result.ltr.base
    base_mtr = uw_result.mtr.base
    base_str = uw_result.str_.base

    # Conservative = worst stress case across all strategies
    worst_ltr = uw_result.ltr.worst_case_cash_flow
    worst_mtr = uw_result.mtr.worst_case_cash_flow
    worst_str = uw_result.str_.worst_case_cash_flow

    strategies_raw = [
        {"name": "LTR", "cash_flow": worst_ltr, "dscr": base_ltr.dscr, "coc": base_ltr.cash_on_cash, "noi": base_ltr.noi},
        {"name": "MTR", "cash_flow": worst_mtr, "dscr": base_mtr.dscr, "coc": base_mtr.cash_on_cash, "noi": base_mtr.noi},
        {"name": "STR", "cash_flow": worst_str, "dscr": base_str.dscr, "coc": base_str.cash_on_cash, "noi": base_str.noi},
    ]
    strategies_ranked = sorted(strategies_raw, key=lambda s: s["cash_flow"], reverse=True)

    # Stress tests (use best strategy)
    best_uw = uw_result.best_strategy()
    stress_tests = []
    for sc in list(Scenario):
        if sc.value == "base":
            continue
        res = uw_result.ltr.scenarios.get(sc) or uw_result.mtr.scenarios.get(sc)
        if res:
            stress_tests.append({
                "label":      res.scenario_label,
                "cash_flow":  res.monthly_cash_flow,
                "dscr":       res.dscr,
            })

    # Refi at 5.5%
    from underwriting.calculator import _monthly_payment
    refi_pmt = _monthly_payment(uw_result.loan_amount, 0.055, 30)

    return {
        "canonical_id":       record.canonical_id,
        "source_id":          record.source_id,
        "address":            record.address,
        "city":               record.city,
        "zip_code":           record.zip_code,
        "price":              record.price,
        "property_type":      record.property_type.value if hasattr(record.property_type, "value") else str(record.property_type),
        "num_units":          record.num_units,
        "beds":               record.beds,
        "baths":              record.baths,
        "sqft":               record.sqft,
        "year_built":         record.year_built,
        "days_on_market":     record.days_on_market,

        # Neighborhood
        **neighborhood,

        # VA Loan
        "loan_amount":        uw_result.loan_amount,
        "funding_fee":        uw_result.loan_amount - record.price,
        "monthly_payment":    uw_result.monthly_payment,
        "total_piti":         uw_result.monthly_payment + (record.price * 0.0077 / 12) + (record.price * 0.005 / 12),
        "cash_to_close":      uw_result.total_cash_invested,
        "refi_payment_55":    refi_pmt,

        # Strategies
        "strategies":         strategies_ranked,
        "top_strategy":       strategies_ranked[0]["name"] if strategies_ranked else "—",
        "top_cash_flow":      strategies_ranked[0]["cash_flow"] if strategies_ranked else 0.0,
        "stress_tests":       stress_tests,

        # Comp sourcing
        "rent_source":        uw_result.rent_source.value,

        # Scoring
        "deal_score":         deal_score.deal_score,
        "return_score":       deal_score.return_score.score,
        "risk_score":         deal_score.risk_score.score,
        "confidence_score_pts": deal_score.confidence_score.score,
        "is_high_priority":   deal_score.is_high_priority,
        "why_scored_high":    deal_score.why_scored_high,
        "risk_flags":         deal_score.risk_score.risk_flags,
        "comps_count":        comps_count,
    }


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(
    scan_result: ScanResult,
    assumptions: dict | None = None,
    walkscore_key: str = "",
    maps_key: str = "",
    alert_threshold: int = 85,
    alert_recipient: str = "",
    app_base_url: str = "",
) -> dict[str, Any]:
    """
    Run the full analysis pipeline on a ScanResult.

    Returns a dict with keys: inbox, alerts, rejected, scan_summary.
    """
    if assumptions is None:
        assumptions = {}

    # ── Fetch live VA rate once for the whole pipeline run ─────────────────────
    va_rate_result = fetch_va_rate()
    if "interest_rate" not in assumptions:
        assumptions = {**assumptions, "interest_rate": va_rate_result.rate}
    if va_rate_result.is_stale:
        logger.warning(
            "va_rate_stale",
            rate_pct=f"{va_rate_result.rate:.3%}",
            fetched_at=va_rate_result.fetched_at.isoformat(),
        )
    logger.info(
        "va_rate_applied",
        rate_pct=f"{va_rate_result.rate:.3%}",
        source=va_rate_result.source,
    )

    inbox_deals:    list[dict] = []
    rejected_props: list[dict] = []
    alert_deals:    list[dict] = []
    scored_pairs:   list       = []
    failed_ids:     set        = set()

    logger.info("pipeline_start", listings=scan_result.total_listings)

    for record in scan_result.listings:
        try:
            # ── Neighborhood ──────────────────────────────────────────────
            neighborhood = _enrich_neighborhood(record, walkscore_key, maps_key)

            if not neighborhood["passed_gates"]:
                rejected_props.append({
                    "canonical_id": record.canonical_id,
                    "address":      record.address,
                    "zip_code":     record.zip_code,
                    "price":        record.price,
                    "property_type": str(record.property_type),
                    "num_units":    record.num_units,
                    "crime_grade":  neighborhood["crime_grade"],
                    "flood_zone":   neighborhood["flood_zone"],
                    "flood_high_risk": neighborhood["flood_high_risk"],
                    "failed_gates": neighborhood["failed_gates"],
                })
                failed_ids.add(record.canonical_id)
                continue

            # ── Comps ──────────────────────────────────────────────────────
            ltr_comps = _ltr_comps_for(record, scan_result.ltr_comps)
            mtr_comps = _mtr_comps_for(record, scan_result.mtr_comps)
            all_comps = ltr_comps + mtr_comps
            comps_count = len(ltr_comps) + len(mtr_comps)

            # ── Underwriting ───────────────────────────────────────────────
            prop_dict = _build_prop_dict(record, assumptions)

            # If no live comps, try Rentcast as fallback before underwriting
            if not all_comps:
                rentcast_ltr, rentcast_mtr = _try_rentcast_rates(record, assumptions)
                if rentcast_ltr:
                    prop_dict["monthly_rent"] = rentcast_ltr
                    logger.info("rentcast_fallback_used", address=record.address, ltr=rentcast_ltr)
                if rentcast_mtr:
                    prop_dict["mtr_monthly_rate"] = rentcast_mtr

            uw_result = underwrite(prop_dict, comps=all_comps if all_comps else None)

            # Best conservative CF across strategies
            cf = max(
                uw_result.ltr.worst_case_cash_flow,
                uw_result.mtr.worst_case_cash_flow,
                uw_result.str_.worst_case_cash_flow,
            )
            best_base = uw_result.best_strategy(Scenario.BASE)

            # ── Scoring ────────────────────────────────────────────────────
            deal_score = score_deal(
                conservative_monthly_cash_flow = cf,
                dscr            = best_base.dscr,
                cash_on_cash    = best_base.cash_on_cash,
                year_built      = record.year_built,
                flood_high_risk = neighborhood["flood_high_risk"],
                purchase_price  = record.price,
                raw_confidence  = record.confidence_score,
                scraped_at      = record.scraped_at,
                comps_count     = comps_count,
                address         = record.address,
                num_units       = record.num_units,
                strategy_validated = comps_count >= 3,
                confidence_penalty = uw_result.confidence_penalty,
            )

            # ── Deal card ──────────────────────────────────────────────────
            card = _build_deal_card(record, neighborhood, uw_result, deal_score, comps_count)
            scored_pairs.append((record, deal_score))

        except Exception as exc:
            logger.error("pipeline_property_error", address=record.address, error=str(exc))
            continue

    # ── Rank and partition ─────────────────────────────────────────────────────
    ranked = rank_deals(scored_pairs, failed_gate_ids=failed_ids)

    def _cards_from_pairs(pairs):
        cards = []
        for rec, ds in pairs:
            try:
                ltr_comps   = _ltr_comps_for(rec, scan_result.ltr_comps)
                mtr_comps   = _mtr_comps_for(rec, scan_result.mtr_comps)
                all_comps   = ltr_comps + mtr_comps
                prop_dict   = _build_prop_dict(rec, assumptions)

                if not all_comps:
                    rentcast_ltr, rentcast_mtr = _try_rentcast_rates(rec, assumptions)
                    if rentcast_ltr:
                        prop_dict["monthly_rent"] = rentcast_ltr
                    if rentcast_mtr:
                        prop_dict["mtr_monthly_rate"] = rentcast_mtr

                uw_result   = underwrite(prop_dict, comps=all_comps if all_comps else None)
                neighborhood = _enrich_neighborhood(rec, walkscore_key, maps_key)
                card = _build_deal_card(rec, neighborhood, uw_result, ds, len(ltr_comps) + len(mtr_comps))
                cards.append(card)
            except Exception:
                pass
        return cards

    alert_cards = _cards_from_pairs(ranked.alerts)
    inbox_cards = _cards_from_pairs(ranked.inbox)

    # ── Send alerts ────────────────────────────────────────────────────────────
    if alert_recipient:
        from delivery.alerts import maybe_send_alert
        for card in alert_cards:
            maybe_send_alert(card, threshold=alert_threshold,
                             recipient=alert_recipient, base_url=app_base_url)

    return {
        "alerts":       alert_cards,
        "inbox":        inbox_cards,
        "rejected":     rejected_props,
        "scan_summary": {
            "total_scanned":  scan_result.total_listings,
            "passed_gates":   len(scored_pairs),
            "alerts":         len(alert_cards),
            "errors":         scan_result.errors,
            "va_rate_pct":    round(va_rate_result.rate * 100, 3),
            "va_rate_source": va_rate_result.source,
            "va_rate_stale":  va_rate_result.is_stale,
        },
    }
