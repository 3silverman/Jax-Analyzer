"""db/repositories/assumptions_repo.py — assumptions single-row CRUD."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Built-in presets ─────────────────────────────────────────────────────────

_PRESETS: dict[str, dict[str, Any]] = {
    "conservative": {
        "interest_rate":    0.085,
        "insurance_pct":    0.006,
        "ltr_vacancy":      0.10,
        "mtr_vacancy":      0.12,
        "str_vacancy":      0.30,
        "mgmt_rate":        0.10,
        "capex_rate":       0.008,
        "maintenance_rate": 0.015,
    },
    "base": {
        "interest_rate":    0.075,
        "insurance_pct":    0.005,
        "ltr_vacancy":      0.08,
        "mtr_vacancy":      0.10,
        "str_vacancy":      0.25,
        "mgmt_rate":        0.08,
        "capex_rate":       0.005,
        "maintenance_rate": 0.010,
    },
    "optimistic": {
        "interest_rate":    0.065,
        "insurance_pct":    0.004,
        "ltr_vacancy":      0.05,
        "mtr_vacancy":      0.07,
        "str_vacancy":      0.18,
        "mgmt_rate":        0.07,
        "capex_rate":       0.003,
        "maintenance_rate": 0.007,
    },
}


_RATE_FIELDS = (
    "interest_rate", "insurance_pct", "ltr_vacancy", "mtr_vacancy",
    "str_vacancy", "mgmt_rate", "capex_rate", "maintenance_rate",
)


def get_preset_values(preset_name: str) -> dict[str, Any] | None:
    """Return the rate values for a built-in named preset, or None if unknown/my_settings."""
    return _PRESETS.get(preset_name)


async def get_my_settings_snapshot(session: AsyncSession) -> dict[str, Any] | None:
    """Return the stored My Settings snapshot, or None if never saved."""
    result = await session.execute(
        text("SELECT my_settings_snapshot FROM assumptions WHERE id = 1")
    )
    row = result.mappings().first()
    if row and row["my_settings_snapshot"]:
        return dict(row["my_settings_snapshot"])
    return None


async def save_my_settings(session: AsyncSession, values: dict[str, Any]) -> None:
    """Snapshot the given rate values as My Settings and set it as the active preset."""
    import json as _json
    snapshot = {k: values[k] for k in _RATE_FIELDS if k in values}
    await session.execute(
        text(
            "UPDATE assumptions "
            "SET my_settings_snapshot = :snap::jsonb, "
            "    active_preset = 'my_settings', "
            "    updated_at = NOW() "
            "WHERE id = 1"
        ),
        {"snap": _json.dumps(snapshot)},
    )


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
        "active_preset",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in filtered)
    await session.execute(
        text(f"UPDATE assumptions SET {set_clause}, updated_at = NOW() WHERE id = 1"),
        filtered,
    )


async def apply_preset(session: AsyncSession, preset_name: str) -> bool:
    """Apply a named preset's rate values and record which preset is active.

    Returns True if the preset was applied, False if not found (e.g. my_settings
    with no saved snapshot).
    """
    if preset_name == "my_settings":
        snapshot = await get_my_settings_snapshot(session)
        if snapshot is None:
            return False  # nothing saved yet
        updates = {**snapshot, "active_preset": "my_settings"}
        await update_assumptions(session, updates)
        return True

    values = get_preset_values(preset_name)
    if values is None:
        return False
    updates = {**values, "active_preset": preset_name}
    await update_assumptions(session, updates)
    return True


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
        "active_preset":    "custom",
    }
