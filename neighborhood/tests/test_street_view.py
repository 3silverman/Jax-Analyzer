"""Tests for street_view URL generator — no live API calls."""

from __future__ import annotations
import pytest
from neighborhood.street_view import get_street_view_urls


class TestGetStreetViewUrls:
    def setup_method(self) -> None:
        self.urls = get_street_view_urls(
            lat=30.32, lon=-81.66,
            address="100 Oak Ave, Jacksonville, FL 32204",
            api_key="TEST_KEY",
        )

    def test_street_view_url_contains_lat_lon(self) -> None:
        assert "30.32" in self.urls["street_view_image_url"]
        assert "-81.66" in self.urls["street_view_image_url"]

    def test_street_view_url_contains_api_key(self) -> None:
        assert "TEST_KEY" in self.urls["street_view_image_url"]

    def test_street_view_url_is_https(self) -> None:
        assert self.urls["street_view_image_url"].startswith("https://")

    def test_satellite_url_is_https(self) -> None:
        assert self.urls["satellite_view_url"].startswith("https://")

    def test_maps_link_is_https(self) -> None:
        assert self.urls["google_maps_link"].startswith("https://")

    def test_no_api_key_still_generates_url(self) -> None:
        urls = get_street_view_urls(lat=30.32, lon=-81.66, api_key="")
        assert "googleapis.com" in urls["street_view_image_url"]

    def test_default_size_640x400(self) -> None:
        assert "640x400" in self.urls["street_view_image_url"]

    def test_custom_size(self) -> None:
        urls = get_street_view_urls(30.32, -81.66, width=320, height=200)
        assert "320x200" in urls["street_view_image_url"]
