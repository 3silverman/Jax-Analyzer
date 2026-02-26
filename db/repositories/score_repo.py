"""db/repositories/score_repo.py — deal_scores CRUD."""

from __future__ import annotations
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_deal_score(session: AsyncSession, ds: dict[str, Any]) -> None:
    stmt = text("""
        INSERT INTO deal_scores (
            property_id, return_score, risk_score, confidence_score, deal_score,
            is_high_priority, alert_sent, alert_reasons,
            conservative_cash_flow, best_strategy, dscr, cash_on_cash,
            why_scored_high, assumptions_snapshot
        ) VALUES (
            :property_id, :return_score, :risk_score, :confidence_score, :deal_score,
            :is_high_priority, :alert_sent, :alert_reasons,
            :conservative_cash_flow, :best_strategy, :dscr, :cash_on_cash,
            :why_scored_high, :assumptions_snapshot
        )
    """)
    await session.execute(stmt, {
        "property_id":           ds["property_id"],
        "return_score":          ds.get("return_score", 0),
        "risk_score":            ds.get("risk_score", 0),
        "confidence_score":      ds.get("confidence_score", 0),
        "deal_score":            ds.get("deal_score", 0),
        "is_high_priority":      ds.get("is_high_priority", False),
        "alert_sent":            ds.get("alert_sent", False),
        "alert_reasons":         ds.get("alert_reasons", []),
        "conservative_cash_flow": ds.get("conservative_cash_flow"),
        "best_strategy":         ds.get("best_strategy"),
        "dscr":                  ds.get("dscr"),
        "cash_on_cash":          ds.get("cash_on_cash"),
        "why_scored_high":       ds.get("why_scored_high"),
        "assumptions_snapshot":  ds.get("assumptions_snapshot", {}),
    })


async def get_latest_score(session: AsyncSession, property_id: str) -> dict | None:
    result = await session.execute(
        text("SELECT * FROM deal_scores WHERE property_id = :pid ORDER BY scored_at DESC LIMIT 1"),
        {"pid": property_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_top_deals(
    session: AsyncSession,
    min_score: int = 60,
    limit: int = 100,
) -> list[dict]:
    result = await session.execute(
        text("""SELECT ds.*, p.address, p.price, p.zip_code, p.property_type, p.num_units
                FROM deal_scores ds
                JOIN properties p ON ds.property_id = p.canonical_id
                WHERE ds.deal_score >= :min AND p.status = 'active'
                ORDER BY ds.deal_score DESC, ds.scored_at DESC
                LIMIT :lim"""),
        {"min": min_score, "lim": limit},
    )
    return [dict(r) for r in result.mappings().all()]


async def mark_alert_sent(session: AsyncSession, score_id: str) -> None:
    await session.execute(
        text("UPDATE deal_scores SET alert_sent = TRUE WHERE id = :sid"),
        {"sid": score_id},
    )
