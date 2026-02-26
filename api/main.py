"""
api/main.py

FastAPI application entry point.

Lifespan:
  - Starts APScheduler daily scan at 6:00 AM ET
  - Starts APScheduler daily email digest at 7:00 AM ET
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes.web import router as web_router

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background scheduler on app startup."""
    scheduler = _build_scheduler()
    scheduler.start()
    logger.info("scheduler_started")
    yield
    scheduler.shutdown(wait=False)
    logger.info("scheduler_stopped")


def _build_scheduler():
    """Configure APScheduler with daily scan + digest jobs."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = AsyncIOScheduler(timezone="America/New_York")

    # Daily scan at 6:00 AM ET
    scheduler.add_job(
        _run_daily_scan,
        trigger=CronTrigger(hour=6, minute=0),
        id="daily_scan",
        name="Daily property scan",
        replace_existing=True,
    )

    # Daily email digest at 7:00 AM ET (after scan completes)
    scheduler.add_job(
        _send_daily_digest,
        trigger=CronTrigger(hour=7, minute=0),
        id="daily_digest",
        name="Daily email digest",
        replace_existing=True,
    )

    return scheduler


async def _run_daily_scan() -> None:
    """Async wrapper for the daily scan job. Writes results to DB and in-memory store."""
    import asyncio
    from ingestion.run_scan import run_scan
    from ingestion.pipeline import run_pipeline
    from api.state import get_store

    logger.info("scheduled_scan_start")
    try:
        scan_result = await asyncio.to_thread(run_scan)
        pipeline_out = await asyncio.to_thread(run_pipeline, scan_result)

        # Update in-memory store (always)
        store = get_store()
        store["inbox"]   = pipeline_out.get("inbox", [])
        store["alerts"]  = pipeline_out.get("alerts", [])
        store["rejected"] = pipeline_out.get("rejected", [])
        store.setdefault("scan_logs", []).append({
            **pipeline_out.get("scan_summary", {}),
            "started_at": scan_result.scanned_at.isoformat(),
        })

        # Persist to DB (best-effort; errors are logged not raised)
        await _persist_pipeline_results(pipeline_out, scan_result)

        logger.info(
            "scheduled_scan_done",
            listings=scan_result.total_listings,
            alerts=len(pipeline_out.get("alerts", [])),
        )
    except Exception as exc:
        logger.error("scheduled_scan_failed", error=str(exc))


async def _persist_pipeline_results(pipeline_out: dict, scan_result) -> None:
    """Write pipeline output to Supabase. Called from the daily scan job."""
    try:
        from db.connection import get_session
        from db.repositories import (
            property_repo,
            neighborhood_repo,
            score_repo,
            scan_log_repo,
        )

        all_deals = pipeline_out.get("inbox", []) + pipeline_out.get("alerts", [])
        rejected  = pipeline_out.get("rejected", [])
        summary   = pipeline_out.get("scan_summary", {})

        async with get_session() as session:
            scan_id = await scan_log_repo.start_scan(session)

        async with get_session() as session:
            # Persist each scored deal
            for card in all_deals:
                cid = card.get("canonical_id")
                if not cid:
                    continue

                # Upsert property
                await property_repo.upsert_property(session, {
                    "canonical_id":    cid,
                    "source":          card.get("source", "zillow_sale"),
                    "source_id":       card.get("source_id", ""),
                    "scraped_at":      card.get("scraped_at"),
                    "address":         card.get("address", ""),
                    "city":            card.get("city", "Jacksonville"),
                    "state":           "FL",
                    "zip_code":        card.get("zip_code", ""),
                    "lat":             card.get("lat"),
                    "lon":             card.get("lon"),
                    "price":           card.get("price", 0.0),
                    "beds":            card.get("beds"),
                    "baths":           card.get("baths"),
                    "sqft":            card.get("sqft"),
                    "year_built":      card.get("year_built"),
                    "property_type":   card.get("property_type", "unknown"),
                    "num_units":       card.get("num_units", 1),
                    "days_on_market":  card.get("days_on_market"),
                    "confidence_score": 0.0,
                    "missing_fields":  [],
                    "status":          "active",
                    "raw":             {},
                })

                # Upsert neighborhood
                await neighborhood_repo.upsert_neighborhood(session, {
                    "property_id":              cid,
                    "liveability_total":        (card.get("liveability") or {}).get("total", 0),
                    "walk_pts":                 (card.get("liveability") or {}).get("walk_pts", 0),
                    "hospital_pts":             (card.get("liveability") or {}).get("hospital_pts", 0),
                    "crime_pts":                (card.get("liveability") or {}).get("crime_pts", 0),
                    "flood_pts":                (card.get("liveability") or {}).get("flood_pts", 0),
                    "visual_pts":               (card.get("liveability") or {}).get("visual_pts", 5),
                    "walk_score":               card.get("walk_score"),
                    "crime_grade":              card.get("crime_grade", "UNKNOWN"),
                    "crime_low_confidence":     card.get("crime_low_confidence", False),
                    "flood_zone":               card.get("flood_zone", "UNKNOWN"),
                    "flood_high_risk":          card.get("flood_high_risk", False),
                    "hospital_distance_miles":  card.get("hospital_dist_miles"),
                    "closest_hospital":         card.get("closest_hospital", ""),
                    "proximity_score":          card.get("proximity_score", 0),
                    "passed_gates":             card.get("passed_gates", True),
                    "failed_gate_reasons":      card.get("failed_gates", []),
                    "street_view_url":          card.get("street_view_url"),
                    "satellite_url":            card.get("satellite_url"),
                    "maps_link":                card.get("maps_link"),
                })

                # Full deal card JSON (strategies, stress tests, VA loan)
                deal_card_json = {k: v for k, v in card.items()
                                  if k not in ("liveability",)}

                # Insert deal score
                await score_repo.insert_deal_score(session, {
                    "property_id":           cid,
                    "return_score":          card.get("return_score", 0),
                    "risk_score":            card.get("risk_score", 0),
                    "confidence_score":      card.get("confidence_score_pts", 0),
                    "deal_score":            card.get("deal_score", 0),
                    "is_high_priority":      card.get("is_high_priority", False),
                    "alert_sent":            card.get("is_high_priority", False),
                    "alert_reasons":         card.get("alert_reasons", []),
                    "conservative_cash_flow": card.get("top_cash_flow"),
                    "best_strategy":         card.get("top_strategy"),
                    "dscr":                  (card.get("strategies") or [{}])[0].get("dscr"),
                    "cash_on_cash":          (card.get("strategies") or [{}])[0].get("coc"),
                    "why_scored_high":       card.get("why_scored_high"),
                    "assumptions_snapshot":  {},
                    "deal_card_json":        deal_card_json,
                })

            # Persist failed-gate properties
            for rej in rejected:
                cid = rej.get("canonical_id")
                if not cid:
                    continue
                await property_repo.upsert_property(session, {
                    "canonical_id":  cid,
                    "source":        "zillow_sale",
                    "source_id":     "",
                    "scraped_at":    None,
                    "address":       rej.get("address", ""),
                    "city":          "Jacksonville",
                    "state":         "FL",
                    "zip_code":      rej.get("zip_code", ""),
                    "price":         rej.get("price", 0.0),
                    "property_type": rej.get("property_type", "unknown"),
                    "num_units":     rej.get("num_units", 1),
                    "confidence_score": 0.0,
                    "missing_fields": [],
                    "status":        "failed_gates",
                    "raw":           {},
                })
                await neighborhood_repo.upsert_neighborhood(session, {
                    "property_id":       cid,
                    "crime_grade":       rej.get("crime_grade", "UNKNOWN"),
                    "flood_zone":        rej.get("flood_zone", "UNKNOWN"),
                    "flood_high_risk":   rej.get("flood_high_risk", False),
                    "passed_gates":      False,
                    "failed_gate_reasons": rej.get("failed_gates", []),
                })

        # Complete the scan log
        async with get_session() as session:
            await scan_log_repo.complete_scan(
                session,
                scan_id=scan_id,
                properties_scanned=summary.get("total_scanned", 0),
                passed_gates=summary.get("passed_gates", 0),
                alerts_triggered=summary.get("alerts", 0),
                errors=summary.get("errors", []),
            )

        logger.info("db_persist_complete", deals=len(all_deals), rejected=len(rejected))

    except Exception as exc:
        logger.error("db_persist_failed", error=str(exc))


async def _send_daily_digest() -> None:
    """Async wrapper for the daily email digest job."""
    import asyncio
    from api.state import get_store
    from delivery.email_digest import send_digest
    from api.dependencies import get_settings

    store    = get_store()
    settings = get_settings()
    deals    = store.get("inbox", []) + store.get("alerts", [])

    if not settings.digest_email:
        logger.info("digest_skipped_no_email")
        return

    try:
        await asyncio.to_thread(send_digest, deals, settings.digest_email)
        logger.info("digest_sent", recipient=settings.digest_email)
    except Exception as exc:
        logger.error("digest_failed", error=str(exc))


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title       = "Jacksonville Investment Property Analyzer",
        description = "Automated VA loan multifamily deal finder for Jacksonville, FL",
        version     = "1.0.0",
        lifespan    = lifespan,
    )
    app.include_router(web_router)
    return app


app = create_app()
