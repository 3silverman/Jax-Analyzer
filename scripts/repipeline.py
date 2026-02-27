"""
scripts/repipeline.py

Re-run the pipeline + persist against listings already stored in Supabase,
without triggering a new Apify scrape.  Useful for testing scoring / persist
fixes after a real scan has already populated the properties table.

Usage:
    python -m scripts.repipeline

The script:
  1. Loads all active PropertyRecords from Supabase.
  2. Builds a ScanResult with empty comps (static rent table fills them in).
  3. Calls run_pipeline() — full neighborhood, underwriting, scoring pass.
  4. Persists all results (deal_scores, scored_low, failed-gate rejected) to DB.

Environment:
  DATABASE_URL  — required (same as the main app)
  All other env vars (WALKSCORE_API_KEY, etc.) are used if set; otherwise
  their respective modules fall back gracefully.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger(__name__)


# ── Step 1: load PropertyRecords from the DB ──────────────────────────────────

async def _load_listings() -> list:
    """Return all active properties as PropertyRecord objects."""
    from db.connection import get_session
    from db.repositories.property_repo import get_active_properties
    from normalization.schema import DataSource, PropertyRecord, PropertyType

    async with get_session() as session:
        rows = await get_active_properties(session, limit=2000)

    logger.info("repipeline_listings_loaded", count=len(rows))

    records = []
    for row in rows:
        # Coerce source enum — DB stores a plain string
        try:
            source = DataSource(row.get("source", "zillow_sale"))
        except ValueError:
            source = DataSource.ZILLOW_SALE

        # Coerce property_type enum
        try:
            property_type = PropertyType(row.get("property_type", "unknown"))
        except ValueError:
            property_type = PropertyType.UNKNOWN

        scraped_at = row.get("scraped_at") or datetime.now(tz=timezone.utc)
        if isinstance(scraped_at, str):
            scraped_at = datetime.fromisoformat(scraped_at)
        if scraped_at.tzinfo is None:
            scraped_at = scraped_at.replace(tzinfo=timezone.utc)

        price = row.get("price") or 0.0
        if not price or price <= 0:
            logger.debug("repipeline_skip_no_price", address=row.get("address"))
            continue

        try:
            record = PropertyRecord(
                canonical_id   = row["canonical_id"],
                source         = source,
                source_id      = row.get("source_id") or "",
                scraped_at     = scraped_at,
                address        = row.get("address", ""),
                city           = row.get("city", "Jacksonville"),
                state          = row.get("state", "FL"),
                zip_code       = row.get("zip_code", ""),
                lat            = row.get("lat"),
                lon            = row.get("lon"),
                price          = float(price),
                beds           = row.get("beds"),
                baths          = row.get("baths"),
                sqft           = row.get("sqft"),
                year_built     = row.get("year_built"),
                property_type  = property_type,
                num_units      = row.get("num_units") or 1,
                days_on_market = row.get("days_on_market"),
                confidence_score = float(row.get("confidence_score") or 0.5),
                missing_fields = list(row.get("missing_fields") or []),
            )
            records.append(record)
        except Exception as exc:
            logger.warning("repipeline_record_build_error",
                           address=row.get("address"), error=str(exc))
            continue

    logger.info("repipeline_records_built", count=len(records))
    return records


# ── Step 2: run pipeline + persist ───────────────────────────────────────────

async def _persist(pipeline_out: dict, scan_result) -> None:
    """Inline persist — same logic as api/main._persist_pipeline_results."""
    from db.connection import get_session
    from db.repositories import (
        property_repo,
        neighborhood_repo,
        score_repo,
        scan_log_repo,
    )

    all_deals = (
        pipeline_out.get("inbox", []) +
        pipeline_out.get("alerts", []) +
        pipeline_out.get("scored_low", [])
    )
    rejected = pipeline_out.get("rejected", [])
    summary  = pipeline_out.get("scan_summary", {})

    async with get_session() as session:
        scan_id = await scan_log_repo.start_scan(session)

    async with get_session() as session:
        for card in all_deals:
            cid = card.get("canonical_id")
            if not cid:
                continue

            await property_repo.upsert_property(session, {
                "canonical_id":   cid,
                "source":         card.get("source", "zillow_sale"),
                "source_id":      card.get("source_id", ""),
                "scraped_at":     card.get("scraped_at"),
                "address":        card.get("address", ""),
                "city":           card.get("city", "Jacksonville"),
                "state":          "FL",
                "zip_code":       card.get("zip_code", ""),
                "lat":            card.get("lat"),
                "lon":            card.get("lon"),
                "price":          card.get("price", 0.0),
                "beds":           card.get("beds"),
                "baths":          card.get("baths"),
                "sqft":           card.get("sqft"),
                "year_built":     card.get("year_built"),
                "property_type":  card.get("property_type", "unknown"),
                "num_units":      card.get("num_units", 1),
                "days_on_market": card.get("days_on_market"),
                "confidence_score": 0.0,
                "missing_fields": [],
                "status":         "active",
                "raw":            {},
            })

            await neighborhood_repo.upsert_neighborhood(session, {
                "property_id":             cid,
                "liveability_total":       (card.get("liveability") or {}).get("total", 0),
                "walk_pts":                (card.get("liveability") or {}).get("walk_pts", 0),
                "hospital_pts":            (card.get("liveability") or {}).get("hospital_pts", 0),
                "crime_pts":               (card.get("liveability") or {}).get("crime_pts", 0),
                "flood_pts":               (card.get("liveability") or {}).get("flood_pts", 0),
                "visual_pts":              (card.get("liveability") or {}).get("visual_pts", 5),
                "walk_score":              card.get("walk_score"),
                "crime_grade":             card.get("crime_grade", "UNKNOWN"),
                "crime_low_confidence":    card.get("crime_low_confidence", False),
                "flood_zone":              card.get("flood_zone", "UNKNOWN"),
                "flood_high_risk":         card.get("flood_high_risk", False),
                "hospital_distance_miles": card.get("hospital_dist_miles"),
                "closest_hospital":        card.get("closest_hospital", ""),
                "proximity_score":         card.get("proximity_score", 0),
                "passed_gates":            card.get("passed_gates", True),
                "failed_gate_reasons":     card.get("failed_gates", []),
                "street_view_url":         card.get("street_view_url"),
                "satellite_url":           card.get("satellite_url"),
                "maps_link":               card.get("maps_link"),
            })

            deal_card_json = {k: v for k, v in card.items() if k != "liveability"}

            await score_repo.insert_deal_score(session, {
                "property_id":            cid,
                "return_score":           card.get("return_score", 0),
                "risk_score":             card.get("risk_score", 0),
                "confidence_score":       card.get("confidence_score_pts", 0),
                "deal_score":             card.get("deal_score", 0),
                "is_high_priority":       card.get("is_high_priority", False),
                "alert_sent":             False,
                "alert_reasons":          card.get("alert_reasons", []),
                "conservative_cash_flow": card.get("top_cash_flow"),
                "best_strategy":          card.get("top_strategy"),
                "dscr":                   (card.get("strategies") or [{}])[0].get("dscr"),
                "cash_on_cash":           (card.get("strategies") or [{}])[0].get("coc"),
                "why_scored_high":        card.get("why_scored_high"),
                "assumptions_snapshot":   {},
                "deal_card_json":         deal_card_json,
            })

        for rej in rejected:
            cid = rej.get("canonical_id")
            if not cid:
                continue
            await property_repo.upsert_property(session, {
                "canonical_id":   cid,
                "source":         "zillow_sale",
                "source_id":      "",
                "scraped_at":     None,
                "address":        rej.get("address", ""),
                "city":           "Jacksonville",
                "state":          "FL",
                "zip_code":       rej.get("zip_code", ""),
                "price":          rej.get("price", 0.0),
                "property_type":  rej.get("property_type", "unknown"),
                "num_units":      rej.get("num_units", 1),
                "confidence_score": 0.0,
                "missing_fields": [],
                "status":         "failed_gates",
                "raw":            {},
            })
            await neighborhood_repo.upsert_neighborhood(session, {
                "property_id":         cid,
                "crime_grade":         rej.get("crime_grade", "UNKNOWN"),
                "flood_zone":          rej.get("flood_zone", "UNKNOWN"),
                "flood_high_risk":     rej.get("flood_high_risk", False),
                "passed_gates":        False,
                "failed_gate_reasons": rej.get("failed_gates", []),
            })

    async with get_session() as session:
        await scan_log_repo.complete_scan(
            session,
            scan_id         = scan_id,
            properties_scanned = summary.get("total_scanned", 0),
            passed_gates    = summary.get("passed_gates", 0),
            alerts_triggered = summary.get("alerts", 0),
            errors          = summary.get("errors", []),
        )

    logger.info(
        "repipeline_persist_done",
        deals    = len(all_deals),
        rejected = len(rejected),
        inbox    = len(pipeline_out.get("inbox", [])),
        scored_low = len(pipeline_out.get("scored_low", [])),
        alerts   = len(pipeline_out.get("alerts", [])),
    )


async def main() -> None:
    from ingestion.run_scan import ScanResult
    from ingestion.pipeline import run_pipeline
    from db.connection import reset_engine

    records = await _load_listings()
    if not records:
        logger.error("repipeline_no_listings")
        sys.exit(1)

    # Build a ScanResult from cached listings — no Apify calls.
    # Comps are empty; static rent table (USE_STATIC_RENT_TABLE=true) fills them in.
    scan_result = ScanResult(
        listings   = records,
        ltr_comps  = [],
        mtr_comps  = [],
        scanned_at = datetime.now(tz=timezone.utc),
        errors     = [],
    )

    logger.info("repipeline_pipeline_start", listings=len(records))
    pipeline_out = await asyncio.to_thread(run_pipeline, scan_result)

    summary = pipeline_out.get("scan_summary", {})
    logger.info(
        "repipeline_pipeline_done",
        inbox      = len(pipeline_out.get("inbox", [])),
        scored_low = len(pipeline_out.get("scored_low", [])),
        alerts     = len(pipeline_out.get("alerts", [])),
        rejected   = len(pipeline_out.get("rejected", [])),
        passed_gates = summary.get("passed_gates", 0),
    )

    # The pipeline uses a background event loop for DB cache ops.
    # Reset so _persist runs on the current (main) asyncio loop.
    reset_engine()

    await _persist(pipeline_out, scan_result)


if __name__ == "__main__":
    asyncio.run(main())
