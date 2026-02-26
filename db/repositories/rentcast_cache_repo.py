"""db/repositories/rentcast_cache_repo.py — 7-day per-property Rentcast API cache.

Prevents repeat API calls for the same listing sitting unsold across multiple
daily scan runs.  Keyed on canonical_id; TTL enforced in pipeline logic.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_cached_rentcast(
    session: AsyncSession,
    canonical_id: str,
) -> dict[str, Any] | None:
    """Return the cache entry for a property, or None if never cached.

    Returns dict with keys: canonical_id, zip_code, ltr_estimate, fetched_at.
    Staleness check (7-day TTL) is the caller's responsibility.
    """
    result = await session.execute(
        text(
            "SELECT canonical_id, zip_code, ltr_estimate, fetched_at "
            "FROM rentcast_cache WHERE canonical_id = :cid"
        ),
        {"cid": canonical_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def upsert_rentcast_cache(
    session: AsyncSession,
    canonical_id: str,
    zip_code: str,
    ltr_estimate: float | None,
) -> None:
    """Insert or update a cache entry, refreshing fetched_at to NOW()."""
    await session.execute(
        text(
            "INSERT INTO rentcast_cache (canonical_id, zip_code, ltr_estimate, fetched_at) "
            "VALUES (:cid, :zip, :ltr, NOW()) "
            "ON CONFLICT (canonical_id) DO UPDATE "
            "SET zip_code = :zip, ltr_estimate = :ltr, fetched_at = NOW()"
        ),
        {"cid": canonical_id, "zip": zip_code, "ltr": ltr_estimate},
    )
