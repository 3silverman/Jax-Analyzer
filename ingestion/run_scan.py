"""
ingestion/run_scan.py

Daily scan orchestrator.

Runs all ingestion actors in sequence, collects PropertyRecords + RentalComps,
and returns a ScanResult for downstream processing (neighborhood filtering,
underwriting, scoring).

Called by APScheduler and can also be invoked directly:
    python -m ingestion.run_scan
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from ingestion.apify_client import ApifyClient, ApifyError
from ingestion.furnished_finder import fetch_mtr_comps
from ingestion.zillow_listings import fetch_listings
from ingestion.zillow_rentals import fetch_rental_comps
from normalization.schema import PropertyRecord, RentalComp

logger = structlog.get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Result container
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    """
    Output of a single daily scan run.

    Attributes:
        listings:    For-sale PropertyRecords from Zillow.
        ltr_comps:   Long-term rental RentalComps from Zillow Rentals.
        mtr_comps:   Medium-term rental RentalComps from Furnished Finder.
        scanned_at:  UTC timestamp when the scan was initiated.
        errors:      Non-fatal errors encountered during the scan.
    """
    listings:   list[PropertyRecord] = field(default_factory=list)
    ltr_comps:  list[RentalComp]     = field(default_factory=list)
    mtr_comps:  list[RentalComp]     = field(default_factory=list)
    scanned_at: datetime             = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    errors:     list[str]            = field(default_factory=list)

    @property
    def total_listings(self) -> int:
        return len(self.listings)

    @property
    def total_comps(self) -> int:
        return len(self.ltr_comps) + len(self.mtr_comps)


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────

def run_scan() -> ScanResult:
    """
    Execute all ingestion actors and return a ScanResult.

    Each actor failure is caught and logged; remaining actors still run.
    A scan with zero listings is logged as a warning but is not fatal.

    Returns:
        ScanResult with all collected data and any error messages.
    """
    result = ScanResult()
    logger.info("scan_start", scanned_at=result.scanned_at.isoformat())

    try:
        apify = ApifyClient()
    except ApifyError as exc:
        msg = f"Cannot initialise Apify client: {exc}"
        logger.error("scan_apify_init_failed", error=msg)
        result.errors.append(msg)
        return result

    with apify:
        # ── Zillow for-sale listings ──────────────────────────────────────────
        try:
            result.listings = fetch_listings(apify)
            logger.info("scan_listings_fetched", count=result.total_listings)
        except Exception as exc:
            msg = f"zillow_listings failed: {exc}"
            logger.error("scan_actor_error", actor="zillow_listings", error=msg)
            result.errors.append(msg)

        # ── Zillow rental comps (LTR) ─────────────────────────────────────────
        try:
            result.ltr_comps = fetch_rental_comps(apify)
            logger.info("scan_ltr_comps_fetched", count=len(result.ltr_comps))
        except Exception as exc:
            msg = f"zillow_rentals failed: {exc}"
            logger.error("scan_actor_error", actor="zillow_rentals", error=msg)
            result.errors.append(msg)

        # ── Furnished Finder MTR comps ────────────────────────────────────────
        try:
            result.mtr_comps = fetch_mtr_comps(apify)
            logger.info("scan_mtr_comps_fetched", count=len(result.mtr_comps))
        except Exception as exc:
            msg = f"furnished_finder failed: {exc}"
            logger.error("scan_actor_error", actor="furnished_finder", error=msg)
            result.errors.append(msg)

    if result.total_listings == 0:
        logger.warning("scan_no_listings_found")

    logger.info(
        "scan_complete",
        listings=result.total_listings,
        ltr_comps=len(result.ltr_comps),
        mtr_comps=len(result.mtr_comps),
        errors=len(result.errors),
    )
    return result


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import structlog

    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ]
    )

    scan = run_scan()
    summary = {
        "scanned_at":   scan.scanned_at.isoformat(),
        "listings":     scan.total_listings,
        "ltr_comps":    len(scan.ltr_comps),
        "mtr_comps":    len(scan.mtr_comps),
        "errors":       scan.errors,
    }
    print(json.dumps(summary, indent=2))
