"""The settled-window scoreboard must fail closed and must compute honestly."""
from decimal import Decimal as D

import pytest

from src.l5_settled_scoreboard import (
    WINDOW_S, WindowNotComplete, WindowNotSettled, assert_complete,
    assert_settled, scoreboard)


def _ev(start, n, step):
    return [(start + i * step, "book", {}) for i in range(n)]


def test_partial_window_is_refused():
    with pytest.raises(WindowNotComplete, match="before the window closes"):
        assert_complete(_ev(0.0, 301, 1.0), window_start=0.0)


def test_late_start_is_refused():
    ev = _ev(60.0, WINDOW_S, 1.0)
    with pytest.raises(WindowNotComplete, match="into the window"):
        assert_complete(ev, window_start=0.0)


def test_a_hole_in_the_middle_is_refused():
    ev = _ev(0.0, 400, 1.0) + _ev(900.0 - 100, 101, 1.0)
    with pytest.raises(WindowNotComplete, match="gap"):
        assert_complete(ev, window_start=0.0)


def test_a_complete_window_passes():
    cov = assert_complete(_ev(0.0, 901, 1.0), window_start=0.0)
    assert cov["coverage_min"] == pytest.approx(15.0)


def test_no_winner_is_refused_and_a_last_price_cannot_be_passed():
    for bad in (None, "", "last", "0.97"):
        with pytest.raises(WindowNotSettled):
            assert_settled(bad)
    assert assert_settled("up") == "up"


def _res(matched, matched_pnl, fills, **kw):
    base = dict(matched_qty=matched, matched_pnl=matched_pnl, resid_qty=D(0),
                resid_pnl=D(0), fees=D("0.10"), maker_fills=fills,
                taker_fills=0, n_fills=fills, peak_cash=D(5),
                worst_residual=D(0), credited_rebate=D(0),
                sourced_incentive=D("0.02"), net=D(1))
    base.update(kw)
    return base


def test_pair_cost_and_ratio_are_measured_against_the_control():
    ctl = _res(D(10), D("0.40"), 1)
    arm = _res(D(60), D("2.40"), 11)
    b = scoreboard(arm, control=ctl)
    assert b["pair_cost"] == (D(60) - D("2.40")) / D(60)
    assert b["extra_fills_vs_control"] == 10
    assert b["extra_matched_vs_control"] == D(50)
    assert b["matched_per_extra_fill"] == D(5)


def test_stranded_extra_fills_show_a_zero_ratio_not_a_flattering_one():
    """More fills that never pair must read as 0 matched per extra fill."""
    ctl = _res(D(10), D("0.40"), 1)
    arm = _res(D(10), D("0.40"), 11, resid_qty=D(100))
    b = scoreboard(arm, control=ctl)
    assert b["matched_per_extra_fill"] == D(0)
    assert b["unmatched_residual"] == D(100)


def test_no_extra_fills_gives_undefined_not_zero():
    """A no-op arm must not be reported as a stranding arm."""
    ctl = _res(D(10), D("0.40"), 1)
    b = scoreboard(_res(D(10), D("0.40"), 1), control=ctl)
    assert b["matched_per_extra_fill"] is None


def test_modeled_rebate_is_never_reported_as_confirmed():
    b = scoreboard(_res(D(10), D("0.40"), 1, sourced_incentive=D("9.99")))
    assert b["confirmed_incentives"] == D(0)
    assert b["sourced_incentives"] == D("9.99")
