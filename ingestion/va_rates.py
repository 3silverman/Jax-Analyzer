"""
ingestion/va_rates.py

Fetches and caches the current VA 30-year fixed mortgage rate.

Primary source: CFPB Mortgage Trends API (no API key required)
  URL: https://www.consumerfinance.gov/api/trends/mortgage/30-year-fixed/
  Returns JSON: {"data": [{"date": "YYYY-MM-DD", "rate": 6.91}, ...]}
  Most recent entry is last in the array.

Fallback chain:
  1. CFPB Mortgage Trends API (free, no key)
  2. Last in-memory cached rate (from previous successful fetch this session)
  3. Hardcoded 6.75% — flagged as stale if > 48 h since last successful fetch

Note: VA rates typically run 0.25–0.50% below conventional 30-year fixed.
This module returns the PMMS rate as-is; the Assumptions UI lets users override.

Daily cache: one HTTP fetch per process restart. Call fetch_va_rate() once at
startup and store the result in the assumptions table.
"""

from __future__ import annotations

import io
import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_FALLBACK_RATE: float = 0.0675         # 6.75% — used when CFPB unreachable and no cache
_CFPB_URL = (
    "https://www.consumerfinance.gov/api/trends/mortgage/30-year-fixed/"
)
_STALE_HOURS: float = 48.0             # rate flagged stale after this many hours


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class VARate:
    rate:        float     # decimal (e.g. 0.0691 for 6.91%)
    fetched_at:  datetime  # UTC timestamp of last successful or fallback fetch
    source:      str       # "cfpb" | "cache" | "fallback"
    is_fallback: bool      # True if using hardcoded default (no live data ever)
    is_stale:    bool      # True if fetched_at > 48 h ago


# ── Module-level cache ────────────────────────────────────────────────────────

_cache: Optional[VARate] = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_cfpb_rate() -> float | None:
    """
    Fetch the latest 30-year fixed rate from the CFPB Mortgage Trends API.

    Expected response shape:
        {"data": [{"date": "YYYY-MM-DD", "rate": 6.91}, ...]}
    The most recent entry is last in the array. "rate" may be a float or string.

    Returns the rate as a decimal (e.g. 0.0691), or None on any failure.
    """
    try:
        req = urllib.request.Request(
            _CFPB_URL,
            headers={"User-Agent": "jax-analyzer/1.0 (VA loan real estate analysis)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")

        payload = json.loads(content)

        # Normalise: accept top-level list or {"data": [...]}
        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, dict):
            entries = payload.get("data") or payload.get("rates") or []
        else:
            return None

        # Walk backwards to find the last entry with a valid numeric rate
        for entry in reversed(entries):
            if not isinstance(entry, dict):
                continue
            raw = entry.get("rate") or entry.get("value")
            if raw is None:
                continue
            try:
                rate = float(raw)
                if rate > 0:
                    return rate / 100.0    # percent → decimal
            except (ValueError, TypeError):
                continue

        return None

    except Exception as exc:
        logger.warning("cfpb_fetch_error", error=str(exc))
        return None


def _is_stale(va_rate: VARate) -> bool:
    """Return True if the rate was fetched more than _STALE_HOURS ago."""
    now = datetime.now(tz=timezone.utc)
    fetched = va_rate.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (now - fetched).total_seconds() / 3600 > _STALE_HOURS


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_va_rate(force_refresh: bool = False) -> VARate:
    """
    Get the current VA 30-year fixed rate.

    Uses an in-memory daily cache to avoid redundant HTTP requests.
    Falls back to the last cached rate or hardcoded default if CFPB is unreachable.

    Args:
        force_refresh: Bypass cache and always attempt a fresh CFPB fetch.

    Returns:
        VARate dataclass with rate, source, and freshness metadata.
    """
    global _cache
    now = datetime.now(tz=timezone.utc)

    # Serve from cache if fresh enough and not forced
    if not force_refresh and _cache is not None:
        stale = _is_stale(_cache)
        return VARate(
            rate=_cache.rate,
            fetched_at=_cache.fetched_at,
            source="cache",
            is_fallback=_cache.is_fallback,
            is_stale=stale,
        )

    # Attempt CFPB fetch
    rate = _fetch_cfpb_rate()

    if rate is not None:
        logger.info("va_rate_fetched", rate_pct=f"{rate:.3%}", source="cfpb")
        result = VARate(
            rate=rate,
            fetched_at=now,
            source="cfpb",
            is_fallback=False,
            is_stale=False,
        )
    else:
        # Degrade to previous cache value, or hardcoded fallback
        if _cache is not None:
            logger.warning(
                "va_rate_cfpb_unavailable",
                using="cached",
                cached_rate_pct=f"{_cache.rate:.3%}",
            )
            result = VARate(
                rate=_cache.rate,
                fetched_at=_cache.fetched_at,
                source="cache",
                is_fallback=_cache.is_fallback,
                is_stale=_is_stale(_cache),
            )
        else:
            logger.warning(
                "va_rate_cfpb_unavailable",
                using="hardcoded_fallback",
                fallback_rate_pct=f"{_FALLBACK_RATE:.3%}",
            )
            result = VARate(
                rate=_FALLBACK_RATE,
                fetched_at=now,
                source="fallback",
                is_fallback=True,
                is_stale=False,   # brand-new, just set to now
            )

    _cache = result
    return result


def get_cached_rate() -> float:
    """
    Return the cached rate as a decimal without triggering a network call.
    Returns the hardcoded fallback (0.0675) if no cache exists yet.
    """
    return _cache.rate if _cache is not None else _FALLBACK_RATE


def clear_cache() -> None:
    """Clear the module-level cache. Primarily for use in tests."""
    global _cache
    _cache = None
