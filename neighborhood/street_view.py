"""
neighborhood/street_view.py

Google Maps URL generator for Street View and satellite imagery.

No live API calls — generates signed-URL strings only.
GOOGLE_MAPS_API_KEY is used to produce the Static Maps / Street View image URL.
If the key is absent, URLs fall back to keyless (limited quota) forms.
"""

from __future__ import annotations

import os
from typing import TypedDict
from urllib.parse import urlencode


class StreetViewURLs(TypedDict):
    street_view_image_url: str   # Static Street View image (640x400)
    satellite_view_url:    str   # Google Maps satellite embed link
    google_maps_link:      str   # Standard Google Maps link for address


def get_street_view_urls(
    lat: float,
    lon: float,
    address: str = "",
    api_key: str | None = None,
    width: int = 640,
    height: int = 400,
) -> StreetViewURLs:
    """
    Build Google Maps Street View image URL, satellite embed, and maps link.

    Args:
        lat:     Property latitude.
        lon:     Property longitude.
        address: Full street address (used for Google Maps link).
        api_key: Google Maps API key (falls back to GOOGLE_MAPS_API_KEY env var).
        width:   Street View image width in pixels (default 640).
        height:  Street View image height in pixels (default 400).

    Returns:
        StreetViewURLs with three URL strings.
    """
    key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")

    # ── Street View Static Image ──────────────────────────────────────────────
    sv_params: dict = {
        "size":     f"{width}x{height}",
        "location": f"{lat},{lon}",
        "fov":      90,
        "heading":  0,
        "pitch":    10,
        "source":   "outdoor",
    }
    if key:
        sv_params["key"] = key
    sv_url = "https://maps.googleapis.com/maps/api/streetview?" + urlencode(sv_params)

    # ── Satellite embed (Google Maps embed API) ───────────────────────────────
    sat_params: dict = {
        "q":      f"{lat},{lon}",
        "zoom":   18,
        "maptype": "satellite",
    }
    if key:
        sat_params["key"] = key
    sat_url = "https://www.google.com/maps/embed/v1/view?" + urlencode(sat_params)

    # ── Standard Google Maps link ─────────────────────────────────────────────
    if address:
        query = address
    else:
        query = f"{lat},{lon}"
    maps_link = f"https://www.google.com/maps/search/?api=1&query={urlencode({'q': query}).split('=', 1)[1]}"

    return StreetViewURLs(
        street_view_image_url = sv_url,
        satellite_view_url    = sat_url,
        google_maps_link      = maps_link,
    )
