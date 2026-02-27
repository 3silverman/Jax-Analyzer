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

async def main() -> None:
    from ingestion.run_scan import ScanResult
    from ingestion.pipeline import run_pipeline
    from api.main import _persist_pipeline_results
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
        inbox        = len(pipeline_out.get("inbox", [])),
        scored_low   = len(pipeline_out.get("scored_low", [])),
        alerts       = len(pipeline_out.get("alerts", [])),
        rejected     = len(pipeline_out.get("rejected", [])),
        passed_gates = summary.get("passed_gates", 0),
    )

    # The pipeline uses a background event loop for DB cache ops.
    # Reset so _persist_pipeline_results runs on the current (main) asyncio loop.
    reset_engine()

    await _persist_pipeline_results(pipeline_out, scan_result)


if __name__ == "__main__":
    asyncio.run(main())
