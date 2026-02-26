"""Tests for db/repositories/rentcast_cache_repo.py.

All DB I/O is mocked — no real Postgres required.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest

from db.repositories.rentcast_cache_repo import (
    get_cached_rentcast,
    upsert_rentcast_cache,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_row(canonical_id: str, ltr: float | None, days_ago: int = 0) -> MagicMock:
    fetched_at = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    row = MagicMock()
    row.__iter__ = lambda self: iter([
        ("canonical_id", canonical_id),
        ("zip_code", "32204"),
        ("ltr_estimate", ltr),
        ("fetched_at", fetched_at),
    ])
    row.keys = lambda: ["canonical_id", "zip_code", "ltr_estimate", "fetched_at"]
    # Support dict() conversion via mappings
    row.__getitem__ = lambda self, k: {
        "canonical_id": canonical_id,
        "zip_code": "32204",
        "ltr_estimate": ltr,
        "fetched_at": fetched_at,
    }[k]
    return row


def _session_returning(row: MagicMock | None) -> AsyncMock:
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


# ── get_cached_rentcast ───────────────────────────────────────────────────────

class TestGetCachedRentcast:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_row(self) -> None:
        session = _session_returning(None)
        result = await get_cached_rentcast(session, "abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_execute_with_canonical_id(self) -> None:
        session = _session_returning(None)
        await get_cached_rentcast(session, "abc123")
        assert session.execute.called
        call_sql = str(session.execute.call_args[0][0])
        assert "rentcast_cache" in call_sql

    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self) -> None:
        row = _make_row("abc123", ltr=2400.0, days_ago=2)
        session = _session_returning(row)
        result = await get_cached_rentcast(session, "abc123")
        assert result is not None
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_passes_canonical_id_as_param(self) -> None:
        session = _session_returning(None)
        await get_cached_rentcast(session, "myid_xyz")
        params = session.execute.call_args[0][1]
        assert params["cid"] == "myid_xyz"


# ── upsert_rentcast_cache ─────────────────────────────────────────────────────

class TestUpsertRentcastCache:
    @pytest.mark.asyncio
    async def test_calls_execute(self) -> None:
        session = AsyncMock()
        await upsert_rentcast_cache(session, "abc123", "32204", 2400.0)
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_passes_all_params(self) -> None:
        session = AsyncMock()
        await upsert_rentcast_cache(session, "abc123", "32204", 1800.0)
        params = session.execute.call_args[0][1]
        assert params["cid"] == "abc123"
        assert params["zip"] == "32204"
        assert params["ltr"] == pytest.approx(1800.0)

    @pytest.mark.asyncio
    async def test_accepts_none_ltr_estimate(self) -> None:
        session = AsyncMock()
        await upsert_rentcast_cache(session, "no_estimate", "32205", None)
        params = session.execute.call_args[0][1]
        assert params["ltr"] is None

    @pytest.mark.asyncio
    async def test_sql_contains_on_conflict(self) -> None:
        session = AsyncMock()
        await upsert_rentcast_cache(session, "abc", "32204", 0.0)
        sql = str(session.execute.call_args[0][0])
        assert "ON CONFLICT" in sql.upper()

    @pytest.mark.asyncio
    async def test_sql_updates_fetched_at(self) -> None:
        session = AsyncMock()
        await upsert_rentcast_cache(session, "abc", "32204", 0.0)
        sql = str(session.execute.call_args[0][0])
        assert "fetched_at" in sql.lower()
