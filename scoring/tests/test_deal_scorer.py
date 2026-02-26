"""Tests for deal_scorer.py"""

from __future__ import annotations
from datetime import datetime, timezone
import pytest
from scoring.deal_scorer import score_deal


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TestScoreDeal:
    def test_total_is_sum_of_components(self) -> None:
        ds = score_deal(
            conservative_monthly_cash_flow=800,
            dscr=1.3,
            cash_on_cash=0.06,
            year_built=1990,
            flood_high_risk=False,
            purchase_price=300_000,
            raw_confidence=0.85,
            scraped_at=_now(),
            comps_count=6,
            address="100 Oak St",
            num_units=2,
            strategy_validated=True,
        )
        expected = ds.return_score.score + ds.risk_score.score + ds.confidence_score.score
        assert ds.deal_score == min(100, expected)

    def test_deal_score_capped_at_100(self) -> None:
        ds = score_deal(
            conservative_monthly_cash_flow=2000,
            dscr=2.0,
            cash_on_cash=0.20,
            year_built=2010,
            flood_high_risk=False,
            purchase_price=300_000,
            raw_confidence=1.0,
            scraped_at=_now(),
            comps_count=10,
            address="100 Main St",
            num_units=4,
            strategy_validated=True,
        )
        assert ds.deal_score <= 100

    def test_high_priority_alert_all_conditions_met(self) -> None:
        ds = score_deal(
            conservative_monthly_cash_flow=600,
            dscr=1.25,
            cash_on_cash=0.07,
            year_built=2005,
            flood_high_risk=False,
            purchase_price=300_000,
            raw_confidence=0.90,
            scraped_at=_now(),
            comps_count=6,
            address="100 Oak St",
            num_units=2,
            strategy_validated=True,
        )
        assert ds.is_high_priority is True
        assert ds.alert_reasons == []

    def test_high_priority_false_low_confidence(self) -> None:
        ds = score_deal(
            conservative_monthly_cash_flow=600,
            dscr=1.25,
            cash_on_cash=0.07,
            year_built=2005,
            flood_high_risk=False,
            purchase_price=300_000,
            raw_confidence=0.30,   # low confidence → conf score < 20
            scraped_at=_now(),
            comps_count=0,
            address="100 Oak St",
            num_units=2,
            strategy_validated=True,
        )
        assert ds.is_high_priority is False
        assert any("Confidence Score" in r for r in ds.alert_reasons)

    def test_high_priority_false_no_comps(self) -> None:
        ds = score_deal(
            conservative_monthly_cash_flow=600,
            dscr=1.25,
            cash_on_cash=0.07,
            year_built=2005,
            flood_high_risk=False,
            purchase_price=300_000,
            raw_confidence=0.95,
            scraped_at=_now(),
            comps_count=6,
            address="100 Oak St",
            num_units=2,
            strategy_validated=False,  # no validated strategy
        )
        assert ds.is_high_priority is False

    def test_why_scored_high_is_string(self) -> None:
        ds = score_deal(800, 1.3, 0.06, 1990, False, 300_000)
        assert isinstance(ds.why_scored_high, str)
        assert len(ds.why_scored_high) > 0

    def test_disqualified_return_score_gives_low_deal_score(self) -> None:
        ds = score_deal(
            conservative_monthly_cash_flow=300,  # below $500
            dscr=1.3,
            cash_on_cash=0.06,
            year_built=1990,
            flood_high_risk=False,
            purchase_price=300_000,
        )
        assert ds.return_score.disqualified is True
        assert ds.deal_score < 40

    def test_scored_at_is_set(self) -> None:
        ds = score_deal(800, 1.3, 0.06, 1990, False, 300_000)
        assert ds.scored_at is not None


class TestRanker:
    """Basic ranker integration tests."""

    def test_import_ranker(self) -> None:
        from scoring.ranker import rank_deals, RankedDeals
        assert rank_deals is not None

    def test_high_score_goes_to_alerts(self) -> None:
        from scoring.ranker import rank_deals
        ds = score_deal(
            conservative_monthly_cash_flow=800,
            dscr=1.3,
            cash_on_cash=0.08,
            year_built=2005,
            flood_high_risk=False,
            purchase_price=300_000,
            raw_confidence=0.95,
            scraped_at=_now(),
            comps_count=6,
            address="100 Oak St",
            num_units=2,
            strategy_validated=True,
        )
        # Force high deal score for bucket test
        object.__setattr__(ds, "deal_score", 90)
        ranked = rank_deals([("prop_a", ds)])
        assert len(ranked.alerts) == 1

    def test_rejected_bucket_for_failed_gate(self) -> None:
        from scoring.ranker import rank_deals
        ds = score_deal(800, 1.3, 0.08, 2005, False, 300_000)
        ranked = rank_deals([("prop_b", ds)], failed_gate_ids={"prop_b"})
        assert len(ranked.rejected) == 1

    def test_total_counts_all_buckets(self) -> None:
        from scoring.ranker import rank_deals
        ds_good = score_deal(800, 1.3, 0.08, 2005, False, 300_000,
                             raw_confidence=0.9, scraped_at=_now(), comps_count=6,
                             address="a", num_units=2, strategy_validated=True)
        ds_bad = score_deal(300, 1.1, 0.01, 1920, True, 300_000)
        ranked = rank_deals([("a", ds_good), ("b", ds_bad)])
        assert ranked.total == 2
