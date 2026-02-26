"""
normalization/tests/test_deduplication.py

Tests for the deduplication module.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from normalization.deduplication import (
    deduplicate_property_records,
    deduplicate_rental_comps,
)
from normalization.schema import (
    DataSource,
    PropertyRecord,
    PropertyType,
    RentalComp,
    RentalStrategy,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_record(
    canonical_id: str = "aaa111bbb222",
    confidence: float = 0.8,
    beds: int | None = 4,
    lat: float | None = 30.32,
    lon: float | None = -81.66,
) -> PropertyRecord:
    return PropertyRecord(
        canonical_id   = canonical_id,
        source         = DataSource.ZILLOW_SALE,
        source_id      = canonical_id,
        scraped_at     = datetime.now(tz=timezone.utc),
        address        = "100 Test St",
        zip_code       = "32204",
        price          = 300_000.0,
        confidence_score = confidence,
        beds           = beds,
        lat            = lat,
        lon            = lon,
        property_type  = PropertyType.DUPLEX,
        num_units      = 2,
    )


def _make_comp(
    canonical_id: str = "comp111",
    monthly_rate: float | None = 1_500.0,
) -> RentalComp:
    return RentalComp(
        canonical_id   = canonical_id,
        source         = DataSource.ZILLOW_RENTAL,
        source_id      = canonical_id,
        scraped_at     = datetime.now(tz=timezone.utc),
        address        = "100 Test St",
        zip_code       = "32204",
        rental_strategy = RentalStrategy.LTR,
        monthly_rate   = monthly_rate,
    )


# ── PropertyRecord dedup ──────────────────────────────────────────────────────

class TestDeduplicatePropertyRecords:
    def test_single_record_unchanged(self) -> None:
        rec = _make_record()
        result = deduplicate_property_records([rec])
        assert len(result) == 1

    def test_empty_list(self) -> None:
        assert deduplicate_property_records([]) == []

    def test_same_canonical_id_keeps_highest_confidence(self) -> None:
        high = _make_record(confidence=0.9)
        low  = _make_record(confidence=0.5)
        result = deduplicate_property_records([high, low])
        assert len(result) == 1
        assert result[0].confidence_score == pytest.approx(0.9)

    def test_merge_fills_missing_fields(self) -> None:
        # high confidence but missing beds; low confidence has beds
        high = _make_record(confidence=0.9, beds=None)
        low  = _make_record(confidence=0.5, beds=4)
        result = deduplicate_property_records([high, low])
        assert len(result) == 1
        assert result[0].beds == 4

    def test_different_canonical_ids_both_kept(self) -> None:
        rec_a = _make_record(canonical_id="aaa111", lat=30.32, lon=-81.66)
        rec_b = _make_record(canonical_id="bbb222", lat=30.50, lon=-81.40)
        result = deduplicate_property_records([rec_a, rec_b])
        assert len(result) == 2

    def test_coord_dedup_merges_nearby_records(self) -> None:
        # Two records with different canonical_ids but within 30m of each other
        rec_a = _make_record(canonical_id="aaa111", lat=30.32000, lon=-81.66000, confidence=0.9)
        rec_b = _make_record(canonical_id="bbb222", lat=30.32001, lon=-81.66001, confidence=0.5)
        result = deduplicate_property_records([rec_a, rec_b])
        assert len(result) == 1
        assert result[0].confidence_score == pytest.approx(0.9)

    def test_coord_dedup_keeps_distant_records(self) -> None:
        # 1 km apart — should NOT be merged
        rec_a = _make_record(canonical_id="aaa111", lat=30.32, lon=-81.66)
        rec_b = _make_record(canonical_id="bbb222", lat=30.33, lon=-81.67)
        result = deduplicate_property_records([rec_a, rec_b])
        assert len(result) == 2

    def test_records_without_coords_not_coord_deduped(self) -> None:
        rec_a = _make_record(canonical_id="aaa111", lat=None, lon=None)
        rec_b = _make_record(canonical_id="bbb222", lat=None, lon=None)
        result = deduplicate_property_records([rec_a, rec_b])
        assert len(result) == 2


# ── RentalComp dedup ──────────────────────────────────────────────────────────

class TestDeduplicateRentalComps:
    def test_single_comp_unchanged(self) -> None:
        comp = _make_comp()
        result = deduplicate_rental_comps([comp])
        assert len(result) == 1

    def test_empty_list(self) -> None:
        assert deduplicate_rental_comps([]) == []

    def test_same_id_keeps_rated_comp(self) -> None:
        with_rate    = _make_comp(canonical_id="dup111", monthly_rate=1_800.0)
        without_rate = _make_comp(canonical_id="dup111", monthly_rate=None)
        result = deduplicate_rental_comps([without_rate, with_rate])
        assert len(result) == 1
        assert result[0].monthly_rate == pytest.approx(1_800.0)

    def test_different_ids_both_kept(self) -> None:
        comp_a = _make_comp(canonical_id="aaa", monthly_rate=1_500.0)
        comp_b = _make_comp(canonical_id="bbb", monthly_rate=1_700.0)
        result = deduplicate_rental_comps([comp_a, comp_b])
        assert len(result) == 2

    def test_merge_fills_missing_rate(self) -> None:
        primary   = _make_comp(canonical_id="sameId", monthly_rate=None)
        secondary = _make_comp(canonical_id="sameId", monthly_rate=2_000.0)
        result = deduplicate_rental_comps([primary, secondary])
        assert len(result) == 1
        assert result[0].monthly_rate == pytest.approx(2_000.0)
