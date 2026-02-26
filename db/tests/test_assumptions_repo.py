"""Tests for db/repositories/assumptions_repo.py — preset and My Settings logic.

These tests mock the async SQLAlchemy session so no real DB is required.
"""

from __future__ import annotations
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from db.repositories.assumptions_repo import (
    _PRESETS,
    _RATE_FIELDS,
    apply_preset,
    get_my_settings_snapshot,
    get_preset_values,
    save_my_settings,
)


# ── Helper: build a fake async session ──────────────────────────────────────

def _mock_session_with_snapshot(snapshot: dict | None) -> AsyncMock:
    """Return a mock session whose SELECT returns the given snapshot value."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: snapshot  # row["my_settings_snapshot"]
    result = MagicMock()
    result.mappings.return_value.first.return_value = row if snapshot is not None else None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


# ── get_preset_values (pure function, no DB) ─────────────────────────────────

class TestGetPresetValues:
    def test_conservative_returns_dict(self) -> None:
        v = get_preset_values("conservative")
        assert v is not None
        assert "interest_rate" in v
        assert v["ltr_vacancy"] == 0.10  # 10%

    def test_base_returns_dict(self) -> None:
        v = get_preset_values("base")
        assert v is not None
        assert v["ltr_vacancy"] == 0.08  # 8%

    def test_optimistic_returns_dict(self) -> None:
        v = get_preset_values("optimistic")
        assert v is not None
        assert v["interest_rate"] < get_preset_values("base")["interest_rate"]

    def test_my_settings_returns_none(self) -> None:
        # my_settings is not a built-in preset — handled by DB snapshot
        assert get_preset_values("my_settings") is None

    def test_custom_returns_none(self) -> None:
        assert get_preset_values("custom") is None

    def test_unknown_returns_none(self) -> None:
        assert get_preset_values("bogus") is None

    def test_all_rate_fields_present_in_each_preset(self) -> None:
        for name, values in _PRESETS.items():
            for field in _RATE_FIELDS:
                assert field in values, f"'{field}' missing from preset '{name}'"

    def test_conservative_stricter_than_base(self) -> None:
        c = get_preset_values("conservative")
        b = get_preset_values("base")
        assert c["ltr_vacancy"] > b["ltr_vacancy"]
        assert c["interest_rate"] > b["interest_rate"]

    def test_optimistic_looser_than_base(self) -> None:
        o = get_preset_values("optimistic")
        b = get_preset_values("base")
        assert o["ltr_vacancy"] < b["ltr_vacancy"]
        assert o["interest_rate"] < b["interest_rate"]


# ── get_my_settings_snapshot ─────────────────────────────────────────────────

class TestGetMySettingsSnapshot:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_snapshot(self) -> None:
        session = _mock_session_with_snapshot(None)
        result = await get_my_settings_snapshot(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_snapshot_dict_when_saved(self) -> None:
        snap = {"interest_rate": 0.07, "ltr_vacancy": 0.09, "insurance_pct": 0.005,
                "mtr_vacancy": 0.10, "str_vacancy": 0.22, "mgmt_rate": 0.08,
                "capex_rate": 0.004, "maintenance_rate": 0.009}
        session = _mock_session_with_snapshot(snap)
        result = await get_my_settings_snapshot(session)
        assert result == snap


# ── save_my_settings ─────────────────────────────────────────────────────────

class TestSaveMySettings:
    @pytest.mark.asyncio
    async def test_calls_execute_with_json_snapshot(self) -> None:
        session = AsyncMock()
        values = {
            "interest_rate": 0.072,
            "insurance_pct": 0.0045,
            "ltr_vacancy":   0.09,
            "mtr_vacancy":   0.11,
            "str_vacancy":   0.22,
            "mgmt_rate":     0.085,
            "capex_rate":    0.004,
            "maintenance_rate": 0.009,
            # extra key that should be stripped
            "active_preset": "custom",
        }
        await save_my_settings(session, values)
        assert session.execute.called
        call_args = session.execute.call_args
        params = call_args[0][1] if call_args[0] else call_args[1]
        snap_str = params["snap"]
        snap = json.loads(snap_str)
        # Only _RATE_FIELDS should be in the snapshot
        assert set(snap.keys()) == set(_RATE_FIELDS)
        assert snap["interest_rate"] == 0.072
        assert "active_preset" not in snap

    @pytest.mark.asyncio
    async def test_snapshot_excludes_non_rate_fields(self) -> None:
        session = AsyncMock()
        values = {
            "interest_rate": 0.07,
            "insurance_pct": 0.005,
            "ltr_vacancy":   0.08,
            "mtr_vacancy":   0.10,
            "str_vacancy":   0.25,
            "mgmt_rate":     0.08,
            "capex_rate":    0.005,
            "maintenance_rate": 0.01,
            "alert_threshold": 85,   # should not be snapshotted
        }
        await save_my_settings(session, values)
        call_args = session.execute.call_args
        params = call_args[0][1] if call_args[0] else call_args[1]
        snap = json.loads(params["snap"])
        assert "alert_threshold" not in snap


# ── apply_preset ─────────────────────────────────────────────────────────────

class TestApplyPreset:
    @pytest.mark.asyncio
    async def test_built_in_preset_returns_true(self) -> None:
        session = AsyncMock()
        result = await apply_preset(session, "base")
        assert result is True
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_unknown_preset_returns_false(self) -> None:
        session = AsyncMock()
        result = await apply_preset(session, "bogus")
        assert result is False
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_my_settings_returns_false_when_no_snapshot(self) -> None:
        # get_my_settings_snapshot returns None → apply_preset returns False
        session = _mock_session_with_snapshot(None)
        result = await apply_preset(session, "my_settings")
        assert result is False

    @pytest.mark.asyncio
    async def test_my_settings_returns_true_when_snapshot_exists(self) -> None:
        snap = {k: 0.05 for k in _RATE_FIELDS}
        session = _mock_session_with_snapshot(snap)
        # The second execute (for update_assumptions) should also work
        update_result = MagicMock()
        update_result.mappings.return_value.first.return_value = None
        # First call is SELECT for snapshot, subsequent calls are UPDATE
        session.execute = AsyncMock(side_effect=[
            _build_select_result(snap),
            MagicMock(),   # UPDATE call
        ])
        result = await apply_preset(session, "my_settings")
        assert result is True

    @pytest.mark.asyncio
    async def test_conservative_applies_correct_rates(self) -> None:
        """apply_preset('conservative') calls update_assumptions with conservative values."""
        session = AsyncMock()
        await apply_preset(session, "conservative")
        # Should have called execute (for UPDATE)
        assert session.execute.called
        # Extract params from the UPDATE call
        call_args = session.execute.call_args
        params = call_args[0][1] if call_args[0] else call_args[1]
        assert params.get("ltr_vacancy") == 0.10
        assert params.get("active_preset") == "conservative"


def _build_select_result(snapshot: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: snapshot
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    return result
