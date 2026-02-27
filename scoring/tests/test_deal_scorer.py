"""Tests for deal_scorer.py"""

from __future__ import annotations
from datetime import datetime, timezone
import pytest
from scoring.deal_scorer import StrategyMetrics, score_deal


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _sm(cash_flow: float, dscr: float = 1.3, coc: float = 0.06) -> StrategyMetrics:
    """Build a StrategyMetrics with the same values for all three slots in a test."""
    return StrategyMetrics(cash_flow=cash_flow, dscr=dscr, cash_on_cash=coc)


def _score(
    cash_flow: float = 800,
    dscr: float = 1.3,
    coc: float = 0.06,
    year_built: int = 1990,
    flood_high_risk: bool = False,
    purchase_price: float = 300_000,
    **kwargs,
):
    """Convenience wrapper: pass the same CF/DSCR/CoC for all three strategies."""
    sm = _sm(cash_flow, dscr, coc)
    return score_deal(ltr=sm, mtr=sm, str_=sm,
                      year_built=year_built, flood_high_risk=flood_high_risk,
                      purchase_price=purchase_price, **kwargs)


class TestScoreDeal:
    def test_total_is_sum_of_components(self) -> None:
        ds = _score(
            cash_flow=800, dscr=1.3, coc=0.06,
            year_built=1990, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=0.85, scraped_at=_now(), comps_count=6,
            address="100 Oak St", num_units=2, strategy_validated=True,
        )
        expected = ds.return_score.score + ds.risk_score.score + ds.confidence_score.score
        assert ds.deal_score == min(100, expected)

    def test_deal_score_capped_at_100(self) -> None:
        ds = _score(
            cash_flow=2000, dscr=2.0, coc=0.20,
            year_built=2010, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=1.0, scraped_at=_now(), comps_count=10,
            address="100 Main St", num_units=4, strategy_validated=True,
        )
        assert ds.deal_score <= 100

    def test_high_priority_alert_all_conditions_met(self) -> None:
        ds = _score(
            cash_flow=600, dscr=1.25, coc=0.07,
            year_built=2005, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=0.90, scraped_at=_now(), comps_count=6,
            address="100 Oak St", num_units=2, strategy_validated=True,
        )
        assert ds.is_high_priority is True
        assert ds.alert_reasons == []

    def test_high_priority_false_low_confidence(self) -> None:
        ds = _score(
            cash_flow=600, dscr=1.25, coc=0.07,
            year_built=2005, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=0.30,  # low confidence → conf score < 20
            scraped_at=_now(), comps_count=0,
            address="100 Oak St", num_units=2, strategy_validated=True,
        )
        assert ds.is_high_priority is False
        assert any("Confidence Score" in r for r in ds.alert_reasons)

    def test_high_priority_false_no_comps(self) -> None:
        ds = _score(
            cash_flow=600, dscr=1.25, coc=0.07,
            year_built=2005, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=0.95, scraped_at=_now(), comps_count=6,
            address="100 Oak St", num_units=2,
            strategy_validated=False,  # no validated strategy
        )
        assert ds.is_high_priority is False

    def test_why_scored_high_is_string(self) -> None:
        ds = _score(800, 1.3, 0.06, 1990, False, 300_000)
        assert isinstance(ds.why_scored_high, str)
        assert len(ds.why_scored_high) > 0

    def test_winning_strategy_is_labelled(self) -> None:
        # MTR has higher CF → should win
        ltr = StrategyMetrics(cash_flow=400, dscr=1.1, cash_on_cash=0.05)
        mtr = StrategyMetrics(cash_flow=700, dscr=1.4, cash_on_cash=0.10)
        str_ = StrategyMetrics(cash_flow=300, dscr=0.9, cash_on_cash=0.04)
        ds = score_deal(ltr=ltr, mtr=mtr, str_=str_,
                        year_built=2005, flood_high_risk=False, purchase_price=300_000)
        assert ds.winning_strategy == "MTR"

    def test_disqualified_return_score_gives_low_deal_score(self) -> None:
        ds = _score(
            cash_flow=300,  # all three strategies below $500 → disqualified
            dscr=1.3, coc=0.06,
            year_built=1990, flood_high_risk=False, purchase_price=300_000,
        )
        assert ds.return_score.disqualified is True
        assert ds.deal_score < 40

    def test_scored_at_is_set(self) -> None:
        ds = _score(800, 1.3, 0.06, 1990, False, 300_000)
        assert ds.scored_at is not None


class TestRanker:
    """Basic ranker integration tests."""

    def test_import_ranker(self) -> None:
        from scoring.ranker import rank_deals, RankedDeals
        assert rank_deals is not None

    def test_high_score_goes_to_alerts(self) -> None:
        from scoring.ranker import rank_deals
        ds = _score(
            cash_flow=800, dscr=1.3, coc=0.08,
            year_built=2005, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=0.95, scraped_at=_now(), comps_count=6,
            address="100 Oak St", num_units=2, strategy_validated=True,
        )
        # Force high deal score for bucket test
        object.__setattr__(ds, "deal_score", 90)
        ranked = rank_deals([("prop_a", ds)])
        assert len(ranked.alerts) == 1

    def test_rejected_bucket_for_failed_gate(self) -> None:
        from scoring.ranker import rank_deals
        ds = _score(800, 1.3, 0.08, 2005, False, 300_000)
        ranked = rank_deals([("prop_b", ds)], failed_gate_ids={"prop_b"})
        assert len(ranked.rejected) == 1

    def test_total_counts_all_buckets(self) -> None:
        from scoring.ranker import rank_deals
        ds_good = _score(800, 1.3, 0.08, 2005, False, 300_000,
                         raw_confidence=0.9, scraped_at=_now(), comps_count=6,
                         address="a", num_units=2, strategy_validated=True)
        ds_bad = _score(300, 1.1, 0.01, 1920, True, 300_000)
        ranked = rank_deals([("a", ds_good), ("b", ds_bad)])
        assert ranked.total == 2


class TestPropertyTypeScoring:
    """Tests for property_type-aware scoring (SFR/ADU)."""

    def test_sfr_adu_gets_adu_bonus_in_return_score(self) -> None:
        ds_mf  = _score(1000, 1.3, 0.06, 2005, False, 300_000, property_type="duplex")
        ds_adu = _score(1000, 1.3, 0.06, 2005, False, 300_000, property_type="sfr_adu")
        assert ds_adu.return_score.adu_bonus == 3
        assert ds_mf.return_score.adu_bonus == 0
        assert ds_adu.deal_score >= ds_mf.deal_score

    def test_sfr_gets_vacancy_deduction_in_risk_score(self) -> None:
        ds_mf  = _score(1000, 1.3, 0.06, 2010, False, 300_000, property_type="duplex")
        ds_sfr = _score(1000, 1.3, 0.06, 2010, False, 300_000, property_type="sfr")
        assert ds_sfr.risk_score.score == ds_mf.risk_score.score - 2

    def test_sfr_adu_no_vacancy_deduction_in_risk_score(self) -> None:
        ds_mf  = _score(1000, 1.3, 0.06, 2010, False, 300_000, property_type="duplex")
        ds_adu = _score(1000, 1.3, 0.06, 2010, False, 300_000, property_type="sfr_adu")
        assert ds_adu.risk_score.score == ds_mf.risk_score.score

    def test_none_property_type_no_sfr_penalty(self) -> None:
        ds_none = _score(1000, 1.3, 0.06, 2010, False, 300_000, property_type=None)
        ds_mf   = _score(1000, 1.3, 0.06, 2010, False, 300_000, property_type="duplex")
        assert ds_none.risk_score.score == ds_mf.risk_score.score


class TestConfidencePenalty:
    """confidence_penalty=True caps Confidence Score at 19, blocking high-priority alerts."""

    def test_confidence_penalty_caps_score_at_19(self) -> None:
        ds = _score(
            cash_flow=800, dscr=1.3, coc=0.06,
            year_built=2000, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=1.0, scraped_at=_now(), comps_count=10,
            address="100 Oak St", num_units=2, confidence_penalty=True,
        )
        assert ds.confidence_score.score <= 19
        assert ds.confidence_score.data_penalty is True

    def test_confidence_penalty_blocks_high_priority_alert(self) -> None:
        ds = _score(
            cash_flow=800, dscr=1.3, coc=0.08,
            year_built=2005, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=1.0, scraped_at=_now(), comps_count=10,
            address="100 Oak St", num_units=2,
            strategy_validated=True, confidence_penalty=True,
        )
        assert ds.is_high_priority is False
        assert any("Confidence Score" in r for r in ds.alert_reasons)

    def test_no_penalty_flag_allows_normal_confidence(self) -> None:
        ds = _score(
            cash_flow=800, dscr=1.3, coc=0.06,
            year_built=2000, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=1.0, scraped_at=_now(), comps_count=10,
            address="100 Oak St", num_units=2, confidence_penalty=False,
        )
        assert ds.confidence_score.score >= 20
        assert ds.confidence_score.data_penalty is False

    def test_penalty_only_caps_if_score_already_at_or_above_20(self) -> None:
        ds = _score(
            cash_flow=800, dscr=1.3, coc=0.06,
            year_built=2000, flood_high_risk=False, purchase_price=300_000,
            raw_confidence=0.20, scraped_at=_now(), comps_count=0,
            address="100 Oak St", num_units=2, confidence_penalty=True,
        )
        assert ds.confidence_score.data_penalty is False
