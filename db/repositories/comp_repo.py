"""
db/repositories/comp_repo.py

Async CRUD operations for the rental_comps table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_comp(session: AsyncSession, comp: dict[str, Any]) -> None:
    """Insert a rental comp (no dedup — comps accumulate over time)."""
    stmt = text("""
        INSERT INTO rental_comps (
            canonical_id, property_id, source, source_id, scraped_at,
            address, zip_code, lat, lon,
            beds, baths, sqft,
            rental_strategy, monthly_rate, adr, utilities_included, raw
        ) VALUES (
            :canonical_id, :property_id, :source, :source_id, :scraped_at,
            :address, :zip_code, :lat, :lon,
            :beds, :baths, :sqft,
            :rental_strategy, :monthly_rate, :adr, :utilities_included, :raw
        )
        ON CONFLICT DO NOTHING
    """)
    await session.execute(stmt, {
        "canonical_id":       comp["canonical_id"],
        "property_id":        comp.get("property_id"),
        "source":             comp["source"],
        "source_id":          comp.get("source_id", ""),
        "scraped_at":         comp.get("scraped_at", datetime.now(tz=timezone.utc)),
        "address":            comp["address"],
        "zip_code":           comp["zip_code"],
        "lat":                comp.get("lat"),
        "lon":                comp.get("lon"),
        "beds":               comp.get("beds"),
        "baths":              comp.get("baths"),
        "sqft":               comp.get("sqft"),
        "rental_strategy":    comp["rental_strategy"],
        "monthly_rate":       comp.get("monthly_rate"),
        "adr":                comp.get("adr"),
        "utilities_included": comp.get("utilities_included", False),
        "raw":                comp.get("raw", {}),
    })


async def get_comps_for_zip(
    session: AsyncSession,
    zip_code: str,
    strategy: str,
    beds: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch rental comps for a zip code and strategy."""
    if beds is not None:
        result = await session.execute(
            text("""SELECT * FROM rental_comps
                    WHERE zip_code = :zip AND rental_strategy = :strat AND beds = :beds
                    ORDER BY scraped_at DESC LIMIT :lim"""),
            {"zip": zip_code, "strat": strategy, "beds": beds, "lim": limit},
        )
    else:
        result = await session.execute(
            text("""SELECT * FROM rental_comps
                    WHERE zip_code = :zip AND rental_strategy = :strat
                    ORDER BY scraped_at DESC LIMIT :lim"""),
            {"zip": zip_code, "strat": strategy, "lim": limit},
        )
    return [dict(r) for r in result.mappings().all()]


async def count_comps(session: AsyncSession, zip_code: str, strategy: str) -> int:
    """Count available comps for a zip code + strategy."""
    result = await session.execute(
        text("SELECT COUNT(*) FROM rental_comps WHERE zip_code = :zip AND rental_strategy = :strat"),
        {"zip": zip_code, "strat": strategy},
    )
    return result.scalar_one()
