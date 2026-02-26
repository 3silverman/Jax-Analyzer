"""db/repositories/assumptions_repo.py — assumptions single-row CRUD."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_assumptions(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(text("SELECT * FROM assumptions WHERE id = 1"))
    row = result.mappings().first()
    return dict(row) if row else _defaults()


async def update_assumptions(session: AsyncSession, updates: dict[str, Any]) -> None:
    allowed = {
        "interest_rate", "insurance_pct", "ltr_vacancy", "mtr_vacancy",
        "str_vacancy", "mgmt_rate", "capex_rate", "maintenance_rate",
        "va_funding_fee_pct", "loan_term_years", "closing_costs_pct",
        "alert_threshold", "min_cash_flow", "min_dscr",
        "current_va_rate", "rate_fetched_at", "rate_is_stale", "rate_source",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in filtered)
    await session.execute(
        text(f"UPDATE assumptions SET {set_clause}, updated_at = NOW() WHERE id = 1"),
        filtered,
    )


async def update_va_rate(
    session: AsyncSession,
    rate: float,
    fetched_at: datetime,
    source: str,
    is_stale: bool = False,
) -> None:
    """Store the latest VA rate fetch result in the assumptions row."""
    await session.execute(
        text(
            "UPDATE assumptions SET "
            "current_va_rate = :rate, "
            "rate_fetched_at = :fetched_at, "
            "rate_source = :source, "
            "rate_is_stale = :is_stale, "
            "updated_at = NOW() "
            "WHERE id = 1"
        ),
        {"rate": rate, "fetched_at": fetched_at, "source": source, "is_stale": is_stale},
    )


def _defaults() -> dict[str, Any]:
    return {
        "interest_rate":    0.075,
        "insurance_pct":    0.005,
        "ltr_vacancy":      0.08,
        "mtr_vacancy":      0.10,
        "str_vacancy":      0.25,
        "mgmt_rate":        0.08,
        "capex_rate":       0.005,
        "maintenance_rate": 0.01,
        "va_funding_fee_pct": 0.0215,
        "loan_term_years":  30,
        "closing_costs_pct": 0.03,
        "alert_threshold":  85,
        "min_cash_flow":    500.0,
        "min_dscr":         1.2,
        "current_va_rate":  None,
        "rate_fetched_at":  None,
        "rate_is_stale":    False,
        "rate_source":      "fallback",
    }
