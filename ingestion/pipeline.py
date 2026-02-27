"""
ingestion/pipeline.py

Full analysis pipeline: ingestion → neighborhood → underwriting → scoring → delivery.

Called by the daily scan job after raw data is collected. Accepts a ScanResult
and returns a list of deal dicts ready for the UI / DB.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import structlog

from ingestion.run_scan import ScanResult
from ingestion.va_rates import fetch_va_rate
from neighborhood.crime_grade import get_crime_grade, get_cached_crime_grade, CrimeGradeResult
from neighborhood.flood_zone import get_flood_zone
from neighborhood.gates import evaluate_gates
from neighborhood.hospital_proximity import get_hospital_proximity
from neighborhood.liveability import compute_liveability
from neighborhood.street_view import get_street_view_urls, score_street_view
from neighborhood.walk_score import get_walk_score
from normalization.schema import PropertyRecord, RentalComp
from scoring.deal_scorer import score_deal
from scoring.ranker import rank_deals
from underwriting.calculator import underwrite, Scenario

logger = structlog.get_logger(__name__)

# Score threshold below which we skip the Anthropic visual scoring API call
_VISUAL_SCORE_MIN_DEAL_SCORE = 50


# ── Comp lookup helpers ────────────────────────────────────────────────────────

def _ltr_comps_for(record: PropertyRecord, ltr_comps: list[RentalComp]) -> list[RentalComp]:
    return [c for c in ltr_comps if c.zip_code == record.zip_code]


def _mtr_comps_for(record: PropertyRecord, mtr_comps: list[RentalComp]) -> list[RentalComp]:
    return [c for c in mtr_comps if c.zip_code == record.zip_code]


def _median_rate(comps: list[RentalComp]) -> float | None:
    rates = sorted(c.monthly_rate for c in comps if c.monthly_rate and c.monthly_rate > 0)
    if not rates:
        return None
    return rates[len(rates) // 2]


# ── Rentcast — cache + comps + estimate ───────────────────────────────────────

_RENTCAST_CACHE_TTL_DAYS = 7


def _sync_db(coro: Any, default: Any = None) -> Any:
    """Run an async DB coroutine from synchronous pipeline code.

    Uses a thread-pool executor when a loop is already running (APScheduler),
    falls back to asyncio.run() otherwise.  Always returns `default` on error.
    """
    import asyncio
    try:
        try:
            asyncio.get_running_loop()
            # A loop is already running — delegate to a worker thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result(timeout=10)
        except RuntimeError:
            # No running loop — safe to call asyncio.run() directly.
            return asyncio.run(coro)
    except Exception as exc:
        logger.debug("sync_db_error", error=str(exc))
        return default


def _dedupe_comps_by_address(comps: list[RentalComp]) -> list[RentalComp]:
    """Return comps deduplicated by normalised address; first occurrence wins."""
    seen: set[str] = set()
    out: list[RentalComp] = []
    for comp in comps:
        key = comp.address.lower().strip()
        if key not in seen:
            seen.add(key)
            out.append(comp)
    return out


async def _store_rentcast_comps_async(
    comps: list[RentalComp], property_id: str
) -> None:
    """Persist Rentcast comps to rental_comps (best-effort, no raise)."""
    from db.connection import get_session
    from db.repositories.comp_repo import upsert_comp
    async with get_session() as session:
        for comp in comps:
            await upsert_comp(session, {
                "canonical_id":       comp.canonical_id,
                "property_id":        property_id,
                "source":             comp.source.value,
                "source_id":          comp.source_id,
                "scraped_at":         comp.scraped_at,
                "address":            comp.address,
                "zip_code":           comp.zip_code,
                "lat":                comp.lat,
                "lon":                comp.lon,
                "beds":               comp.beds,
                "baths":              comp.baths,
                "sqft":               comp.sqft,
                "rental_strategy":    comp.rental_strategy.value,
                "monthly_rate":       comp.monthly_rate,
                "adr":                comp.adr,
                "utilities_included": comp.utilities_included,
                "raw":                comp.raw,
            })


def _try_rentcast_cached(
    record: PropertyRecord,
    apify_comp_count: int,
) -> tuple[list[RentalComp], float | None]:
    """Return (supplemental_comps, ltr_estimate) with 7-day DB caching.

    Logic:
    1. Check rentcast_cache — if entry is < 7 days old, return cached ltr_estimate
       with no API calls (comps from previous run are already stored in rental_comps).
    2. Cache miss: call get_rent_comps() when apify_comp_count < 3 and lat/lon known,
       normalise results to RentalComp, merge into rental_comps table.
    3. If still no comps after supplement, call get_rent_estimate() as last resort.
    4. Upsert rentcast_cache with fetched_at = NOW().
    """
    from datetime import datetime, timezone

    api_key = os.environ.get("RENTCAST_API_KEY", "")
    if not api_key:
        return [], None

    # ── 1. Check cache ─────────────────────────────────────────────────────────
    cached: dict | None = None
    try:
        async def _read_cache() -> dict | None:
            from db.connection import get_session
            from db.repositories.rentcast_cache_repo import get_cached_rentcast
            async with get_session() as session:
                return await get_cached_rentcast(session, record.canonical_id)

        cached = _sync_db(_read_cache())
    except Exception as exc:
        logger.debug("rentcast_cache_read_error", error=str(exc))

    if cached:
        fetched_at = cached["fetched_at"]
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - fetched_at).days
        if age_days < _RENTCAST_CACHE_TTL_DAYS:
            logger.info(
                "rentcast_cache_hit",
                canonical_id=record.canonical_id,
                age_days=age_days,
            )
            return [], cached.get("ltr_estimate")

    # ── 2. Cache miss — call live API ──────────────────────────────────────────
    rc_comps: list[RentalComp] = []
    ltr_estimate: float | None = None

    try:
        from ingestion.rentcast_client import RentcastClient, RentcastError
        from normalization.normalizer import normalize_rentcast_comp

        client = RentcastClient(api_key=api_key)
        num_units = record.num_units or 1

        # get_rent_comps() — supplement when Apify is thin
        if apify_comp_count < 3 and record.lat and record.lon:
            try:
                beds_per_unit = max(1, round((record.beds or num_units) / num_units))
                raw_comps = client.get_rent_comps(
                    lat=record.lat,
                    lon=record.lon,
                    beds=beds_per_unit,
                    radius_miles=0.5,
                    limit=25,
                )
                rc_comps = [
                    normalize_rentcast_comp(r, subject_zip=record.zip_code)
                    for r in raw_comps
                    if r.get("price")
                ]
                logger.info(
                    "rentcast_comps_fetched",
                    address=record.address,
                    count=len(rc_comps),
                )
                if rc_comps:
                    _sync_db(
                        _store_rentcast_comps_async(rc_comps, record.canonical_id)
                    )
            except RentcastError as exc_comps:
                logger.warning(
                    "rentcast_comps_error",
                    address=record.address,
                    error=str(exc_comps),
                )

        # get_rent_estimate() — last resort when there are still no comps
        if apify_comp_count + len(rc_comps) == 0:
            try:
                estimate = client.get_rent_estimate(
                    address=record.address,
                    zip_code=record.zip_code,
                    property_type=str(record.property_type),
                    bedrooms=record.beds,
                    bathrooms=record.baths,
                )
                if estimate:
                    raw_rate = estimate.get("rent") or estimate.get("rentRangeLow")
                    if raw_rate:
                        ltr_estimate = float(raw_rate) * num_units
                logger.info(
                    "rentcast_estimate_fetched",
                    address=record.address,
                    ltr=ltr_estimate,
                )
            except RentcastError as exc_est:
                logger.warning(
                    "rentcast_estimate_error",
                    address=record.address,
                    error=str(exc_est),
                )

    except Exception as exc:
        logger.warning("rentcast_unexpected", address=record.address, error=str(exc))

    # ── 3. Update cache ────────────────────────────────────────────────────────
    try:
        async def _write_cache() -> None:
            from db.connection import get_session
            from db.repositories.rentcast_cache_repo import upsert_rentcast_cache
            async with get_session() as session:
                await upsert_rentcast_cache(
                    session, record.canonical_id, record.zip_code, ltr_estimate
                )

        _sync_db(_write_cache())
    except Exception as exc:
        logger.debug("rentcast_cache_write_error", error=str(exc))

    return rc_comps, ltr_estimate


# ── Airbnb STR comps ───────────────────────────────────────────────────────────

def _try_airbnb_str_comps(
    record: PropertyRecord,
) -> "tuple[StrCompResult | None, bool]":
    """
    Fetch Airbnb STR comps for a property, with weekly DB cache.

    Cache key: (zip_code, beds_per_unit, ISO-week-number).
    On a cache hit the actor is NOT called — the same week's results are reused
    across all properties with the same zip + bed count.

    Returns:
        (StrCompResult | None, str_validated)
    """
    try:
        from ingestion.airbnb_comps import get_str_comps, StrCompResult
        from db.repositories.airbnb_cache_repo import (
            get_cached_str_comps,
            upsert_str_comp_cache,
        )
        from db.connection import get_session

        if not record.zip_code:
            return None, False

        num_units     = record.num_units or 1
        beds          = record.beds or 0
        beds_per_unit = max(1, round(beds / num_units))
        week_number   = datetime.now(tz=timezone.utc).isocalendar()[1]

        # ── Cache check ───────────────────────────────────────────────────────
        async def _check_cache():
            async with get_session() as session:
                return await get_cached_str_comps(
                    session, record.zip_code, beds_per_unit, week_number
                )

        cached = _sync_db(_check_cache())
        if cached is not None:
            logger.info(
                "airbnb_cache_hit",
                zip_code=record.zip_code,
                bedrooms=beds_per_unit,
                week=week_number,
            )
            result = StrCompResult(
                comp_count=cached["comp_count"],
                median_adr=cached["median_adr"],
                estimated_occupancy=cached["estimated_occupancy"],
                gross_monthly=cached["gross_monthly"],
                net_monthly=cached["net_monthly"],
                confidence=cached["confidence"],
                str_validated=bool(cached["str_validated"]),
            )
            return result, result.str_validated

        # ── Live actor call ───────────────────────────────────────────────────
        result = get_str_comps(record.zip_code, beds_per_unit)

        async def _write_cache():
            async with get_session() as session:
                await upsert_str_comp_cache(
                    session,
                    zip_code=record.zip_code,
                    bedrooms=beds_per_unit,
                    week_number=week_number,
                    comp_count=result.comp_count,
                    median_adr=result.median_adr,
                    estimated_occupancy=result.estimated_occupancy,
                    gross_monthly=result.gross_monthly,
                    net_monthly=result.net_monthly,
                    confidence=result.confidence,
                    str_validated=result.str_validated,
                    comps_json=result.comps[:20] if result.comps else [],
                )
                await session.commit()

        try:
            _sync_db(_write_cache())
        except Exception as exc:
            logger.debug("airbnb_cache_write_error", error=str(exc))

        return result, result.str_validated

    except Exception as exc:
        logger.warning("airbnb_str_error", address=record.address, error=str(exc))
        return None, False


# ── Redfin sold comps ──────────────────────────────────────────────────────────

def _try_redfin_zip_median(zip_code: str) -> float | None:
    """Fetch zip-level sold price median from Redfin via Apify. Returns None on failure."""
    try:
        from ingestion.redfin_sold import get_zip_median_sold_price
        result = get_zip_median_sold_price(zip_code)
        if result.median_sold_price:
            logger.info("redfin_zip_median",
                        zip_code=zip_code, median=result.median_sold_price,
                        comps_count=result.comps_count)
        return result.median_sold_price
    except Exception as exc:
        logger.warning("redfin_zip_median_error", zip_code=zip_code, error=str(exc))
        return None


# ── Neighborhood enrichment ────────────────────────────────────────────────────

async def _enrich_neighborhood_async(
    record: PropertyRecord,
    walkscore_key: str = "",
    maps_key: str = "",
) -> dict:
    """
    Async neighborhood enrichment: crime grade uses DB-backed cache when available.
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
        flood = FloodZoneResult(flood_zone="UNKNOWN", is_high_risk=False,
                                sfha=False, panel_number="")

    # Crime grade — uses DB-backed async cache
    try:
        crime = await get_cached_crime_grade(record.zip_code)
    except Exception as e:
        logger.warning("crime_grade_error", zip_code=record.zip_code, error=str(e))
        from neighborhood.crime_grade import CrimeGradeResult
        crime = CrimeGradeResult(grade="UNKNOWN", passes_gate=False, low_confidence=True)

    # Hospital proximity
    hospital = get_hospital_proximity(lat, lon)

    # Liveability (visual_score filled in later if deal_score >= threshold)
    liveability = compute_liveability(walk_score, hospital, crime["grade"], flood)

    # Street View URLs
    sv_urls = get_street_view_urls(lat, lon, addr, api_key=maps_key)

    # Gate evaluation
    gate_result = evaluate_gates(record, crime, flood)

    return {
        "walk_score":           walk_score,
        "flood_zone":           flood["flood_zone"],
        "flood_high_risk":      flood["is_high_risk"],
        "crime_grade":          crime["grade"],
        "crime_low_confidence": crime["low_confidence"],
        "hospital_dist_miles":  hospital["distance_miles"],
        "closest_hospital":     hospital["closest_hospital"],
        "proximity_score":      hospital["proximity_score"],
        "liveability_score":    liveability["total"],
        "street_view_url":      sv_urls["street_view_image_url"],
        "satellite_url":        sv_urls["satellite_view_url"],
        "maps_link":            sv_urls["google_maps_link"],
        "passed_gates":         gate_result.passed,
        "failed_gates":         gate_result.failed_gates,
        "liveability":          liveability,
    }


def _enrich_neighborhood(
    record: PropertyRecord,
    walkscore_key: str = "",
    maps_key: str = "",
) -> dict:
    """Synchronous wrapper for backward compatibility (used in existing pipeline)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context (e.g., APScheduler async job) — create task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    _enrich_neighborhood_async(record, walkscore_key, maps_key)
                )
                return future.result(timeout=60)
        else:
            return loop.run_until_complete(
                _enrich_neighborhood_async(record, walkscore_key, maps_key)
            )
    except Exception:
        # Fallback to synchronous crime grade if async fails
        return _enrich_neighborhood_sync(record, walkscore_key, maps_key)


def _enrich_neighborhood_sync(
    record: PropertyRecord,
    walkscore_key: str = "",
    maps_key: str = "",
    prefetched_crime: "CrimeGradeResult | None" = None,
) -> dict:
    """Pure synchronous neighborhood enrichment (no DB caching for crime grade)."""
    lat  = record.lat  or 30.32
    lon  = record.lon  or -81.66
    addr = record.address

    ws_result = None
    try:
        if walkscore_key:
            ws_result = get_walk_score(addr, lat, lon, api_key=walkscore_key)
    except Exception as e:
        logger.warning("walk_score_error", address=addr, error=str(e))
    walk_score = ws_result["walk_score"] if ws_result else None

    try:
        flood = get_flood_zone(lat, lon)
    except Exception as e:
        logger.warning("flood_zone_error", address=addr, error=str(e))
        from neighborhood.flood_zone import FloodZoneResult
        flood = FloodZoneResult(flood_zone="UNKNOWN", is_high_risk=False,
                                sfha=False, panel_number="")

    if prefetched_crime is not None:
        crime = prefetched_crime
    else:
        try:
            crime = get_crime_grade(record.zip_code)
        except Exception as e:
            logger.warning("crime_grade_error", zip_code=record.zip_code, error=str(e))
            crime = CrimeGradeResult(grade="UNKNOWN", passes_gate=False, low_confidence=True)

    hospital = get_hospital_proximity(lat, lon)
    liveability = compute_liveability(walk_score, hospital, crime["grade"], flood)
    sv_urls = get_street_view_urls(lat, lon, addr, api_key=maps_key)
    gate_result = evaluate_gates(record, crime, flood)

    return {
        "walk_score":           walk_score,
        "flood_zone":           flood["flood_zone"],
        "flood_high_risk":      flood["is_high_risk"],
        "crime_grade":          crime["grade"],
        "crime_low_confidence": crime["low_confidence"],
        "hospital_dist_miles":  hospital["distance_miles"],
        "closest_hospital":     hospital["closest_hospital"],
        "proximity_score":      hospital["proximity_score"],
        "liveability_score":    liveability["total"],
        "street_view_url":      sv_urls["street_view_image_url"],
        "satellite_url":        sv_urls["satellite_view_url"],
        "maps_link":            sv_urls["google_maps_link"],
        "passed_gates":         gate_result.passed,
        "failed_gates":         gate_result.failed_gates,
        "liveability":          liveability,
    }


# ── Underwriting helper ────────────────────────────────────────────────────────

def _build_prop_dict(record: PropertyRecord, assumptions: dict) -> dict[str, Any]:
    num_units = record.num_units or 1
    return {
        "purchase_price":       record.price,
        "num_units":            num_units,
        "zip_code":             record.zip_code,
        "beds":                 record.beds or 0,
        "interest_rate":        assumptions.get("interest_rate", 0.075),
        "insurance_annual":     record.price * assumptions.get("insurance_pct", 0.005),
        "ltr_vacancy_rate":     assumptions.get("ltr_vacancy", 0.08),
        "mtr_vacancy_rate":     assumptions.get("mtr_vacancy", 0.10),
        "str_vacancy_rate":     assumptions.get("str_vacancy", 0.25),
        "ltr_mgmt_rate":        assumptions.get("mgmt_rate", 0.08),
        "ltr_maintenance_rate": assumptions.get("maintenance_rate", 0.01),
        "ltr_capex_rate":       assumptions.get("capex_rate", 0.005),
        "va_funding_fee_pct":   assumptions.get("va_funding_fee_pct", 0.0215),
    }


# ── Deal card builder ──────────────────────────────────────────────────────────

def _build_deal_card(
    record: PropertyRecord,
    neighborhood: dict,
    uw_result,
    deal_score,
    comps_count: int,
    zip_median_sold: float | None = None,
    str_comp: "StrCompResult | None" = None,
) -> dict[str, Any]:
    base_ltr = uw_result.ltr.base
    base_mtr = uw_result.mtr.base
    base_str = uw_result.str_.base

    worst_ltr = uw_result.ltr.worst_case_cash_flow
    worst_mtr = uw_result.mtr.worst_case_cash_flow
    worst_str = uw_result.str_.worst_case_cash_flow

    strategies_raw = [
        {"name": "LTR", "cash_flow": worst_ltr, "dscr": base_ltr.dscr, "coc": base_ltr.cash_on_cash, "noi": base_ltr.noi},
        {"name": "MTR", "cash_flow": worst_mtr, "dscr": base_mtr.dscr, "coc": base_mtr.cash_on_cash, "noi": base_mtr.noi},
        {"name": "STR", "cash_flow": worst_str, "dscr": base_str.dscr, "coc": base_str.cash_on_cash, "noi": base_str.noi},
    ]
    strategies_ranked = sorted(strategies_raw, key=lambda s: s["cash_flow"], reverse=True)

    stress_tests = []
    for sc in list(Scenario):
        if sc.value == "base":
            continue
        res = uw_result.ltr.scenarios.get(sc) or uw_result.mtr.scenarios.get(sc)
        if res:
            stress_tests.append({
                "label":     res.scenario_label,
                "cash_flow": res.monthly_cash_flow,
                "dscr":      res.dscr,
            })

    from underwriting.calculator import _monthly_payment
    refi_pmt = _monthly_payment(uw_result.loan_amount, 0.055, 30)

    # Appraisal gap risk
    appraisal_gap_risk = False
    appraisal_gap_pct  = None
    if zip_median_sold:
        from ingestion.redfin_sold import compute_appraisal_gap
        appraisal_gap_risk, appraisal_gap_pct = compute_appraisal_gap(
            record.price, zip_median_sold
        )

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
        "lat":                record.lat,
        "lon":                record.lon,

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
        # STR comp intelligence
        "str_comp_count":       str_comp.comp_count          if str_comp else 0,
        "str_median_adr":       str_comp.median_adr          if str_comp else None,
        "str_occupancy":        str_comp.estimated_occupancy if str_comp else None,
        "str_gross_monthly":    str_comp.gross_monthly       if str_comp else None,
        "str_net_monthly":      str_comp.net_monthly         if str_comp else None,
        "str_confidence":       str_comp.confidence          if str_comp else "LOW",
        "str_validated":        str_comp.str_validated       if str_comp else False,
        "str_sample_addresses": str_comp.sample_addresses    if str_comp else [],

        # Appraisal gap
        "appraisal_gap_risk": appraisal_gap_risk,
        "appraisal_gap_pct":  appraisal_gap_pct,
        "zip_median_sold":    zip_median_sold,

        # Scoring
        "deal_score":           deal_score.deal_score,
        "return_score":         deal_score.return_score.score,
        "risk_score":           deal_score.risk_score.score,
        "confidence_score_pts": deal_score.confidence_score.score,
        "is_high_priority":     deal_score.is_high_priority,
        "why_scored_high":      deal_score.why_scored_high,
        "risk_flags":           deal_score.risk_score.risk_flags,
        "comps_count":          comps_count,
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

    # ── Live VA rate ───────────────────────────────────────────────────────────
    va_rate_result = fetch_va_rate()
    if "interest_rate" not in assumptions:
        assumptions = {**assumptions, "interest_rate": va_rate_result.rate}
    if va_rate_result.is_stale:
        logger.warning("va_rate_stale", rate_pct=f"{va_rate_result.rate:.3%}",
                       fetched_at=va_rate_result.fetched_at.isoformat())
    logger.info("va_rate_applied", rate_pct=f"{va_rate_result.rate:.3%}",
                source=va_rate_result.source)

    # ── Redfin zip median cache: fetch once per unique zip ─────────────────────
    zip_medians: dict[str, float | None] = {}
    for record in scan_result.listings:
        if record.zip_code not in zip_medians:
            zip_medians[record.zip_code] = _try_redfin_zip_median(record.zip_code)

    inbox_deals:    list[dict] = []
    rejected_props: list[dict] = []
    alert_deals:    list[dict] = []
    scored_pairs:   list       = []
    failed_ids:     set        = set()
    card_cache:     dict       = {}   # canonical_id → completed deal card (Fix B)

    logger.info("pipeline_start", listings=scan_result.total_listings)

    for record in scan_result.listings:
        try:
            # ── Neighborhood ──────────────────────────────────────────────────
            neighborhood = _enrich_neighborhood(record, walkscore_key, maps_key)

            if not neighborhood["passed_gates"]:
                rejected_props.append({
                    "canonical_id":  record.canonical_id,
                    "address":       record.address,
                    "zip_code":      record.zip_code,
                    "price":         record.price,
                    "property_type": record.property_type.value if hasattr(record.property_type, "value") else str(record.property_type),
                    "num_units":     record.num_units,
                    "crime_grade":   neighborhood["crime_grade"],
                    "flood_zone":    neighborhood["flood_zone"],
                    "flood_high_risk": neighborhood["flood_high_risk"],
                    "failed_gates":  neighborhood["failed_gates"],
                })
                failed_ids.add(record.canonical_id)
                continue

            # ── Comps ──────────────────────────────────────────────────────────
            ltr_comps   = _ltr_comps_for(record, scan_result.ltr_comps)
            mtr_comps   = _mtr_comps_for(record, scan_result.mtr_comps)
            apify_count = len(ltr_comps) + len(mtr_comps)

            # Supplement with Rentcast comps when Apify is thin (< 3 total).
            # Cache prevents repeat API calls for the same listing day-over-day.
            rc_comps, rentcast_ltr = _try_rentcast_cached(record, apify_count)
            if rc_comps:
                ltr_comps = _dedupe_comps_by_address(ltr_comps + rc_comps)

            all_comps   = ltr_comps + mtr_comps
            comps_count = len(ltr_comps) + len(mtr_comps)

            # ── Underwriting ───────────────────────────────────────────────────
            prop_dict = _build_prop_dict(record, assumptions)

            # Fall back to Rentcast estimate only when there are truly no comps
            if not all_comps and rentcast_ltr:
                prop_dict["monthly_rent"] = rentcast_ltr
                logger.info(
                    "rentcast_estimate_applied",
                    address=record.address,
                    ltr=rentcast_ltr,
                )

            # Airbnb STR comps
            str_result, str_validated = _try_airbnb_str_comps(record)
            if str_result and str_result.median_adr and str_result.median_adr > 0:
                num_units = record.num_units or 1
                prop_dict["str_adr"]          = str_result.median_adr * num_units
                prop_dict["str_vacancy_rate"] = 1.0 - (str_result.estimated_occupancy or 0.60)
                logger.info(
                    "airbnb_str_applied",
                    address=record.address,
                    adr=prop_dict["str_adr"],
                    occ=str_result.estimated_occupancy,
                )

            uw_result = underwrite(prop_dict, comps=all_comps if all_comps else None)

            # Best conservative CF across strategies
            cf = max(
                uw_result.ltr.worst_case_cash_flow,
                uw_result.mtr.worst_case_cash_flow,
                uw_result.str_.worst_case_cash_flow,
            )
            best_base = uw_result.best_strategy(Scenario.BASE)

            # Appraisal gap from Redfin zip median
            zip_median = zip_medians.get(record.zip_code)

            # ── Scoring ────────────────────────────────────────────────────────
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
                strategy_validated = comps_count >= 3 or str_validated,
                confidence_penalty = uw_result.confidence_penalty,
                zip_median_price   = zip_median,
                property_type      = record.property_type.value if hasattr(record.property_type, "value") else str(record.property_type),
            )

            # ── Visual scoring (only for high-potential deals) ─────────────────
            visual_score_val = 5  # default neutral
            if (deal_score.deal_score >= _VISUAL_SCORE_MIN_DEAL_SCORE
                    and neighborhood.get("street_view_url")):
                try:
                    vs = score_street_view(neighborhood["street_view_url"])
                    if not vs["low_confidence"]:
                        visual_score_val = vs["score"]
                        neighborhood["visual_score"] = vs["score"]
                        neighborhood["visual_breakdown"] = {
                            k: vs[k] for k in
                            ("property_cond", "street_clean", "neighbourhood", "safety", "rentability")
                        }
                except Exception as exc:
                    logger.warning("visual_score_error", address=record.address, error=str(exc))

            # Recompute liveability with real visual score if updated
            if visual_score_val != 5:
                from neighborhood.liveability import compute_liveability
                from neighborhood.flood_zone import FloodZoneResult
                flood_obj = FloodZoneResult(
                    flood_zone=neighborhood["flood_zone"],
                    is_high_risk=neighborhood["flood_high_risk"],
                    sfha=neighborhood["flood_high_risk"],
                    panel_number="",
                )
                hospital_obj = {
                    "distance_miles":   neighborhood["hospital_dist_miles"],
                    "closest_hospital": neighborhood["closest_hospital"],
                    "proximity_score":  neighborhood["proximity_score"],
                }
                lv = compute_liveability(
                    neighborhood.get("walk_score"),
                    hospital_obj,
                    neighborhood["crime_grade"],
                    flood_obj,
                    visual_score=visual_score_val,
                )
                neighborhood["liveability"] = lv
                neighborhood["liveability_score"] = lv["total"]

            # ── Deal card ──────────────────────────────────────────────────────
            card = _build_deal_card(
                record, neighborhood, uw_result, deal_score, comps_count,
                zip_median_sold=zip_median,
                str_comp=str_result,
            )
            card_cache[record.canonical_id] = card
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
                ltr_comps    = _ltr_comps_for(rec, scan_result.ltr_comps)
                mtr_comps    = _mtr_comps_for(rec, scan_result.mtr_comps)
                apify_count  = len(ltr_comps) + len(mtr_comps)
                # Cache hit expected here — all properties were processed in first pass.
                rc_comps, rentcast_ltr = _try_rentcast_cached(rec, apify_count)
                if rc_comps:
                    ltr_comps = _dedupe_comps_by_address(ltr_comps + rc_comps)
                all_comps    = ltr_comps + mtr_comps
                prop_dict    = _build_prop_dict(rec, assumptions)

                if not all_comps and rentcast_ltr:
                    prop_dict["monthly_rent"] = rentcast_ltr

                str_result_c, _ = _try_airbnb_str_comps(rec)
                if str_result_c and str_result_c.median_adr and str_result_c.median_adr > 0:
                    num_units_c = rec.num_units or 1
                    prop_dict["str_adr"]          = str_result_c.median_adr * num_units_c
                    prop_dict["str_vacancy_rate"] = 1.0 - (str_result_c.estimated_occupancy or 0.60)
                uw_result   = underwrite(prop_dict, comps=all_comps if all_comps else None)
                neighborhood = _enrich_neighborhood(rec, walkscore_key, maps_key)
                zip_median  = zip_medians.get(rec.zip_code)
                card = _build_deal_card(
                    rec, neighborhood, uw_result, ds,
                    len(ltr_comps) + len(mtr_comps),
                    zip_median_sold=zip_median,
                    str_comp=str_result_c,
                )
                cards.append(card)
            except Exception:
                pass
        return cards

    # Cards were built and cached during the first pass — no need to recompute.
    alert_cards = [card_cache[rec.canonical_id] for rec, _ in ranked.alerts
                   if rec.canonical_id in card_cache]
    inbox_cards = [card_cache[rec.canonical_id] for rec, _ in ranked.inbox
                   if rec.canonical_id in card_cache]

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
