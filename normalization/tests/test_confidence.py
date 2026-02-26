"""
normalization/tests/test_confidence.py

Tests for the confidence scoring module.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from normalization.confidence import annotate, score_confidence
from normalization.schema import DataSource, PropertyRecord, PropertyType


def _make_record(**kwargs) -> PropertyRecord:
    defaults = dict(
        canonical_id  = "abc123def456",
        source        = DataSource.ZILLOW_SALE,
        source_id     = "zpid_999",
        scraped_at    = datetime.now(tz=timezone.utc),
        address       = "500 River Blvd",
        zip_code      = "32204",
        price         = 280_000.0,
        beds          = 4,
        baths         = 2.0,
        sqft          = 1_800.0,
        year_built    = 1975,
        lat           = 30.32,
        lon           = -81.66,
        property_type = PropertyType.DUPLEX,
        days_on_market = 14,
    )
    defaults.update(kwargs)
    return PropertyRecord(**defaults)


class TestScoreConfidence:
    def test_perfect_record_scores_near_one(self) -> None:
        rec = _make_record()
        score, missing = score_confidence(rec)
        # All optional fields present + fresh scrape → should be ≥ 0.95
        assert score >= 0.95
        assert missing == []

    def test_missing_beds_reduces_score(self) -> None:
        rec = _make_record(beds=None)
        score_with, _ = score_confidence(_make_record())
        score_without, missing = score_confidence(rec)
        assert score_without < score_with
        assert "beds" in missing

    def test_missing_sqft_reduces_score(self) -> None:
        rec = _make_record(sqft=None)
        _, missing = score_confidence(rec)
        assert "sqft" in missing

    def test_missing_lat_lon_reduces_score(self) -> None:
        rec = _make_record(lat=None, lon=None)
        _, missing = score_confidence(rec)
        assert "lat_lon" in missing

    def test_unknown_property_type_penalised(self) -> None:
        rec = _make_record(property_type=PropertyType.UNKNOWN)
        _, missing = score_confidence(rec)
        assert "property_type" in missing

    def test_stale_record_lower_score(self) -> None:
        fresh = _make_record(scraped_at=datetime.now(tz=timezone.utc))
        stale = _make_record(scraped_at=datetime.now(tz=timezone.utc) - timedelta(days=10))
        score_fresh, _ = score_confidence(fresh)
        score_stale, _ = score_confidence(stale)
        assert score_fresh > score_stale

    def test_implausible_price_penalised(self) -> None:
        # Price below 50k should trigger sanity penalty
        rec = _make_record(price=10_000.0)
        score_normal, _ = score_confidence(_make_record())
        score_bad, _    = score_confidence(rec)
        assert score_bad < score_normal

    def test_score_bounded_0_to_1(self) -> None:
        # Even with many missing fields, never below 0
        minimal = _make_record(
            beds=None, baths=None, sqft=None, year_built=None,
            lat=None, lon=None, days_on_market=None,
            property_type=PropertyType.UNKNOWN,
            scraped_at=datetime.now(tz=timezone.utc) - timedelta(days=30),
        )
        score, _ = score_confidence(minimal)
        assert 0.0 <= score <= 1.0

    def test_score_is_rounded_to_4_dp(self) -> None:
        rec = _make_record()
        score, _ = score_confidence(rec)
        assert score == round(score, 4)


class TestAnnotate:
    def test_annotate_populates_fields(self) -> None:
        rec = _make_record()
        annotated = annotate(rec)
        assert annotated.confidence_score > 0.0
        assert isinstance(annotated.missing_fields, list)

    def test_annotate_returns_copy(self) -> None:
        rec = _make_record()
        annotated = annotate(rec)
        # Original unchanged
        assert rec.confidence_score == 0.0

    def test_annotate_missing_fields_populated(self) -> None:
        rec = _make_record(beds=None, sqft=None)
        annotated = annotate(rec)
        assert "beds" in annotated.missing_fields
        assert "sqft" in annotated.missing_fields
