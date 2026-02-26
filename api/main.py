"""
api/main.py

FastAPI application entry point.

Lifespan:
  - Starts APScheduler daily scan at 6:00 AM ET
  - Starts APScheduler daily email digest at 7:00 AM ET
"""

from __future__ import annotations

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
    """Async wrapper for the daily scan job."""
    import asyncio
    from ingestion.run_scan import run_scan
    from api.state import get_store

    logger.info("scheduled_scan_start")
    try:
        result = await asyncio.to_thread(run_scan)
        store  = get_store()
        store.setdefault("scan_logs", []).append({
            "listings":  result.total_listings,
            "ltr_comps": len(result.ltr_comps),
            "mtr_comps": len(result.mtr_comps),
            "errors":    result.errors,
            "scanned_at": result.scanned_at.isoformat(),
        })
        logger.info("scheduled_scan_done", listings=result.total_listings)
    except Exception as exc:
        logger.error("scheduled_scan_failed", error=str(exc))


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
