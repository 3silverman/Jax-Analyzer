"""
neighborhood/street_view.py

Google Maps URL generator for Street View and satellite imagery.
Optionally scores street-level visual quality using the Anthropic API.

GOOGLE_MAPS_API_KEY — for Street View Static API image URLs.
ANTHROPIC_API_KEY   — for visual scoring (optional; disabled if absent).

Visual scoring dimensions (0–10 each, averaged → 0–10 total):
  1. Property condition   (maintenance, paint, roof visible)
  2. Street cleanliness   (litter, overgrowth, sidewalk quality)
  3. Neighbourhood character (parked cars, landscaping, neighbouring properties)
  4. Safety signals        (fencing, lighting, graffiti absence)
  5. Rentability appeal    (curb appeal, tenant-facing aesthetics)

Only run for properties passing hard gates AND deal_score >= 50 (caller responsibility).
"""

from __future__ import annotations

import os
from typing import TypedDict
from urllib.parse import urlencode

import structlog

logger = structlog.get_logger(__name__)


class StreetViewURLs(TypedDict):
    street_view_image_url: str   # Static Street View image (640x400)
    satellite_view_url:    str   # Google Maps satellite embed link
    google_maps_link:      str   # Standard Google Maps link for address


class VisualScore(TypedDict):
    score:           int      # 0–10 overall visual score
    property_cond:   int      # 0–10 property condition
    street_clean:    int      # 0–10 street cleanliness
    neighbourhood:   int      # 0–10 neighbourhood character
    safety:          int      # 0–10 safety signals
    rentability:     int      # 0–10 rentability appeal
    low_confidence:  bool     # True if image unavailable / Claude parse failed


_VISUAL_PROMPT = """You are a professional real-estate analyst scoring a Street View image of a property.
Score the following 5 dimensions on a scale of 0–10 (whole numbers only):

1. property_condition   — Visible maintenance level: paint, roof, windows, siding, foundation.
                          10 = pristine; 0 = severe disrepair.
2. street_cleanliness   — Litter, overgrowth, sidewalk condition, street surface quality.
                          10 = spotless; 0 = heavily littered/overgrown.
3. neighbourhood_char   — Neighbouring properties, landscaping, parked cars, overall feel.
                          10 = well-kept neighbourhood; 0 = declining/blighted.
4. safety_signals       — Adequate fencing/lighting, absence of graffiti, visible security.
                          10 = very safe-appearing; 0 = unsafe-appearing.
5. rentability_appeal   — Curb appeal for potential tenants / corporate housing.
                          10 = highly appealing; 0 = unappealing.

If the image is unavailable (grey placeholder or error), return all scores as -1.

Respond with ONLY a JSON object in this exact format (no markdown, no explanation):
{"property_condition":7,"street_cleanliness":6,"neighbourhood_char":7,"safety_signals":8,"rentability_appeal":7}"""


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

    # ── Satellite embed ───────────────────────────────────────────────────────
    sat_params: dict = {
        "q":       f"{lat},{lon}",
        "zoom":    18,
        "maptype": "satellite",
    }
    if key:
        sat_params["key"] = key
    sat_url = "https://www.google.com/maps/embed/v1/view?" + urlencode(sat_params)

    # ── Standard Google Maps link ─────────────────────────────────────────────
    query = address if address else f"{lat},{lon}"
    maps_link = (
        "https://www.google.com/maps/search/?api=1&query="
        + urlencode({"q": query}).split("=", 1)[1]
    )

    return StreetViewURLs(
        street_view_image_url = sv_url,
        satellite_view_url    = sat_url,
        google_maps_link      = maps_link,
    )


def score_street_view(
    image_url: str,
    anthropic_api_key: str | None = None,
) -> VisualScore:
    """
    Score street-level visual quality using Claude's vision capability.

    Only called for properties that have passed hard gates and scored >= 50.
    Falls back to score=5 (neutral) with low_confidence=True if:
      - No ANTHROPIC_API_KEY configured
      - Street View image fetch fails (grey placeholder = no image for address)
      - Claude response is unparseable

    Args:
        image_url:          Google Street View Static image URL.
        anthropic_api_key:  Anthropic key; falls back to ANTHROPIC_API_KEY env var.

    Returns:
        VisualScore TypedDict with 0–10 scores per dimension and an overall average.
    """
    key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return _default_visual_score(low_confidence=True)

    img_b64 = _fetch_image_b64(image_url)
    if img_b64 is None:
        logger.warning("street_view_image_fetch_failed", url=image_url[:80])
        return _default_visual_score(low_confidence=True)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": _VISUAL_PROMPT},
                ],
            }],
        )
        raw_text = msg.content[0].text.strip()
        vs = _parse_visual_response(raw_text)
        logger.info(
            "street_view_scored",
            score=vs["score"],
            low_confidence=vs["low_confidence"],
        )
        return vs

    except Exception as exc:
        logger.warning("street_view_claude_error", error=str(exc))
        return _default_visual_score(low_confidence=True)


def _fetch_image_b64(url: str) -> str | None:
    """Download a Street View image and return as base64. Returns None on error or placeholder."""
    import base64
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = resp.read()
        # Google returns a tiny placeholder image when no Street View exists (< 10KB)
        if len(data) < 8_000:
            return None
        return base64.b64encode(data).decode("ascii")
    except Exception as exc:
        logger.warning("street_view_fetch_error", error=str(exc))
        return None


def _parse_visual_response(text: str) -> VisualScore:
    """Parse JSON from Claude's visual scoring response."""
    import json
    try:
        text = text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(l for l in lines if not l.startswith("```"))
        data = json.loads(text)

        pc = int(data.get("property_condition", 5))
        sc = int(data.get("street_cleanliness", 5))
        nc = int(data.get("neighbourhood_char", 5))
        sf = int(data.get("safety_signals", 5))
        ra = int(data.get("rentability_appeal", 5))

        if any(v < 0 for v in (pc, sc, nc, sf, ra)):
            return _default_visual_score(low_confidence=True)

        pc, sc, nc, sf, ra = [max(0, min(10, v)) for v in (pc, sc, nc, sf, ra)]
        overall = round((pc + sc + nc + sf + ra) / 5)

        return VisualScore(
            score=overall,
            property_cond=pc,
            street_clean=sc,
            neighbourhood=nc,
            safety=sf,
            rentability=ra,
            low_confidence=False,
        )
    except Exception as exc:
        logger.warning("street_view_parse_error", error=str(exc), text=text[:100])
        return _default_visual_score(low_confidence=True)


def _default_visual_score(low_confidence: bool = False) -> VisualScore:
    return VisualScore(
        score=5,
        property_cond=5,
        street_clean=5,
        neighbourhood=5,
        safety=5,
        rentability=5,
        low_confidence=low_confidence,
    )
