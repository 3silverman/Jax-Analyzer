"""db/repositories/outcome_repo.py — outcomes CRUD."""

from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def record_outcome(
    session: AsyncSession,
    property_id: str,
    outcome: str,
    notes: str = "",
) -> None:
    await session.execute(
        text("INSERT INTO outcomes (property_id, outcome, notes) VALUES (:pid, :outcome, :notes)"),
        {"pid": property_id, "outcome": outcome, "notes": notes},
    )


async def get_outcomes(session: AsyncSession, property_id: str) -> list[dict]:
    result = await session.execute(
        text("SELECT * FROM outcomes WHERE property_id = :pid ORDER BY created_at DESC"),
        {"pid": property_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_shortlisted(session: AsyncSession) -> list[dict]:
    """Return all properties that have an active (non-rejected) outcome."""
    result = await session.execute(
        text("""SELECT DISTINCT ON (o.property_id)
                    o.*, p.address, p.price, p.zip_code, p.property_type, p.num_units
                FROM outcomes o
                JOIN properties p ON o.property_id = p.canonical_id
                WHERE o.outcome != 'rejected'
                ORDER BY o.property_id, o.created_at DESC"""),
    )
    return [dict(r) for r in result.mappings().all()]
