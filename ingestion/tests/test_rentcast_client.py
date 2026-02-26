"""Tests for ingestion/rentcast_client.py.

Verifies: get_property() is removed, get_rent_estimate() signature is correct,
get_rent_comps() works as expected.  All HTTP calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from ingestion.rentcast_client import RentcastClient, RentcastError


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_client() -> RentcastClient:
    """Return a RentcastClient with a fake API key (no real HTTP)."""
    return RentcastClient(api_key="test_key_123")


def _mock_response(data: dict | list, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = (status < 400)
    resp.json.return_value = data
    resp.text = json.dumps(data)[:200]
    return resp


# ── Dead code removal — get_property() must not exist ─────────────────────────

class TestGetPropertyRemoved:
    def test_get_property_not_present(self) -> None:
        client = _make_client()
        assert not hasattr(client, "get_property"), (
            "get_property() was dead code and must be removed from RentcastClient"
        )


# ── get_rent_estimate ─────────────────────────────────────────────────────────

class TestGetRentEstimate:
    def test_minimal_call_address_and_zip(self) -> None:
        client = _make_client()
        mock_resp = _mock_response({"rent": 1500.0})
        client._http.get = MagicMock(return_value=mock_resp)

        result = client.get_rent_estimate("123 Main St", "32204")

        assert result == {"rent": 1500.0}
        call_params = client._http.get.call_args[1]["params"]
        assert call_params["address"] == "123 Main St"
        assert call_params["zipCode"] == "32204"

    def test_optional_params_included_when_provided(self) -> None:
        client = _make_client()
        mock_resp = _mock_response({"rent": 1800.0})
        client._http.get = MagicMock(return_value=mock_resp)

        client.get_rent_estimate(
            "456 Oak Ave", "32205",
            property_type="Multi Family",
            bedrooms=3,
            bathrooms=2.0,
        )

        call_params = client._http.get.call_args[1]["params"]
        assert call_params["propertyType"] == "Multi Family"
        assert call_params["bedrooms"] == 3
        assert call_params["bathrooms"] == pytest.approx(2.0)

    def test_optional_params_omitted_when_none(self) -> None:
        client = _make_client()
        mock_resp = _mock_response({"rent": 1200.0})
        client._http.get = MagicMock(return_value=mock_resp)

        client.get_rent_estimate("789 Pine St", "32206")

        call_params = client._http.get.call_args[1]["params"]
        assert "propertyType" not in call_params
        assert "bedrooms" not in call_params
        assert "bathrooms" not in call_params

    def test_returns_none_on_404(self) -> None:
        client = _make_client()
        mock_resp = _mock_response({}, status=404)
        client._http.get = MagicMock(return_value=mock_resp)
        assert client.get_rent_estimate("Missing St", "32207") is None

    def test_raises_on_non_2xx(self) -> None:
        client = _make_client()
        mock_resp = _mock_response({"error": "bad"}, status=500)
        client._http.get = MagicMock(return_value=mock_resp)
        with pytest.raises(RentcastError):
            client.get_rent_estimate("Bad St", "32204")


# ── get_rent_comps ────────────────────────────────────────────────────────────

class TestGetRentComps:
    def test_returns_list_when_data_key_present(self) -> None:
        client = _make_client()
        comps = [{"id": "1", "price": 1400}, {"id": "2", "price": 1500}]
        mock_resp = _mock_response({"data": comps})
        client._http.get = MagicMock(return_value=mock_resp)

        result = client.get_rent_comps(lat=30.32, lon=-81.65)

        assert len(result) == 2
        assert result[0]["id"] == "1"

    def test_returns_direct_list(self) -> None:
        client = _make_client()
        comps = [{"id": "3", "price": 1600}]
        mock_resp = _mock_response(comps)
        client._http.get = MagicMock(return_value=mock_resp)

        result = client.get_rent_comps(lat=30.32, lon=-81.65)

        assert len(result) == 1

    def test_returns_empty_on_404(self) -> None:
        client = _make_client()
        mock_resp = _mock_response({}, status=404)
        client._http.get = MagicMock(return_value=mock_resp)
        assert client.get_rent_comps(lat=30.32, lon=-81.65) == []

    def test_passes_lat_lon_params(self) -> None:
        client = _make_client()
        mock_resp = _mock_response([])
        client._http.get = MagicMock(return_value=mock_resp)

        client.get_rent_comps(lat=30.32, lon=-81.65, beds=2, radius_miles=0.75, limit=10)

        call_params = client._http.get.call_args[1]["params"]
        assert call_params["latitude"] == pytest.approx(30.32)
        assert call_params["longitude"] == pytest.approx(-81.65)
        assert call_params["bedrooms"] == 2
        assert call_params["radius"] == pytest.approx(0.75)
        assert call_params["limit"] == 10

    def test_property_type_hardcoded_multi_family(self) -> None:
        client = _make_client()
        mock_resp = _mock_response([])
        client._http.get = MagicMock(return_value=mock_resp)

        client.get_rent_comps(lat=30.0, lon=-81.0)

        call_params = client._http.get.call_args[1]["params"]
        assert call_params["propertyType"] == "Multi Family"

    def test_beds_omitted_when_none(self) -> None:
        client = _make_client()
        mock_resp = _mock_response([])
        client._http.get = MagicMock(return_value=mock_resp)

        client.get_rent_comps(lat=30.0, lon=-81.0, beds=None)

        call_params = client._http.get.call_args[1]["params"]
        assert "bedrooms" not in call_params


# ── Context manager ───────────────────────────────────────────────────────────

class TestContextManager:
    def test_enter_returns_self(self) -> None:
        client = _make_client()
        assert client.__enter__() is client

    def test_exit_closes_http(self) -> None:
        client = _make_client()
        client._http.close = MagicMock()
        client.__exit__(None, None, None)
        client._http.close.assert_called_once()


# ── Missing API key ───────────────────────────────────────────────────────────

class TestMissingApiKey:
    def test_raises_when_no_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("RENTCAST_API_KEY", None)
            with pytest.raises(RentcastError, match="RENTCAST_API_KEY"):
                RentcastClient(api_key="")
