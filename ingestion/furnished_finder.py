"""
ingestion/furnished_finder.py

Fetches Jacksonville MTR (medium-term rental) comps from Furnished Finder
via an Apify actor, then normalizes them into canonical RentalComp objects.

These comps validate MTR rent assumptions in the underwriting module.
Actor: "misceres/furnished-finder-scraper" (community actor)
"""

from __future__ import annotations

from typing import Any

import structlog

from ingestion.apify_client import ApifyClient
from normalization.deduplication import deduplicate_rental_comps
from normalization.normalizer import normalize_furnished_finder
from normalization.schema import RentalComp, ZIP_WHITELIST

logger = structlog.get_logger(__name__)

# ── Actor configuration ────────────────────────────────────────────────────────

# Furnished Finder search — Jacksonville, FL; target hospital corridors for MTR
_FF_SEARCH_URLS: list[str] = [
    # Riverside / Avondale / Springfield — closest to St. Vincent's + UF Health Shands
    "https://www.furnishedfinder.com/housing/Jacksonville/FL",
]

_ACTOR_INPUT: dict[str, Any] = {
    "startUrls": [{"url": url} for url in _FF_SEARCH_URLS],
    "maxItems":  300,
    "proxy": {"useApifyProxy": True},
}


# ── Public function ────────────────────────────────────────────────────────────

def fetch_mtr_comps(client: ApifyClient) -> list[RentalComp]:
    """
    Run the Furnished Finder actor and return deduplicated MTR RentalComp objects.

    Only retains comps in the JAX zip whitelist with a non-zero monthly_rate.

    Args:
        client: Authenticated ApifyClient instance.

    Returns:
        Deduplicated list of RentalComp (MTR) objects.
    """
    logger.info("furnished_finder_fetch_start")
    raw_items = client.run_actor(
        "misceres/furnished-finder-scraper",
        input_payload=_ACTOR_INPUT,
        memory_mbytes=512,
    )
    logger.info("furnished_finder_raw_count", count=len(raw_items))

    comps: list[RentalComp] = []
    for item in raw_items:
        try:
            comp = normalize_furnished_finder(item)
            if comp.zip_code in ZIP_WHITELIST and comp.monthly_rate and comp.monthly_rate > 0:
                comps.append(comp)
        except Exception:
            logger.warning("furnished_finder_normalize_error", raw=item)

    deduped = deduplicate_rental_comps(comps)
    logger.info("furnished_finder_after_dedup", count=len(deduped))
    return deduped
