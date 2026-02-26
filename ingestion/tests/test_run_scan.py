"""
ingestion/tests/test_run_scan.py

Unit tests for the ScanResult dataclass and run_scan orchestrator.
Tests mock out actual Apify API calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ingestion.apify_client import ApifyError
from ingestion.run_scan import ScanResult, run_scan
from normalization.schema import DataSource, PropertyRecord, PropertyType, RentalComp, RentalStrategy


# ── ScanResult ────────────────────────────────────────────────────────────────

def _make_record() -> PropertyRecord:
    return PropertyRecord(
        canonical_id = "abc123def456",
        source       = DataSource.ZILLOW_SALE,
        source_id    = "zpid_1",
        scraped_at   = datetime.now(tz=timezone.utc),
        address      = "100 Oak St",
        zip_code     = "32204",
        price        = 300_000.0,
    )


def _make_comp(strategy: RentalStrategy = RentalStrategy.LTR) -> RentalComp:
    return RentalComp(
        canonical_id    = "comp123",
        source          = DataSource.ZILLOW_RENTAL,
        source_id       = "c1",
        scraped_at      = datetime.now(tz=timezone.utc),
        address         = "200 Maple Ave",
        zip_code        = "32204",
        rental_strategy = strategy,
        monthly_rate    = 1_500.0,
    )


class TestScanResult:
    def test_total_listings(self) -> None:
        sr = ScanResult(listings=[_make_record(), _make_record()])
        assert sr.total_listings == 2

    def test_total_comps(self) -> None:
        ltr = [_make_comp(RentalStrategy.LTR)] * 3
        mtr = [_make_comp(RentalStrategy.MTR)] * 2
        sr = ScanResult(ltr_comps=ltr, mtr_comps=mtr)
        assert sr.total_comps == 5

    def test_defaults_empty(self) -> None:
        sr = ScanResult()
        assert sr.total_listings == 0
        assert sr.total_comps == 0
        assert sr.errors == []


# ── run_scan ──────────────────────────────────────────────────────────────────

class TestRunScan:
    @patch("ingestion.run_scan.fetch_mtr_comps")
    @patch("ingestion.run_scan.fetch_rental_comps")
    @patch("ingestion.run_scan.fetch_listings")
    @patch("ingestion.run_scan.ApifyClient")
    def test_successful_scan(
        self,
        mock_client_cls,
        mock_fetch_listings,
        mock_fetch_rentals,
        mock_fetch_mtr,
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__  = MagicMock(return_value=False)

        mock_fetch_listings.return_value = [_make_record()]
        mock_fetch_rentals.return_value  = [_make_comp(RentalStrategy.LTR)]
        mock_fetch_mtr.return_value      = [_make_comp(RentalStrategy.MTR)]

        result = run_scan()

        assert result.total_listings == 1
        assert len(result.ltr_comps)  == 1
        assert len(result.mtr_comps)  == 1
        assert result.errors          == []

    @patch("ingestion.run_scan.fetch_mtr_comps", side_effect=RuntimeError("FF down"))
    @patch("ingestion.run_scan.fetch_rental_comps", return_value=[])
    @patch("ingestion.run_scan.fetch_listings", return_value=[])
    @patch("ingestion.run_scan.ApifyClient")
    def test_partial_failure_recorded(
        self,
        mock_client_cls,
        mock_fetch_listings,
        mock_fetch_rentals,
        mock_fetch_mtr,
    ) -> None:
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_client_cls.return_value.__exit__  = MagicMock(return_value=False)

        result = run_scan()

        assert len(result.errors) == 1
        assert "furnished_finder" in result.errors[0]

    @patch("ingestion.run_scan.ApifyClient", side_effect=ApifyError("APIFY_TOKEN is not set in the environment."))
    def test_missing_apify_token_returns_error(self, mock_client_cls) -> None:
        result = run_scan()
        assert len(result.errors) == 1
        assert result.total_listings == 0
