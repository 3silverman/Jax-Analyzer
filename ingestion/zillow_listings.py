"""
ingestion/zillow_listings.py

Fetches Jacksonville for-sale multifamily listings via the Apify Zillow actor,
then normalizes them into canonical PropertyRecord objects.

Actor: "lukaskrivka/zillow-scraper"
"""

from __future__ import annotations

from typing import Any

import structlog

from ingestion.apify_client import ApifyClient
from normalization.deduplication import deduplicate_property_records
from normalization.normalizer import normalize_zillow_listing
from normalization.schema import PropertyRecord, ZIP_WHITELIST

logger = structlog.get_logger(__name__)

# ── Actor configuration ────────────────────────────────────────────────────────

# Zillow search URLs targeting JAX multifamily in the whitelisted zips
_SEARCH_URLS: list[str] = [
    # Multifamily for-sale in each target zip
    "https://www.zillow.com/jacksonville-fl-32204/?searchQueryState=%7B%22mapBounds%22%3A%7B%7D%2C%22filterState%22%3A%7B%22con%22%3A%7B%22value%22%3Afalse%7D%2C%22gar%22%3A%7B%22value%22%3Afalse%7D%2C%22auc%22%3A%7B%22value%22%3Afalse%7D%2C%22fore%22%3A%7B%22value%22%3Afalse%7D%2C%22sf%22%3A%7B%22value%22%3Afalse%7D%2C%22mf%22%3A%7B%22value%22%3Atrue%7D%2C%22tow%22%3A%7B%22value%22%3Afalse%7D%7D%7D",
    "https://www.zillow.com/jacksonville-fl-32205/?searchQueryState=%7B%22filterState%22%3A%7B%22mf%22%3A%7B%22value%22%3Atrue%7D%2C%22sf%22%3A%7B%22value%22%3Afalse%7D%7D%7D",
    "https://www.zillow.com/jacksonville-fl-32206/?searchQueryState=%7B%22filterState%22%3A%7B%22mf%22%3A%7B%22value%22%3Atrue%7D%2C%22sf%22%3A%7B%22value%22%3Afalse%7D%7D%7D",
    "https://www.zillow.com/jacksonville-fl-32207/?searchQueryState=%7B%22filterState%22%3A%7B%22mf%22%3A%7B%22value%22%3Atrue%7D%2C%22sf%22%3A%7B%22value%22%3Afalse%7D%7D%7D",
    "https://www.zillow.com/jacksonville-fl-32210/?searchQueryState=%7B%22filterState%22%3A%7B%22mf%22%3A%7B%22value%22%3Atrue%7D%2C%22sf%22%3A%7B%22value%22%3Afalse%7D%7D%7D",
    "https://www.zillow.com/jacksonville-fl-32211/?searchQueryState=%7B%22filterState%22%3A%7B%22mf%22%3A%7B%22value%22%3Atrue%7D%2C%22sf%22%3A%7B%22value%22%3Afalse%7D%7D%7D",
    "https://www.zillow.com/jacksonville-fl-32217/?searchQueryState=%7B%22filterState%22%3A%7B%22mf%22%3A%7B%22value%22%3Atrue%7D%2C%22sf%22%3A%7B%22value%22%3Afalse%7D%7D%7D",
]

_ACTOR_INPUT: dict[str, Any] = {
    "startUrls": [{"url": url} for url in _SEARCH_URLS],
    "maxItems":  500,
    "proxy": {"useApifyProxy": True},
}


# ── Public function ────────────────────────────────────────────────────────────

def fetch_listings(client: ApifyClient) -> list[PropertyRecord]:
    """
    Run the Zillow for-sale actor and return deduplicated PropertyRecord objects
    filtered to the JAX zip whitelist.

    Args:
        client: Authenticated ApifyClient instance.

    Returns:
        Deduplicated list of PropertyRecord objects for whitelisted zips.
    """
    logger.info("zillow_listings_fetch_start")
    raw_items = client.run_actor(
        "lukaskrivka/zillow-scraper",
        input_payload=_ACTOR_INPUT,
        memory_mbytes=1024,
    )
    logger.info("zillow_listings_raw_count", count=len(raw_items))

    records: list[PropertyRecord] = []
    for item in raw_items:
        try:
            rec = normalize_zillow_listing(item)
            if rec.zip_code in ZIP_WHITELIST:
                records.append(rec)
        except Exception:
            logger.warning("zillow_listing_normalize_error", raw=item)

    deduped = deduplicate_property_records(records)
    logger.info("zillow_listings_after_dedup", count=len(deduped))
    return deduped
