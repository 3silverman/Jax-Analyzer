"""
ingestion/zillow_rentals.py

Fetches Jacksonville rental listings via the Apify Zillow rental actor,
then normalizes them into canonical RentalComp (LTR) objects.

"""

from __future__ import annotations

from typing import Any

import structlog

from ingestion.actor_ids import ZILLOW_SCRAPER
from ingestion.apify_client import ApifyClient
from normalization.deduplication import deduplicate_rental_comps
from normalization.normalizer import normalize_zillow_rental
from normalization.schema import RentalComp, ZIP_WHITELIST

logger = structlog.get_logger(__name__)

# ── Actor configuration ────────────────────────────────────────────────────────

_RENTAL_SEARCH_URLS: list[str] = [
    "https://www.zillow.com/jacksonville-fl-32204/rentals/?searchQueryState=%7B%22filterState%22%3A%7B%22fr%22%3A%7B%22value%22%3Atrue%7D%2C%22fsba%22%3A%7B%22value%22%3Afalse%7D%2C%22nc%22%3A%7B%22value%22%3Afalse%7D%2C%22fsbo%22%3A%7B%22value%22%3Afalse%7D%2C%22cmsn%22%3A%7B%22value%22%3Afalse%7D%2C%22auc%22%3A%7B%22value%22%3Afalse%7D%2C%22fore%22%3A%7B%22value%22%3Afalse%7D%7D%7D",
    "https://www.zillow.com/jacksonville-fl-32205/rentals/",
    "https://www.zillow.com/jacksonville-fl-32206/rentals/",
    "https://www.zillow.com/jacksonville-fl-32207/rentals/",
    "https://www.zillow.com/jacksonville-fl-32210/rentals/",
    "https://www.zillow.com/jacksonville-fl-32211/rentals/",
    "https://www.zillow.com/jacksonville-fl-32217/rentals/",
]

_ACTOR_INPUT: dict[str, Any] = {
    "startUrls": [{"url": url} for url in _RENTAL_SEARCH_URLS],
    "maxItems":  800,
    "proxy": {"useApifyProxy": True},
}


# ── Public function ────────────────────────────────────────────────────────────

def fetch_rental_comps(client: ApifyClient) -> list[RentalComp]:
    """
    Run the Zillow rentals actor and return deduplicated RentalComp (LTR) objects.

    Args:
        client: Authenticated ApifyClient instance.

    Returns:
        Deduplicated list of RentalComp objects for whitelisted JAX zips.
    """
    logger.info("zillow_rentals_fetch_start")
    raw_items = client.run_actor(
        ZILLOW_SCRAPER,
        input_payload=_ACTOR_INPUT,
        memory_mbytes=1024,
    )
    logger.info("zillow_rentals_raw_count", count=len(raw_items))

    comps: list[RentalComp] = []
    for item in raw_items:
        try:
            comp = normalize_zillow_rental(item)
            if comp.zip_code in ZIP_WHITELIST and comp.monthly_rate and comp.monthly_rate > 0:
                comps.append(comp)
        except Exception:
            logger.warning("zillow_rental_normalize_error", raw=item)

    deduped = deduplicate_rental_comps(comps)
    logger.info("zillow_rentals_after_dedup", count=len(deduped))
    return deduped
