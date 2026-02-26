"""
db/repositories/property_repo.py

Async CRUD operations for the properties table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


async def upsert_property(session: AsyncSession, rec: dict[str, Any]) -> None:
    """Insert or update a property record."""
    stmt = text("""
        INSERT INTO properties (
            canonical_id, source, source_id, scraped_at,
            address, city, state, zip_code, lat, lon,
            price, beds, baths, sqft, year_built, property_type,
            num_units, lot_size_sqft, days_on_market,
            confidence_score, missing_fields, status, raw
        ) VALUES (
            :canonical_id, :source, :source_id, :scraped_at,
            :address, :city, :state, :zip_code, :lat, :lon,
            :price, :beds, :baths, :sqft, :year_built, :property_type,
            :num_units, :lot_size_sqft, :days_on_market,
            :confidence_score, :missing_fields, :status, :raw
        )
        ON CONFLICT (canonical_id) DO UPDATE SET
            source           = EXCLUDED.source,
            source_id        = EXCLUDED.source_id,
            scraped_at       = EXCLUDED.scraped_at,
            updated_at       = NOW(),
            price            = EXCLUDED.price,
            beds             = COALESCE(EXCLUDED.beds, properties.beds),
            baths            = COALESCE(EXCLUDED.baths, properties.baths),
            sqft             = COALESCE(EXCLUDED.sqft, properties.sqft),
            year_built       = COALESCE(EXCLUDED.year_built, properties.year_built),
            property_type    = EXCLUDED.property_type,
            num_units        = EXCLUDED.num_units,
            days_on_market   = EXCLUDED.days_on_market,
            confidence_score = EXCLUDED.confidence_score,
            missing_fields   = EXCLUDED.missing_fields,
            raw              = EXCLUDED.raw
    """)
    await session.execute(stmt, {
        "canonical_id":    rec["canonical_id"],
        "source":          rec["source"],
        "source_id":       rec.get("source_id", ""),
        "scraped_at":      rec.get("scraped_at", datetime.now(tz=timezone.utc)),
        "address":         rec["address"],
        "city":            rec.get("city", "Jacksonville"),
        "state":           rec.get("state", "FL"),
        "zip_code":        rec["zip_code"],
        "lat":             rec.get("lat"),
        "lon":             rec.get("lon"),
        "price":           rec["price"],
        "beds":            rec.get("beds"),
        "baths":           rec.get("baths"),
        "sqft":            rec.get("sqft"),
        "year_built":      rec.get("year_built"),
        "property_type":   rec.get("property_type", "unknown"),
        "num_units":       rec.get("num_units", 1),
        "lot_size_sqft":   rec.get("lot_size_sqft"),
        "days_on_market":  rec.get("days_on_market"),
        "confidence_score": rec.get("confidence_score", 0.0),
        "missing_fields":  rec.get("missing_fields", []),
        "status":          rec.get("status", "active"),
        "raw":             rec.get("raw", {}),
    })


async def get_property(session: AsyncSession, canonical_id: str) -> dict | None:
    """Fetch a single property by canonical_id."""
    result = await session.execute(
        text("SELECT * FROM properties WHERE canonical_id = :cid"),
        {"cid": canonical_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_active_properties(
    session: AsyncSession,
    zip_codes: list[str] | None = None,
    limit: int = 500,
) -> list[dict]:
    """Fetch active properties, optionally filtered by zip codes."""
    if zip_codes:
        result = await session.execute(
            text("SELECT * FROM properties WHERE status = 'active' AND zip_code = ANY(:zips) LIMIT :lim"),
            {"zips": zip_codes, "lim": limit},
        )
    else:
        result = await session.execute(
            text("SELECT * FROM properties WHERE status = 'active' LIMIT :lim"),
            {"lim": limit},
        )
    return [dict(r) for r in result.mappings().all()]


async def update_property_status(
    session: AsyncSession,
    canonical_id: str,
    status: str,
) -> None:
    """Update status of a property."""
    await session.execute(
        text("UPDATE properties SET status = :status, updated_at = NOW() WHERE canonical_id = :cid"),
        {"status": status, "cid": canonical_id},
    )
