"""
db/repositories/crime_grade_repo.py

CRUD for crime_grade_cache and crime_grade_overrides tables.

TTL-aware lookup order:
  1. Check crime_grade_overrides (manual overrides always win)
  2. Check crime_grade_cache where scraped_at > NOW() - 7 days
  3. Return None → caller must scrape and then call store_grade()
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_TTL_DAYS = 7


async def get_cached_grade(session: AsyncSession, zip_code: str) -> dict | None:
    """
    Return a cached/overridden crime grade for zip_code, or None if not found / expired.

    Checks overrides first, then the scrape cache with 7-day TTL.
    """
    # 1. Manual override always wins
    result = await session.execute(
        text("SELECT grade, passes_gate, low_confidence FROM crime_grade_overrides WHERE zip_code = :z"),
        {"z": zip_code},
    )
    row = result.mappings().first()
    if row:
        return {"grade": row["grade"], "passes_gate": row["passes_gate"],
                "low_confidence": False, "source": "override"}

    # 2. Scrape cache within TTL
    result = await session.execute(
        text("""
            SELECT grade, passes_gate, low_confidence, scraped_at
            FROM crime_grade_cache
            WHERE zip_code = :z
              AND scraped_at > NOW() - INTERVAL ':days days'
        """.replace(":days", str(_CACHE_TTL_DAYS))),
        {"z": zip_code},
    )
    row = result.mappings().first()
    if row:
        return {"grade": row["grade"], "passes_gate": row["passes_gate"],
                "low_confidence": row["low_confidence"], "source": "cache"}

    return None


async def store_grade(
    session: AsyncSession,
    zip_code: str,
    grade: str,
    passes_gate: bool,
    low_confidence: bool = False,
) -> None:
    """Upsert a freshly-scraped crime grade into the cache."""
    await session.execute(
        text("""
            INSERT INTO crime_grade_cache (zip_code, grade, passes_gate, low_confidence, scraped_at)
            VALUES (:z, :grade, :passes, :lc, :now)
            ON CONFLICT (zip_code) DO UPDATE SET
                grade          = EXCLUDED.grade,
                passes_gate    = EXCLUDED.passes_gate,
                low_confidence = EXCLUDED.low_confidence,
                scraped_at     = EXCLUDED.scraped_at
        """),
        {
            "z":      zip_code,
            "grade":  grade,
            "passes": passes_gate,
            "lc":     low_confidence,
            "now":    datetime.now(tz=timezone.utc),
        },
    )


async def set_override(
    session: AsyncSession,
    zip_code: str,
    grade: str,
    passes_gate: bool,
    set_by: str = "manual",
) -> None:
    """Set or update a manual crime grade override."""
    await session.execute(
        text("""
            INSERT INTO crime_grade_overrides (zip_code, grade, passes_gate, set_by, set_at)
            VALUES (:z, :grade, :passes, :by, NOW())
            ON CONFLICT (zip_code) DO UPDATE SET
                grade       = EXCLUDED.grade,
                passes_gate = EXCLUDED.passes_gate,
                set_by      = EXCLUDED.set_by,
                set_at      = NOW()
        """),
        {"z": zip_code, "grade": grade, "passes": passes_gate, "by": set_by},
    )


async def list_overrides(session: AsyncSession) -> list[dict]:
    result = await session.execute(
        text("SELECT * FROM crime_grade_overrides ORDER BY zip_code")
    )
    return [dict(r) for r in result.mappings().all()]


async def list_cache(session: AsyncSession) -> list[dict]:
    result = await session.execute(
        text("SELECT * FROM crime_grade_cache ORDER BY scraped_at DESC")
    )
    return [dict(r) for r in result.mappings().all()]
