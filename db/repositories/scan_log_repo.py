"""db/repositories/scan_log_repo.py — scan_log CRUD."""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def start_scan(session: AsyncSession) -> str:
    """Insert a new scan_log row; return scan_id."""
    result = await session.execute(
        text("INSERT INTO scan_log (started_at) VALUES (:now) RETURNING scan_id"),
        {"now": datetime.now(tz=timezone.utc)},
    )
    return str(result.scalar_one())


async def complete_scan(
    session: AsyncSession,
    scan_id: str,
    properties_scanned: int,
    passed_gates: int,
    alerts_triggered: int,
    errors: list[str],
) -> None:
    await session.execute(
        text("""UPDATE scan_log SET
                    completed_at        = NOW(),
                    properties_scanned  = :scanned,
                    passed_gates        = :passed,
                    alerts_triggered    = :alerts,
                    errors              = :errors
                WHERE scan_id = :sid"""),
        {
            "sid":     scan_id,
            "scanned": properties_scanned,
            "passed":  passed_gates,
            "alerts":  alerts_triggered,
            "errors":  json.dumps(errors),
        },
    )


async def get_scan_history(session: AsyncSession, limit: int = 30) -> list[dict]:
    result = await session.execute(
        text("SELECT * FROM scan_log ORDER BY started_at DESC LIMIT :lim"),
        {"lim": limit},
    )
    return [dict(r) for r in result.mappings().all()]
