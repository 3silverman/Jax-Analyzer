"""db/repositories/score_repo.py — deal_scores CRUD."""

from __future__ import annotations
import json
from datetime import datetime
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class _DatetimeEncoder(json.JSONEncoder):
    """JSON encoder that serialises datetime objects to ISO-8601 strings."""
    def default(self, obj: object) -> object:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


async def insert_deal_score(session: AsyncSession, ds: dict[str, Any]) -> None:
    stmt = text("""
        INSERT INTO deal_scores (
            property_id, return_score, risk_score, confidence_score, deal_score,
            is_high_priority, alert_sent, alert_reasons,
            conservative_cash_flow, best_strategy, dscr, cash_on_cash,
            why_scored_high, assumptions_snapshot, deal_card_json
        ) VALUES (
            :property_id, :return_score, :risk_score, :confidence_score, :deal_score,
            :is_high_priority, :alert_sent, :alert_reasons,
            :conservative_cash_flow, :best_strategy, :dscr, :cash_on_cash,
            :why_scored_high, :assumptions_snapshot, :deal_card_json
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
        "assumptions_snapshot":  json.dumps(ds.get("assumptions_snapshot") or {}, cls=_DatetimeEncoder),
        "deal_card_json":        json.dumps(ds.get("deal_card_json") or {}, cls=_DatetimeEncoder),
    })


async def get_latest_score(session: AsyncSession, property_id: str) -> dict | None:
    result = await session.execute(
        text("SELECT * FROM deal_scores WHERE property_id = :pid ORDER BY scored_at DESC LIMIT 1"),
        {"pid": property_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_inbox_deals(
    session: AsyncSession,
    limit: int = 200,
) -> list[dict]:
    """Return active deals with full deal card data, sorted by deal_score DESC."""
    result = await session.execute(
        text("""
            SELECT DISTINCT ON (ds.property_id)
                ds.property_id, ds.deal_score, ds.return_score, ds.risk_score,
                ds.confidence_score, ds.is_high_priority, ds.why_scored_high,
                ds.conservative_cash_flow, ds.best_strategy, ds.dscr, ds.cash_on_cash,
                ds.alert_reasons, ds.deal_card_json, ds.scored_at,
                p.address, p.city, p.zip_code, p.price, p.property_type,
                p.num_units, p.beds, p.baths, p.sqft, p.year_built,
                p.days_on_market, p.canonical_id,
                ns.crime_grade, ns.walk_score, ns.flood_zone, ns.flood_high_risk,
                ns.liveability_total, ns.street_view_url, ns.satellite_url, ns.maps_link
            FROM deal_scores ds
            JOIN properties p ON ds.property_id = p.canonical_id
            LEFT JOIN neighborhood_scores ns ON ns.property_id = p.canonical_id
            WHERE p.status = 'active'
            ORDER BY ds.property_id, ds.scored_at DESC
        """),
    )
    rows = [dict(r) for r in result.mappings().all()]
    rows.sort(key=lambda r: r.get("deal_score", 0), reverse=True)
    return rows[:limit]


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


async def get_analytics_summary(session: AsyncSession) -> dict:
    """Return funnel metrics for the analytics page."""
    result = await session.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE p.status = 'active') AS total_active,
            COUNT(DISTINCT ns.property_id) FILTER (WHERE ns.passed_gates = TRUE) AS passed_gates,
            COUNT(DISTINCT ds.property_id) FILTER (WHERE ds.deal_score >= 60) AS scored_60_plus,
            COUNT(DISTINCT ds.property_id) FILTER (WHERE ds.is_high_priority = TRUE) AS high_priority,
            COUNT(DISTINCT o.property_id) FILTER (WHERE o.outcome = 'pursued') AS pursued,
            COUNT(DISTINCT o.property_id) FILTER (WHERE o.outcome = 'offer_made') AS offer_made,
            COUNT(DISTINCT o.property_id) FILTER (WHERE o.outcome = 'closed') AS closed
        FROM properties p
        LEFT JOIN neighborhood_scores ns ON ns.property_id = p.canonical_id
        LEFT JOIN (
            SELECT DISTINCT ON (property_id) property_id, deal_score, is_high_priority
            FROM deal_scores ORDER BY property_id, scored_at DESC
        ) ds ON ds.property_id = p.canonical_id
        LEFT JOIN (
            SELECT DISTINCT ON (property_id) property_id, outcome
            FROM outcomes ORDER BY property_id, created_at DESC
        ) o ON o.property_id = p.canonical_id
    """))
    row = result.mappings().first()
    return dict(row) if row else {}
