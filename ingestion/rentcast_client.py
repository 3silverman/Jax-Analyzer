"""
ingestion/rentcast_client.py

HTTP wrapper around the Rentcast API.

Provides property detail lookups and rental-market comparables.
Does NOT normalize data — returns raw API response dicts.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_BASE_URL    = "https://api.rentcast.io/v1"
_DEFAULT_TIMEOUT = 30  # seconds


class RentcastError(Exception):
    """Raised on non-2xx Rentcast API responses."""


class RentcastClient:
    """
    Minimal Rentcast REST client.

    Usage::

        client = RentcastClient()          # reads RENTCAST_API_KEY from env
        prop   = client.get_property("123 Main St, Jacksonville, FL 32204")
        comps  = client.get_rent_comps(lat=30.32, lon=-81.65, beds=3)
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.environ.get("RENTCAST_API_KEY", "")
        if not self._key:
            raise RentcastError("RENTCAST_API_KEY is not set in the environment.")
        self._http = httpx.Client(
            timeout=_DEFAULT_TIMEOUT,
            headers={"X-Api-Key": self._key, "Accept": "application/json"},
        )

    # ── Internal helper ───────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{_BASE_URL}/{path.lstrip('/')}"
        response = self._http.get(url, params=params or {})
        if response.status_code == 404:
            return None          # property not found — caller handles
        if not response.is_success:
            raise RentcastError(
                f"Rentcast {path} returned {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_property(self, address: str, zip_code: str) -> dict[str, Any] | None:
        """
        Fetch property details by street address.

        Returns raw dict or None if not found.
        """
        logger.info("rentcast_get_property", address=address, zip_code=zip_code)
        params = {"address": address, "zipCode": zip_code}
        result = self._get("properties", params=params)
        if result is None:
            logger.warning("rentcast_property_not_found", address=address)
        return result

    def get_rent_estimate(self, address: str, zip_code: str) -> dict[str, Any] | None:
        """
        Fetch rent estimate for an address (LTR).

        Returns raw dict including rentEstimate, rentRange, comparables.
        """
        logger.info("rentcast_rent_estimate", address=address, zip_code=zip_code)
        params = {"address": address, "zipCode": zip_code}
        return self._get("avm/rent/long-term", params=params)

    def get_rent_comps(
        self,
        lat: float,
        lon: float,
        beds: int | None = None,
        radius_miles: float = 0.5,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """
        Fetch long-term rental comps near a lat/lon coordinate.

        Args:
            lat:          Latitude of the subject property.
            lon:          Longitude of the subject property.
            beds:         Filter by bedroom count (optional).
            radius_miles: Search radius in miles.
            limit:        Max comps to return.

        Returns:
            List of raw comp dicts (empty list if none found).
        """
        logger.info("rentcast_rent_comps", lat=lat, lon=lon, beds=beds)
        params: dict[str, Any] = {
            "latitude":    lat,
            "longitude":   lon,
            "radius":      radius_miles,
            "limit":       limit,
            "propertyType": "Multi Family",
        }
        if beds is not None:
            params["bedrooms"] = beds

        result = self._get("listings/rental/long-term", params=params)
        if result is None:
            return []
        # Rentcast returns {"data": [...]} or a direct list
        if isinstance(result, list):
            return result
        return result.get("data", [])

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "RentcastClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
