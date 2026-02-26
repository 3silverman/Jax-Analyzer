"""
neighborhood/walk_score.py

Walk Score API client.
Returns Walk Score, Transit Score, and Bike Score for a given address + lat/lon.

API docs: https://www.walkscore.com/professional/api.php
Env:      WALKSCORE_API_KEY
"""

from __future__ import annotations

import os
from typing import TypedDict

import httpx
import structlog

logger = structlog.get_logger(__name__)

_API_URL = "https://api.walkscore.com/score"


class WalkScoreResult(TypedDict):
    walk_score:    int | None   # 0–100
    transit_score: int | None   # 0–100  (None if not available)
    bike_score:    int | None   # 0–100  (None if not available)
    walk_description:    str
    transit_description: str
    ws_link: str                # canonical WalkScore permalink


def get_walk_score(
    address: str,
    lat: float,
    lon: float,
    api_key: str | None = None,
) -> WalkScoreResult:
    """
    Fetch walkability scores for a property.

    Args:
        address: Full street address string (used by the API for display).
        lat:     Property latitude.
        lon:     Property longitude.
        api_key: WalkScore API key (falls back to WALKSCORE_API_KEY env var).

    Returns:
        WalkScoreResult dict with walk/transit/bike scores.
        Scores are None if the API does not return them.

    Raises:
        httpx.HTTPStatusError: On non-2xx API response.
    """
    key = api_key or os.environ.get("WALKSCORE_API_KEY", "")
    if not key:
        raise ValueError("WALKSCORE_API_KEY is not set")

    params = {
        "format":  "json",
        "address": address,
        "lat":     lat,
        "lon":     lon,
        "wsapikey": key,
        "transit": 1,
        "bike":    1,
    }

    logger.info("walk_score_request", address=address)
    response = httpx.get(_API_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    walk  = data.get("walkscore")
    desc  = data.get("description", "")
    link  = data.get("ws_link", "")

    transit_data = data.get("transit") or {}
    bike_data    = data.get("bike")    or {}

    result: WalkScoreResult = {
        "walk_score":          int(walk)  if walk is not None else None,
        "transit_score":       int(transit_data.get("score", 0)) if transit_data.get("score") is not None else None,
        "bike_score":          int(bike_data.get("score",   0)) if bike_data.get("score")    is not None else None,
        "walk_description":    desc,
        "transit_description": transit_data.get("description", ""),
        "ws_link":             link,
    }
    logger.info("walk_score_result", walk=result["walk_score"], transit=result["transit_score"])
    return result
