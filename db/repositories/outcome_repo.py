"""db/repositories/outcome_repo.py — outcomes CRUD."""

from __future__ import annotations
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def record_outcome(
    session: AsyncSession,
    property_id: str,
    outcome: str,
    notes: str = "",
    offer_price: float | None = None,
    under_contract: bool = False,
    closed_price: float | None = None,
) -> None:
    await session.execute(
        text("""
            INSERT INTO outcomes (property_id, outcome, notes, offer_price, under_contract, closed_price)
            VALUES (:pid, :outcome, :notes, :offer_price, :under_contract, :closed_price)
        """),
        {
            "pid":            property_id,
            "outcome":        outcome,
            "notes":          notes,
            "offer_price":    offer_price,
            "under_contract": under_contract,
            "closed_price":   closed_price,
        },
    )


async def update_outcome_fields(
    session: AsyncSession,
    outcome_id: str,
    updates: dict[str, Any],
) -> None:
    """Update specific fields on an outcome row."""
    allowed = {"outcome", "notes", "offer_price", "under_contract", "closed_price", "viewed_at"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in filtered)
    filtered["oid"] = outcome_id
    await session.execute(
        text(f"UPDATE outcomes SET {set_clause} WHERE id = :oid"),
        filtered,
    )


async def mark_viewed(session: AsyncSession, property_id: str) -> None:
    """Set viewed_at on the latest outcome for a property if not already set."""
    await session.execute(
        text("""
            UPDATE outcomes SET viewed_at = NOW()
            WHERE id = (
                SELECT id FROM outcomes WHERE property_id = :pid
                ORDER BY created_at DESC LIMIT 1
            ) AND viewed_at IS NULL
        """),
        {"pid": property_id},
    )


async def get_outcomes(session: AsyncSession, property_id: str) -> list[dict]:
    result = await session.execute(
        text("SELECT * FROM outcomes WHERE property_id = :pid ORDER BY created_at DESC"),
        {"pid": property_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_shortlisted(session: AsyncSession) -> list[dict]:
    """Return all properties with an active (non-rejected) outcome."""
    result = await session.execute(
        text("""SELECT DISTINCT ON (o.property_id)
                    o.*, p.address, p.price, p.zip_code, p.property_type, p.num_units,
                    ds.deal_score, ds.deal_card_json
                FROM outcomes o
                JOIN properties p ON o.property_id = p.canonical_id
                LEFT JOIN LATERAL (
                    SELECT deal_score, deal_card_json
                    FROM deal_scores WHERE property_id = o.property_id
                    ORDER BY scored_at DESC LIMIT 1
                ) ds ON TRUE
                WHERE o.outcome != 'rejected'
                ORDER BY o.property_id, o.created_at DESC"""),
    )
    return [dict(r) for r in result.mappings().all()]


async def get_funnel_metrics(session: AsyncSession) -> dict:
    """Return outcome funnel counts."""
    result = await session.execute(text("""
        SELECT
            COUNT(DISTINCT property_id) FILTER (WHERE outcome = 'pursued')     AS pursued,
            COUNT(DISTINCT property_id) FILTER (WHERE outcome = 'offer_made')  AS offer_made,
            COUNT(DISTINCT property_id) FILTER (WHERE outcome = 'closed')      AS closed,
            COUNT(DISTINCT property_id) FILTER (WHERE outcome = 'rejected')    AS rejected,
            COUNT(DISTINCT property_id) FILTER (WHERE under_contract = TRUE)   AS under_contract,
            AVG(offer_price)  FILTER (WHERE offer_price IS NOT NULL)           AS avg_offer_price,
            AVG(closed_price) FILTER (WHERE closed_price IS NOT NULL)          AS avg_closed_price
        FROM outcomes
    """))
    row = result.mappings().first()
    return dict(row) if row else {}
