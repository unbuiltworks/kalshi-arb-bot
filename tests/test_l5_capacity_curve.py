"""The capacity curve must not answer the axis it cannot see.

Two failure modes it is written against. The first is drawing a c>=2 profit
line from a tape that never recorded what a second slot would have traded --
the frozen concurrency gate already called that UNVERIFIABLE and this module
must not quietly re-answer it. The second is drift: the curve restates
constants measured elsewhere, and a constant copied is a constant that can
go stale, so a test recomputes them from source.
"""
from __future__ import annotations

import os

import pytest

from src import l5_capacity_curve as cc

_SRC = open(cc.__file__).read()
_have_cohort = os.path.exists("analysis/l5/l5_r5f_cohort.json")


# ------------------------------------------------------------------- drift
@pytest.mark.skipif(not _have_cohort, reason="cohort not present")
def test_the_hardcoded_per_leg_economics_still_match_their_source():
    """A constant copied from another module can go stale silently."""
    from src import l5_breakeven as be
    rep = be.report()
    assert float(rep["pair_profit_per_matched_pair_usd"]) == pytest.approx(
        cc.PAIR_PROFIT_PER_MATCHED_PAIR, abs=1e-6)
    assert float(rep["residual_loss_per_residual_unit_usd"]) == pytest.approx(
        cc.RESIDUAL_LOSS_PER_RESIDUAL_UNIT, abs=1e-6)
    assert rep["breakeven"]["observed"] == pytest.approx(
        cc.OBSERVED_COMPLETION_RATE, abs=1e-6)
    assert float(rep["opened_legs"]) == pytest.approx(cc.OPENED_LEGS, abs=1e-6)
    assert float(rep["confirmed_credits"]["rebate_per_opened_leg_usd"]) == (
        pytest.approx(cc.CONFIRMED_REBATE_PER_LEG, abs=1e-6))


@pytest.mark.skipif(not _have_cohort, reason="cohort not present")
def test_the_breakeven_here_matches_the_breakeven_module():
    from src import l5_breakeven as be
    assert cc.breakeven_completion_rate() == pytest.approx(
        be.report()["breakeven"]["including_confirmed_credits"], abs=1e-5)


# ------------------------------------------------------------------- shape
def test_the_curve_reproduces_the_measured_rate_at_the_observed_completion():
    """Its only available validation: it must land on what was measured."""
    got = cc.usd_per_dollar_hour(cc.OBSERVED_COMPLETION_RATE)
    assert got == pytest.approx(-0.0721, abs=0.005)
    assert got < 0


def test_the_curve_crosses_zero_at_the_breakeven_rate():
    be = cc.breakeven_completion_rate()
    # breakeven_completion_rate rounds to 6 dp, so the crossing is exact to
    # about a millionth of a dollar-hour, not to machine precision
    assert cc.usd_per_dollar_hour(be) == pytest.approx(0.0, abs=1e-5)
    assert cc.usd_per_dollar_hour(be - 0.01) < 0
    assert cc.usd_per_dollar_hour(be + 0.01) > 0


def test_the_curve_is_monotone_in_completion_rate():
    xs = [cc.usd_per_day(p) for p in (0.75, 0.80, 0.85, 0.90, 0.95)]
    assert all(b > a for a, b in zip(xs, xs[1:]))


def test_the_half_pair_profit_sensitivity_is_strictly_worse_everywhere():
    half = cc.PAIR_PROFIT_PER_MATCHED_PAIR / 2.0
    for row in cc.completion_curve():
        assert (row["usd_per_day_if_marginal_pair_earns_half"]
                < row["usd_per_day"])


def test_the_sensitivity_moves_breakeven_materially_higher():
    """If the marginal pair earns half, completion alone stops being enough."""
    half = cc.breakeven_completion_rate(
        pair_profit=cc.PAIR_PROFIT_PER_MATCHED_PAIR / 2.0)
    assert half > cc.breakeven_completion_rate() + 0.05


def test_the_observed_point_is_marked_as_observed_and_is_unique():
    marks = [r for r in cc.completion_curve() if r["is_observed"]]
    assert len(marks) == 1
    assert marks[0]["completion_rate"] == pytest.approx(
        cc.OBSERVED_COMPLETION_RATE)


# ------------------------------------------------------------- the refusals
def test_concurrency_pnl_is_refused_not_estimated():
    cp = cc.report()["concurrency_pnl"]
    assert cp["verdict"] == "UNVERIFIABLE"
    assert cp["not_computed_here"] is True
    assert "never recorded" in cp["why"]
    # and no arithmetic anywhere produces a c>=2 dollar figure
    for banned in ("concurrency_2", "c2_usd", "arm_c2", "slots *"):
        assert banned not in _SRC, banned


def test_supply_is_reported_as_supply_and_flagged_flat_cycles_only():
    s = cc.supply_axis({0: 10, 1: 5, 2: 3, 3: 2})
    assert s["flat_selection_cycles"] == 20
    assert s["cycles_with_at_least"]["2"] == 5
    assert s["share_with_at_least"]["2"] == 0.25
    assert "not a P&L one" in s["reading"]
    assert "not proof of supply on held ones" in s[
        "measured_on_flat_cycles_only"]


def test_supply_with_no_cycles_returns_null_not_zero_percent():
    assert cc.supply_axis({})["cycles"] == 0


def test_bankroll_is_reported_as_not_binding():
    b = cc.bankroll_axis()
    assert b["mean_utilisation"] < 0.05
    assert "DOES NOT BIND" in b["verdict"]
    assert "asking for the wrong resource" in b["verdict"]


def test_it_proposes_no_rung():
    r = cc.report()
    assert "without the owner" in r["read_only"]
    assert "Nothing here proposes a rung" in r["read_only"]


def test_it_reads_and_never_acts():
    for forbidden in ("requests", "httpx", "place_order", "post(",
                      "subprocess", "os.system"):
        assert forbidden not in _SRC, forbidden
    assert _SRC.count('"w") as f') == 2


def test_it_does_not_touch_the_frozen_completion_experiment():
    for frozen in ("pmus_spring_l5_session", "l5_completion_extension_gate",
                   "l5_completion_extension_eval"):
        assert frozen not in _SRC, frozen
