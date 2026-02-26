"""
ingestion/airdna_client.py

AirDNA Rentalizer API client for STR comp validation.

Uses the free Rentalizer endpoint to estimate short-term rental revenue
for a given lat/lon. When AirDNA data is available, sets strategy_validated=True
for STR in the pipeline.

Environment variable:
    AIRDNA_API_KEY — required; if absent, all calls return None (STR not validated).

AirDNA free Rentalizer endpoint:
    https://api.airdna.co/client/v2/rentalizer/estimate
    GET params: access_token, address (or latitude+longitude), bedrooms
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_RENTALIZER_URL = "https://api.airdna.co/client/v2/rentalizer/estimate"


@dataclass
class AirDNAEstimate:
    monthly_revenue: float        # estimated gross monthly STR revenue
    occupancy_rate: float         # decimal (e.g. 0.72 = 72%)
    adr: float                    # average daily rate ($)
    bedrooms: int
    source: str = "airdna"        # always "airdna" for real responses
    raw: dict | None = None


def _get_api_key() -> str | None:
    return os.environ.get("AIRDNA_API_KEY", "").strip() or None


def get_str_estimate(
    lat: float,
    lon: float,
    bedrooms: int = 2,
    api_key: str | None = None,
) -> AirDNAEstimate | None:
    """
    Fetch an STR revenue estimate from AirDNA Rentalizer.

    Args:
        lat:      Property latitude.
        lon:      Property longitude.
        bedrooms: Number of bedrooms in the unit (for per-unit estimate).
        api_key:  AirDNA API key; falls back to AIRDNA_API_KEY env var.

    Returns:
        AirDNAEstimate if data is available, None if no key or API error.
    """
    key = api_key or _get_api_key()
    if not key:
        return None  # AirDNA not configured — STR uses default assumptions

    params: dict[str, Any] = {
        "access_token": key,
        "latitude":     lat,
        "longitude":    lon,
        "bedrooms":     bedrooms,
        "currency":     "USD",
    }

    try:
        logger.info("airdna_request", lat=lat, lon=lon, bedrooms=bedrooms)
        resp = httpx.get(_RENTALIZER_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("airdna_http_error", status=exc.response.status_code,
                       lat=lat, lon=lon)
        return None
    except Exception as exc:
        logger.warning("airdna_request_error", error=str(exc), lat=lat, lon=lon)
        return None

    # Parse response — AirDNA v2 Rentalizer returns nested structure
    # Expected keys: property_stats, adr, occupancy, revenue
    try:
        stats = data.get("property_stats") or data
        revenue_mo = _extract_monthly_revenue(stats)
        occupancy  = _extract_occupancy(stats)
        adr        = _extract_adr(stats)

        if revenue_mo is None:
            logger.warning("airdna_parse_failed", lat=lat, lon=lon, data=str(data)[:200])
            return None

        estimate = AirDNAEstimate(
            monthly_revenue=revenue_mo,
            occupancy_rate=occupancy or 0.72,
            adr=adr or (revenue_mo / 30),
            bedrooms=bedrooms,
            raw=data,
        )
        logger.info(
            "airdna_estimate",
            lat=lat, lon=lon,
            monthly_revenue=revenue_mo,
            occupancy=estimate.occupancy_rate,
            adr=estimate.adr,
        )
        return estimate

    except Exception as exc:
        logger.warning("airdna_parse_error", error=str(exc), lat=lat, lon=lon)
        return None


def _extract_monthly_revenue(stats: dict) -> float | None:
    """Try common AirDNA v2 revenue keys."""
    # v2 Rentalizer nests under "revenue" or "annual_revenue"
    for key in ("revenue", "monthly_revenue", "annual_revenue"):
        val = stats.get(key)
        if val is not None:
            try:
                v = float(val)
                # If annual, convert to monthly
                if key == "annual_revenue" and v > 0:
                    return v / 12
                return v
            except (TypeError, ValueError):
                continue
    return None


def _extract_occupancy(stats: dict) -> float | None:
    for key in ("occupancy", "occupancy_rate", "avg_occupancy"):
        val = stats.get(key)
        if val is not None:
            try:
                v = float(val)
                return v / 100 if v > 1 else v   # handle both 72 and 0.72
            except (TypeError, ValueError):
                continue
    return None


def _extract_adr(stats: dict) -> float | None:
    for key in ("adr", "avg_daily_rate", "average_daily_rate"):
        val = stats.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def get_multi_unit_str_estimate(
    lat: float,
    lon: float,
    num_units: int,
    beds_per_unit: int,
    api_key: str | None = None,
) -> AirDNAEstimate | None:
    """
    Get a per-unit STR estimate and scale to the whole property.

    Args:
        lat, lon:       Property coordinates.
        num_units:      Number of rentable units.
        beds_per_unit:  Beds per unit (for per-unit estimate).

    Returns:
        AirDNAEstimate with monthly_revenue scaled to total property, or None.
    """
    est = get_str_estimate(lat, lon, bedrooms=beds_per_unit, api_key=api_key)
    if est is None:
        return None
    # Scale per-unit revenue to whole property
    return AirDNAEstimate(
        monthly_revenue=est.monthly_revenue * num_units,
        occupancy_rate=est.occupancy_rate,
        adr=est.adr,
        bedrooms=beds_per_unit,
        raw=est.raw,
    )
