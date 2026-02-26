"""
ingestion/va_rates.py

Fetches and caches the current VA 30-year fixed mortgage rate.

Primary source: FRED (FreddieMac PMMS weekly 30-year fixed, no API key required)
  URL: https://fred.stlouisfed.org/graph/fredgraph.csv?id=MORTGAGE30US
  Returns CSV rows: date,value (e.g. "2025-01-02,6.91")

Fallback chain:
  1. FRED weekly PMMS data (free, no key)
  2. Last in-memory cached rate (from previous successful fetch this session)
  3. Hardcoded 7.50% — flagged as stale if > 48 h since last successful fetch

Note: VA rates typically run 0.25–0.50% below conventional 30-year fixed.
This module returns the PMMS rate as-is; the Assumptions UI lets users override.

Daily cache: one HTTP fetch per process restart. Call fetch_va_rate() once at
startup and store the result in the assumptions table.
"""

from __future__ import annotations

import csv
import io
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_FALLBACK_RATE: float = 0.075          # 7.50% — used when FRED unreachable and no cache
_FRED_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=MORTGAGE30US"
)
_STALE_HOURS: float = 48.0             # rate flagged stale after this many hours


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class VARate:
    rate:        float     # decimal (e.g. 0.0691 for 6.91%)
    fetched_at:  datetime  # UTC timestamp of last successful or fallback fetch
    source:      str       # "fred" | "cache" | "fallback"
    is_fallback: bool      # True if using hardcoded default (no live data ever)
    is_stale:    bool      # True if fetched_at > 48 h ago


# ── Module-level cache ────────────────────────────────────────────────────────

_cache: Optional[VARate] = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_fred_rate() -> float | None:
    """
    Fetch the latest weekly 30-year fixed rate from FRED CSV.

    Returns the rate as a decimal (e.g. 0.0691), or None on any failure.
    """
    try:
        req = urllib.request.Request(
            _FRED_URL,
            headers={"User-Agent": "jax-analyzer/1.0 (VA loan real estate analysis)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        # Walk backwards to find the last row with a valid numeric value
        for row in reversed(rows):
            if len(row) == 2 and row[1].strip() and row[1].strip() != ".":
                try:
                    return float(row[1]) / 100.0   # percent → decimal
                except ValueError:
                    continue
        return None

    except Exception as exc:
        logger.warning("fred_fetch_error", error=str(exc))
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
    Falls back to the last cached rate or hardcoded default if FRED is unreachable.

    Args:
        force_refresh: Bypass cache and always attempt a fresh FRED fetch.

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

    # Attempt FRED fetch
    rate = _fetch_fred_rate()

    if rate is not None:
        logger.info("va_rate_fetched", rate_pct=f"{rate:.3%}", source="fred")
        result = VARate(
            rate=rate,
            fetched_at=now,
            source="fred",
            is_fallback=False,
            is_stale=False,
        )
    else:
        # Degrade to previous cache value, or hardcoded fallback
        if _cache is not None:
            logger.warning(
                "va_rate_fred_unavailable",
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
                "va_rate_fred_unavailable",
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
    Returns the hardcoded fallback (0.075) if no cache exists yet.
    """
    return _cache.rate if _cache is not None else _FALLBACK_RATE


def clear_cache() -> None:
    """Clear the module-level cache. Primarily for use in tests."""
    global _cache
    _cache = None
