"""Tests for db/repositories/airbnb_cache_repo.py.

Mocks the async SQLAlchemy session — no real DB required.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from db.repositories.airbnb_cache_repo import (
    get_cached_str_comps,
    upsert_str_comp_cache,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_session_returning(row_data: dict | None) -> AsyncMock:
    """Return a mock session whose SELECT returns the given row dict (or None)."""
    if row_data is not None:
        row = MagicMock()
        row.__iter__ = MagicMock(return_value=iter(row_data.items()))
        row.keys     = MagicMock(return_value=row_data.keys())
        # Support dict(row) via mappings().first()
        mapping = MagicMock()
        mapping.keys  = MagicMock(return_value=row_data.keys())
        mapping.__getitem__ = lambda self, k: row_data[k]
        mapping.__iter__    = lambda self: iter(row_data)
        # Make dict(row) work by returning the dict directly from mappings().first()
        first_result = MagicMock()
        first_result.__bool__ = MagicMock(return_value=True)
        for k, v in row_data.items():
            setattr(first_result, k, v)
        first_result.__class__   = dict.__class__
        # The repo does dict(row) on the mappings().first() result
        # Use a real dict-like object
        row_dict = dict(row_data)
        result = MagicMock()
        result.mappings.return_value.first.return_value = row_dict
    else:
        result = MagicMock()
        result.mappings.return_value.first.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


# ── get_cached_str_comps ──────────────────────────────────────────────────────

class TestGetCachedStrComps:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_row(self):
        session = _mock_session_returning(None)
        result = await get_cached_str_comps(session, "32204", 1, 9)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self):
        row = {
            "zip_code": "32204",
            "bedrooms": 1,
            "week_number": 9,
            "comp_count": 8,
            "median_adr": 102.0,
            "estimated_occupancy": 0.63,
            "gross_monthly": 1950.0,
            "net_monthly": 1305.0,
            "confidence": "MEDIUM",
            "str_validated": True,
            "fetched_at": "2026-02-26T00:00:00+00:00",
        }
        session = _mock_session_returning(row)
        result = await get_cached_str_comps(session, "32204", 1, 9)
        assert result is not None
        assert result["comp_count"] == 8
        assert result["median_adr"] == 102.0
        assert result["str_validated"] is True

    @pytest.mark.asyncio
    async def test_calls_execute_once(self):
        session = _mock_session_returning(None)
        await get_cached_str_comps(session, "32204", 2, 10)
        assert session.execute.call_count == 1


# ── upsert_str_comp_cache ─────────────────────────────────────────────────────

class TestUpsertStrCompCache:
    @pytest.mark.asyncio
    async def test_calls_execute(self):
        session = AsyncMock()
        await upsert_str_comp_cache(
            session,
            zip_code="32204",
            bedrooms=1,
            week_number=9,
            comp_count=8,
            median_adr=102.0,
            estimated_occupancy=0.63,
            gross_monthly=1950.0,
            net_monthly=1305.0,
            confidence="MEDIUM",
            str_validated=True,
            comps_json=[{"price": 100}],
        )
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_comps_json_serialized(self):
        session = AsyncMock()
        comps = [{"price": 95, "address": "123 Main"}]
        await upsert_str_comp_cache(
            session,
            zip_code="32204",
            bedrooms=1,
            week_number=9,
            comp_count=1,
            median_adr=95.0,
            estimated_occupancy=0.60,
            gross_monthly=1730.0,
            net_monthly=1155.0,
            confidence="LOW",
            str_validated=False,
            comps_json=comps,
        )
        call_args = session.execute.call_args
        params = call_args[0][1] if call_args[0] else call_args[1]
        assert isinstance(params["comps"], str)
        decoded = json.loads(params["comps"])
        assert decoded[0]["price"] == 95

    @pytest.mark.asyncio
    async def test_none_comps_json_uses_empty_list(self):
        session = AsyncMock()
        await upsert_str_comp_cache(
            session,
            zip_code="32204",
            bedrooms=2,
            week_number=9,
            comp_count=0,
            median_adr=None,
            estimated_occupancy=None,
            gross_monthly=None,
            net_monthly=None,
            confidence="LOW",
            str_validated=False,
            comps_json=None,
        )
        call_args = session.execute.call_args
        params = call_args[0][1] if call_args[0] else call_args[1]
        assert json.loads(params["comps"]) == []

    @pytest.mark.asyncio
    async def test_params_match_inputs(self):
        session = AsyncMock()
        await upsert_str_comp_cache(
            session,
            zip_code="32205",
            bedrooms=3,
            week_number=12,
            comp_count=10,
            median_adr=135.0,
            estimated_occupancy=0.70,
            gross_monthly=2870.0,
            net_monthly=2000.0,
            confidence="HIGH",
            str_validated=True,
        )
        call_args = session.execute.call_args
        params = call_args[0][1] if call_args[0] else call_args[1]
        assert params["zip"]       == "32205"
        assert params["beds"]      == 3
        assert params["week"]      == 12
        assert params["count"]     == 10
        assert params["adr"]       == 135.0
        assert params["occ"]       == 0.70
        assert params["gross"]     == 2870.0
        assert params["net"]       == 2000.0
        assert params["conf"]      == "HIGH"
        assert params["validated"] is True
