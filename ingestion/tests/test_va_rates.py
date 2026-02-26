"""
Tests for ingestion/va_rates.py

Covers:
- Successful CFPB fetch parses rate correctly
- Cache returns cached value without re-fetching
- force_refresh bypasses cache
- Graceful fallback to hardcoded default on network failure
- Stale flag logic
- clear_cache() resets module state
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from ingestion.va_rates import (
    VARate,
    clear_cache,
    fetch_va_rate,
    get_cached_rate,
    _FALLBACK_RATE,
    _STALE_HOURS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

# Mock CFPB JSON response — most recent entry last
CFPB_JSON = json_bytes = (
    '{"data": ['
    '{"date": "2025-01-02", "rate": 6.91},'
    '{"date": "2025-01-09", "rate": 6.85},'
    '{"date": "2025-01-16", "rate": null},'
    '{"date": "2025-01-23", "rate": 6.78}'
    ']}'
).encode("utf-8")

# Fallback rate is now 6.75%
_EXPECTED_FALLBACK = 0.0675


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the module-level cache before every test."""
    clear_cache()
    yield
    clear_cache()


def _mock_resp(body: bytes) -> MagicMock:
    m = MagicMock()
    m.read.return_value = body
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCfpbFetch:
    def test_successful_fetch_returns_decimal_rate(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp(CFPB_JSON)):
            result = fetch_va_rate()

        # Last valid entry is 2025-01-23 → 6.78% → 0.0678
        assert result.rate == pytest.approx(0.0678, rel=1e-4)
        assert result.source == "cfpb"
        assert result.is_fallback is False
        assert result.is_stale is False

    def test_skips_null_rate_entries(self):
        # Last entry has null rate — should fall back to prior valid entry
        json_trailing_null = (
            '{"data": ['
            '{"date": "2025-01-09", "rate": 6.85},'
            '{"date": "2025-01-16", "rate": null}'
            ']}'
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_mock_resp(json_trailing_null)):
            result = fetch_va_rate()

        assert result.rate == pytest.approx(0.0685, rel=1e-4)

    def test_rate_as_string_is_parsed(self):
        json_str_rate = (
            '{"data": [{"date": "2025-01-23", "rate": "6.78"}]}'
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_mock_resp(json_str_rate)):
            result = fetch_va_rate()

        assert result.rate == pytest.approx(0.0678, rel=1e-4)

    def test_top_level_list_accepted(self):
        json_list = (
            '[{"date": "2025-01-23", "rate": 6.78}]'
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_mock_resp(json_list)):
            result = fetch_va_rate()

        assert result.rate == pytest.approx(0.0678, rel=1e-4)

    def test_network_error_returns_fallback(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = fetch_va_rate()

        assert result.source == "fallback"
        assert result.is_fallback is True
        assert result.rate == pytest.approx(_FALLBACK_RATE, rel=1e-6)

    def test_fallback_rate_is_6_75_pct(self):
        assert _FALLBACK_RATE == pytest.approx(0.0675, rel=1e-6)


class TestCache:
    def test_second_call_returns_cache_without_network(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp(CFPB_JSON)) as mock_url:
            fetch_va_rate()           # first call: hits network
            result2 = fetch_va_rate() # second call: uses cache

        assert mock_url.call_count == 1  # only one network call
        assert result2.source == "cache"
        assert result2.rate == pytest.approx(0.0678, rel=1e-4)

    def test_force_refresh_bypasses_cache(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp(CFPB_JSON)) as mock_url:
            fetch_va_rate()                       # first call
            fetch_va_rate(force_refresh=True)     # bypasses cache

        assert mock_url.call_count == 2

    def test_clear_cache_resets_state(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp(CFPB_JSON)):
            fetch_va_rate()

        clear_cache()
        assert get_cached_rate() == pytest.approx(_FALLBACK_RATE, rel=1e-6)

    def test_get_cached_rate_returns_fallback_before_any_fetch(self):
        assert get_cached_rate() == pytest.approx(_FALLBACK_RATE, rel=1e-6)

    def test_get_cached_rate_returns_fetched_rate_after_fetch(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp(CFPB_JSON)):
            fetch_va_rate()

        assert get_cached_rate() == pytest.approx(0.0678, rel=1e-4)


class TestStaleness:
    def test_fresh_rate_not_stale(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp(CFPB_JSON)):
            result = fetch_va_rate()

        assert result.is_stale is False

    def test_cache_stale_after_threshold(self):
        import ingestion.va_rates as va_module

        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=_STALE_HOURS + 1)
        va_module._cache = VARate(
            rate=0.0675,
            fetched_at=old_time,
            source="cfpb",
            is_fallback=False,
            is_stale=False,
        )

        result = fetch_va_rate()
        assert result.is_stale is True

    def test_cache_not_stale_within_threshold(self):
        import ingestion.va_rates as va_module

        recent_time = datetime.now(tz=timezone.utc) - timedelta(hours=_STALE_HOURS - 1)
        va_module._cache = VARate(
            rate=0.072,
            fetched_at=recent_time,
            source="cfpb",
            is_fallback=False,
            is_stale=False,
        )

        result = fetch_va_rate()
        assert result.is_stale is False


class TestFallbackWithExistingCache:
    def test_uses_old_cache_rate_when_cfpb_fails(self):
        import ingestion.va_rates as va_module

        cached_rate = 0.0695
        va_module._cache = VARate(
            rate=cached_rate,
            fetched_at=datetime.now(tz=timezone.utc),
            source="cfpb",
            is_fallback=False,
            is_stale=False,
        )

        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            result = fetch_va_rate(force_refresh=True)

        assert result.rate == pytest.approx(cached_rate, rel=1e-6)
        assert result.source == "cache"
        assert result.is_fallback is False


class TestVARateDataclass:
    def test_varate_fields(self):
        now = datetime.now(tz=timezone.utc)
        va = VARate(rate=0.07, fetched_at=now, source="cfpb", is_fallback=False, is_stale=False)
        assert va.rate == 0.07
        assert va.source == "cfpb"
        assert va.is_fallback is False
        assert va.is_stale is False
