"""
neighborhood/flood_zone.py

FEMA NFHL flood zone lookup using the free FEMA Map Service Center API.
No API key required.

Hard gate: property fails if flood zone is AE or VE (or any high-risk zone).
Pass zones: X (unshaded), X500 (shaded X), B, C, D, and any zone starting with X.
"""

from __future__ import annotations

from typing import TypedDict

import httpx
import structlog

logger = structlog.get_logger(__name__)

# FEMA NFHL REST endpoint — returns flood zone for a point
_FEMA_URL = (
    "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"
)

# Zones that trigger the hard gate failure
FAIL_ZONES: frozenset[str] = frozenset({"AE", "VE", "AO", "AH", "A", "V", "AR", "A99"})


class FloodZoneResult(TypedDict):
    flood_zone:      str    # e.g. "X", "AE", "VE", "UNKNOWN"
    is_high_risk:    bool   # True = hard gate FAIL
    sfha:            bool   # Special Flood Hazard Area designation
    panel_number:    str    # FIRM panel number (may be empty)


def get_flood_zone(lat: float, lon: float) -> FloodZoneResult:
    """
    Query the FEMA NFHL API for the flood zone at the given coordinates.

    Args:
        lat: Property latitude.
        lon: Property longitude.

    Returns:
        FloodZoneResult with zone designation and risk flag.
        Returns zone="UNKNOWN", is_high_risk=False if the query fails
        (conservative — we don't block on missing FEMA data alone).
    """
    params = {
        "geometry":     f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR":         4326,
        "spatialRel":   "esriSpatialRelIntersects",
        "outFields":    "FLD_ZONE,SFHA_TF,FIRM_PAN",
        "returnGeometry": "false",
        "f":            "json",
    }

    try:
        logger.info("flood_zone_request", lat=lat, lon=lon)
        response = httpx.get(_FEMA_URL, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning("flood_zone_api_error", error=str(exc), lat=lat, lon=lon)
        return FloodZoneResult(flood_zone="UNKNOWN", is_high_risk=False, sfha=False, panel_number="")

    features = data.get("features", [])
    if not features:
        logger.info("flood_zone_no_features", lat=lat, lon=lon)
        return FloodZoneResult(flood_zone="UNKNOWN", is_high_risk=False, sfha=False, panel_number="")

    attrs      = features[0].get("attributes", {})
    zone       = str(attrs.get("FLD_ZONE") or "UNKNOWN").strip().upper()
    sfha_raw   = str(attrs.get("SFHA_TF") or "").strip().upper()
    panel      = str(attrs.get("FIRM_PAN") or "").strip()

    # FEMA returns "T" for true, "F" for false on SFHA_TF
    sfha = sfha_raw in ("T", "TRUE", "1", "YES")

    is_high_risk = zone in FAIL_ZONES or (sfha and not zone.startswith("X"))

    result = FloodZoneResult(
        flood_zone   = zone,
        is_high_risk = is_high_risk,
        sfha         = sfha,
        panel_number = panel,
    )
    logger.info("flood_zone_result", zone=zone, high_risk=is_high_risk)
    return result
