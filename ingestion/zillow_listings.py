"""
ingestion/zillow_listings.py

Fetches Jacksonville for-sale multifamily AND SFH/ADU listings via the Apify Zillow actor,
then normalizes them into canonical PropertyRecord objects.

Searches two listing types:
  1. Multifamily (duplex / triplex / quadplex) — primary house-hack targets
  2. Single-family (SFH + SFH with ADU) — included if described with ADU or guest house
"""

from __future__ import annotations

from typing import Any

import structlog

from ingestion.actor_ids import ZILLOW_SCRAPER
from ingestion.apify_client import ApifyClient
from normalization.deduplication import deduplicate_property_records
from normalization.normalizer import normalize_zillow_listing
from normalization.schema import PropertyRecord, PropertyType, ZIP_WHITELIST

logger = structlog.get_logger(__name__)

# ── Actor configuration ────────────────────────────────────────────────────────

_MF_ACTOR_INPUT: dict[str, Any] = {
    "zipCodes":        ["32204", "32205", "32206", "32207", "32210", "32211", "32217"],
    "forSaleByAgent":  True,
    "forSaleByOwner":  True,
    "forRent":         False,
    "sold":            False,
    "priceMin":        100000,
    "priceMax":        600000,
}

# SFH input covers priority 4 zips only to control volume; we only care about ADU properties
_SFH_ACTOR_INPUT: dict[str, Any] = {
    "zipCodes":        ["32204", "32205", "32206", "32207"],
    "forSaleByAgent":  True,
    "forSaleByOwner":  True,
    "forRent":         False,
    "sold":            False,
    "priceMin":        100000,
    "priceMax":        500000,
}

# Property types that should pass through without filtering
_ACCEPTABLE_TYPES = {
    PropertyType.DUPLEX,
    PropertyType.TRIPLEX,
    PropertyType.QUADPLEX,
    PropertyType.SFR_ADU,
    PropertyType.SFR,
}


def _is_acceptable(rec: PropertyRecord) -> bool:
    """Return True if the property type is worth analyzing."""
    return rec.property_type in _ACCEPTABLE_TYPES


# ── Public function ────────────────────────────────────────────────────────────

def fetch_listings(client: ApifyClient) -> list[PropertyRecord]:
    """
    Run the Zillow for-sale actor(s) and return deduplicated PropertyRecord objects
    filtered to the JAX zip whitelist.

    Fetches both multifamily and SFH listings. SFH listings without ADU signals
    are included — the normalizer infers SFR_ADU from descriptions containing
    "adu", "garage apartment", "in-law suite", "guest house", etc.

    Args:
        client: Authenticated ApifyClient instance.

    Returns:
        Deduplicated list of PropertyRecord objects for whitelisted zips.
    """
    logger.info("zillow_listings_fetch_start")

    # Fetch multifamily
    mf_raw = client.run_actor(
        ZILLOW_SCRAPER,
        input_payload=_MF_ACTOR_INPUT,
        memory_mbytes=1024,
    )
    logger.info("zillow_mf_raw_count", count=len(mf_raw))

    # Fetch SFH (may timeout gracefully — SFH is optional)
    sfh_raw: list = []
    try:
        sfh_raw = client.run_actor(
            ZILLOW_SCRAPER,
            input_payload=_SFH_ACTOR_INPUT,
            memory_mbytes=512,
        )
        logger.info("zillow_sfh_raw_count", count=len(sfh_raw))
    except Exception as exc:
        logger.warning("zillow_sfh_fetch_failed", error=str(exc))

    all_raw = mf_raw + sfh_raw
    records: list[PropertyRecord] = []
    for item in all_raw:
        try:
            rec = normalize_zillow_listing(item)
            if rec.zip_code not in ZIP_WHITELIST:
                continue
            if not _is_acceptable(rec):
                continue
            records.append(rec)
        except Exception:
            logger.warning("zillow_listing_normalize_error", raw=item)

    deduped = deduplicate_property_records(records)
    logger.info("zillow_listings_after_dedup", count=len(deduped),
                sfh_count=sum(1 for r in deduped if r.property_type == PropertyType.SFR),
                sfh_adu_count=sum(1 for r in deduped if r.property_type == PropertyType.SFR_ADU))
    return deduped
