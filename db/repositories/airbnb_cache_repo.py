"""db/repositories/airbnb_cache_repo.py — weekly per-zip/bedrooms Airbnb comp cache.

Cache key: (zip_code, bedrooms, week_number) using ISO calendar week.
STR comp results are expensive (Apify actor run) and stable within a week.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_cached_str_comps(
    session: AsyncSession,
    zip_code: str,
    bedrooms: int,
    week_number: int,
) -> dict[str, Any] | None:
    """Return cached STR comp result for zip+bedrooms+week, or None if absent."""
    result = await session.execute(
        text(
            "SELECT zip_code, bedrooms, week_number, comp_count, median_adr, "
            "estimated_occupancy, gross_monthly, net_monthly, confidence, "
            "str_validated, fetched_at "
            "FROM airbnb_comp_cache "
            "WHERE zip_code = :zip AND bedrooms = :beds AND week_number = :week"
        ),
        {"zip": zip_code, "beds": bedrooms, "week": week_number},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def upsert_str_comp_cache(
    session: AsyncSession,
    zip_code: str,
    bedrooms: int,
    week_number: int,
    comp_count: int,
    median_adr: float | None,
    estimated_occupancy: float | None,
    gross_monthly: float | None,
    net_monthly: float | None,
    confidence: str,
    str_validated: bool,
    comps_json: list[dict] | None = None,
) -> None:
    """Insert or update the Airbnb comp cache entry for zip+bedrooms+week."""
    await session.execute(
        text(
            "INSERT INTO airbnb_comp_cache "
            "(zip_code, bedrooms, week_number, comp_count, median_adr, "
            "estimated_occupancy, gross_monthly, net_monthly, confidence, "
            "str_validated, comps_json, fetched_at) "
            "VALUES (:zip, :beds, :week, :count, :adr, :occ, :gross, :net, "
            ":conf, :validated, :comps, NOW()) "
            "ON CONFLICT (zip_code, bedrooms, week_number) DO UPDATE SET "
            "comp_count = :count, median_adr = :adr, "
            "estimated_occupancy = :occ, gross_monthly = :gross, "
            "net_monthly = :net, confidence = :conf, str_validated = :validated, "
            "comps_json = :comps, fetched_at = NOW()"
        ),
        {
            "zip":       zip_code,
            "beds":      bedrooms,
            "week":      week_number,
            "count":     comp_count,
            "adr":       median_adr,
            "occ":       estimated_occupancy,
            "gross":     gross_monthly,
            "net":       net_monthly,
            "conf":      confidence,
            "validated": str_validated,
            "comps":     json.dumps(comps_json or []),
        },
    )
