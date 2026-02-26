"""Tests for return_score.py"""

from __future__ import annotations
import pytest
from scoring.return_score import compute_return_score


class TestReturnScore:
    def test_disqualified_low_dscr(self) -> None:
        r = compute_return_score(800, dscr=1.1, cash_on_cash=0.05)
        assert r.score == 0
        assert r.disqualified is True
        assert "1.2" in r.reason

    def test_disqualified_low_cf(self) -> None:
        r = compute_return_score(400, dscr=1.3, cash_on_cash=0.05)
        assert r.score == 0
        assert r.disqualified is True

    def test_cf_1500_gives_40_base(self) -> None:
        r = compute_return_score(1500, dscr=1.2, cash_on_cash=0.0)
        assert r.base_pts == 40

    def test_cf_1000_gives_32_base(self) -> None:
        r = compute_return_score(1000, dscr=1.2, cash_on_cash=0.0)
        assert r.base_pts == 32

    def test_cf_750_gives_26_base(self) -> None:
        r = compute_return_score(750, dscr=1.2, cash_on_cash=0.0)
        assert r.base_pts == 26

    def test_cf_500_gives_18_base(self) -> None:
        r = compute_return_score(500, dscr=1.2, cash_on_cash=0.0)
        assert r.base_pts == 18

    def test_dscr_ge_14_adds_5(self) -> None:
        r = compute_return_score(1000, dscr=1.5, cash_on_cash=0.0)
        assert r.dscr_pts == 5

    def test_dscr_ge_12_adds_2(self) -> None:
        r = compute_return_score(1000, dscr=1.2, cash_on_cash=0.0)
        assert r.dscr_pts == 2

    def test_coc_ge_8_adds_3(self) -> None:
        r = compute_return_score(1000, dscr=1.2, cash_on_cash=0.08)
        assert r.coc_pts == 3

    def test_coc_below_8_no_bonus(self) -> None:
        r = compute_return_score(1000, dscr=1.2, cash_on_cash=0.05)
        assert r.coc_pts == 0

    def test_score_capped_at_40(self) -> None:
        r = compute_return_score(2000, dscr=2.0, cash_on_cash=0.20)
        assert r.score <= 40

    def test_perfect_score(self) -> None:
        r = compute_return_score(1500, dscr=1.5, cash_on_cash=0.10)
        assert r.score == 40
        assert r.disqualified is False

    def test_adu_bonus_adds_3_pts(self) -> None:
        r_no_adu  = compute_return_score(1000, dscr=1.2, cash_on_cash=0.0, is_adu=False)
        r_with_adu = compute_return_score(1000, dscr=1.2, cash_on_cash=0.0, is_adu=True)
        assert r_with_adu.adu_bonus == 3
        assert r_no_adu.adu_bonus == 0
        assert r_with_adu.score == r_no_adu.score + 3

    def test_adu_bonus_does_not_exceed_cap(self) -> None:
        # Max base (40) + DSCR(5) + CoC(3) + ADU(3) would be 51 but cap is 40
        r = compute_return_score(1500, dscr=1.5, cash_on_cash=0.10, is_adu=True)
        assert r.score == 40

    def test_adu_bonus_zero_when_not_adu(self) -> None:
        r = compute_return_score(800, dscr=1.3, cash_on_cash=0.0, is_adu=False)
        assert r.adu_bonus == 0

    def test_adu_bonus_reason_mentioned(self) -> None:
        r = compute_return_score(1000, dscr=1.2, cash_on_cash=0.0, is_adu=True)
        assert "ADU" in r.reason


class TestReturnScoreReason:
    def test_reason_contains_cash_flow(self) -> None:
        r = compute_return_score(800, 1.3, 0.06)
        assert "$800" in r.reason

    def test_reason_contains_dscr(self) -> None:
        r = compute_return_score(800, 1.3, 0.06)
        assert "1.30" in r.reason
