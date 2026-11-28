"""Probability-aware inventory management: the rescue arithmetic and its gates.

No network, no corpus dependency: every window and signal is built here.
"""
from __future__ import annotations

import pytest

from analysis.l5.btc15m_inventory_policy import (EXTENDED_COMPLETE_CUTOFF_S,
                                                 empirical_rescue_threshold,
                                                 fixed_threshold_rescue,
                                                 probability_threshold_rescue,
                                                 summarize)
from analysis.l5.btc15m_signal_replay import SignalBook, _simulate
from analysis.l5.favorite_accumulation import (COMPLETE_CUTOFF_S, PAIR_CEILING,
                                               policy_paired)

WS = 1788000000.0


def _t(off, outcome, px, winner="Up"):
    return {"slug": "btc-updown-15m-1788000000", "ws": WS, "t_off": float(off),
            "side": "BUY", "outcome": outcome, "px": float(px), "size": 1.0,
            "winner": winner}


def _book(points):
    return SignalBook({"btc-updown-15m-1788000000":
                       [(WS + off, p) for off, p in points]})


# ------------------------------------------- defaults preserve the baseline
WINDOWS = [
    [_t(30, "Up", 0.45), _t(90, "Down", 0.48), _t(150, "Up", 0.47)],
    [_t(10, "Down", 0.30), _t(60, "Down", 0.28), _t(120, "Up", 0.60)],
    [_t(20, "Up", 0.40, winner="Down"), _t(500, "Down", 0.70, winner="Down")],
    [_t(40, "Up", 0.50), _t(700, "Down", 0.80)],
]


@pytest.mark.parametrize("ts", WINDOWS)
def test_rescue_off_and_default_cutoff_reproduce_the_deployed_controller(ts):
    a = policy_paired(ts)
    b = _simulate(ts, signal=None, rescue=False,
                  complete_cutoff_s=COMPLETE_CUTOFF_S, fee_per_contract=0.0)
    for k in ("pairs", "residual", "spend", "actions", "realized"):
        assert b[k] == pytest.approx(a[k]), k
    assert b["rescued"] == 0 and b["fees"] == 0.0


# ------------------------------------------------------- the rescue branch
def _stranded():
    """UP at 0.50 then a DOWN print at 0.70: pair 1.20, above the ceiling."""
    return [_t(30, "Up", 0.50, winner="Down"),
            _t(120, "Down", 0.70, winner="Down")]


def test_without_rescue_the_ceiling_strands_the_leg():
    r = _simulate(_stranded(), signal=None, rescue=False)
    assert r["pairs"] == 0 and r["residual"] == 1 and r["rescued"] == 0
    assert 0.50 + 0.70 > PAIR_CEILING


def test_rescue_completes_the_stranded_leg():
    r = _simulate(_stranded(), signal=None, rescue=True)
    assert r["pairs"] == 1 and r["residual"] == 0 and r["rescued"] == 1


def test_rescue_never_pays_a_dollar_or_more():
    ts = [_t(30, "Up", 0.50, winner="Down"), _t(120, "Down", 1.00, winner="Down")]
    assert _simulate(ts, signal=None, rescue=True)["rescued"] == 0


def test_fee_is_charged_per_contract_and_reduces_net():
    free = _simulate(_stranded(), signal=None, rescue=True, fee_per_contract=0.0)
    paid = _simulate(_stranded(), signal=None, rescue=True, fee_per_contract=0.01)
    assert paid["fees"] == pytest.approx(0.02)        # two contracts
    assert paid["realized"] == pytest.approx(free["realized"] - 0.02)


# --------------------------------------------- the EV arithmetic, pinned
def test_fixed_threshold_refuses_above_and_permits_below():
    allow = fixed_threshold_rescue(0.78)
    assert allow(px=0.77) is True
    assert allow(px=0.78) is False
    assert allow(px=0.90) is False


def test_a_threshold_rescue_refuses_the_expensive_completion():
    ts = [_t(30, "Up", 0.50, winner="Down"), _t(120, "Down", 0.90, winner="Down")]
    assert _simulate(ts, signal=None, rescue=True)["rescued"] == 1
    assert _simulate(ts, signal=None, rescue=True,
                     rescue_allow=fixed_threshold_rescue(0.78))["rescued"] == 0


def test_probability_priced_rescue_completes_only_when_escape_is_cheap():
    """Complete iff px < 1 - P(held side wins). Held side here is UP."""
    allow = probability_threshold_rescue(_book([(60, 0.30), (120, 0.30)]))
    # p_up = 0.30, we hold UP, so carrying is worth 0.30 and escaping must
    # cost under 0.70
    assert allow(slug="btc-updown-15m-1788000000", t_abs=WS + 120, px=0.65,
                 up=1.0, dn=0.0, side="Down") is True
    assert allow(slug="btc-updown-15m-1788000000", t_abs=WS + 120, px=0.75,
                 up=1.0, dn=0.0, side="Down") is False


def test_probability_priced_rescue_keeps_a_strong_residual():
    """A held side the model likes is worth carrying, not escaping."""
    allow = probability_threshold_rescue(_book([(60, 0.90), (120, 0.90)]))
    # p_up = 0.90 and we hold UP: escaping must cost under 0.10
    assert allow(slug="btc-updown-15m-1788000000", t_abs=WS + 120, px=0.40,
                 up=1.0, dn=0.0, side="Down") is False


def test_the_held_side_is_the_one_we_are_long_not_the_completing_leg():
    allow = probability_threshold_rescue(_book([(60, 0.90), (120, 0.90)]))
    # holding DOWN while p_up = 0.90 means P(held wins) = 0.10, so escaping
    # is worth almost any price under 0.90
    assert allow(slug="btc-updown-15m-1788000000", t_abs=WS + 120, px=0.80,
                 up=0.0, dn=1.0, side="Up") is True


def test_a_missing_probability_refuses_the_rescue():
    allow = probability_threshold_rescue(SignalBook({}))
    assert allow(slug="x", t_abs=WS, px=0.10, up=1.0, dn=0.0,
                 side="Down") is False


def test_probability_rescue_is_causal():
    allow = probability_threshold_rescue(_book([(600, 0.01)]))
    assert allow(slug="btc-updown-15m-1788000000", t_abs=WS + 120, px=0.10,
                 up=1.0, dn=0.0, side="Down") is False


# ------------------------------------------------------------- the cutoff
def test_the_extended_cutoff_only_reaches_risk_reducing_legs():
    late = [_t(30, "Up", 0.50, winner="Down"), _t(700, "Down", 0.40, winner="Down")]
    assert _simulate(late, signal=None,
                     complete_cutoff_s=COMPLETE_CUTOFF_S)["pairs"] == 0
    assert _simulate(late, signal=None,
                     complete_cutoff_s=EXTENDED_COMPLETE_CUTOFF_S)["pairs"] == 1


def test_the_extended_cutoff_does_not_permit_a_late_first_leg():
    # 700s is past the 480s entry cutoff, and nothing is held to reduce
    assert _simulate([_t(700, "Up", 0.40)], signal=None,
                     complete_cutoff_s=EXTENDED_COMPLETE_CUTOFF_S)["actions"] == 0


def test_extended_cutoff_is_longer_than_deployed_but_inside_the_window():
    assert COMPLETE_CUTOFF_S < EXTENDED_COMPLETE_CUTOFF_S < 900.0


# ----------------------------------------------------- empirical threshold
def test_empirical_threshold_is_one_minus_the_residual_win_rate():
    byw = {
        # carries a residual that WINS
        "btc-updown-15m-1788000000": [_t(30, "Up", 0.40, winner="Up")],
        # carries a residual that LOSES
        "btc-updown-15m-1788000900": [
            dict(_t(30, "Up", 0.40, winner="Down"), ws=WS + 900,
                 slug="btc-updown-15m-1788000900")],
    }
    thr = empirical_rescue_threshold(byw, sorted(byw))
    assert thr == pytest.approx(0.5)              # one win of two carried


def test_empirical_threshold_with_no_residual_refuses_everything():
    byw = {"btc-updown-15m-1788000000": [_t(30, "Up", 0.45),
                                         _t(90, "Down", 0.45)]}
    assert empirical_rescue_threshold(byw, sorted(byw)) == 1.0


# ------------------------------------------------------------- statistics
def test_summarize_reports_paired_fill_and_rescues():
    rows = [(1.0, {"realized": 0.04, "residual": 0.0, "residual_won": 0.0,
                   "residual_lost": 0.0, "pairs": 1, "actions": 2,
                   "rescued": 1, "fees": 0.02}),
            (2.0, {"realized": -0.45, "residual": 1.0, "residual_won": 0.0,
                   "residual_lost": 1.0, "pairs": 0, "actions": 1,
                   "rescued": 0, "fees": 0.01})]
    s = summarize(rows)
    assert s["paired_fill_rate"] == pytest.approx(0.5)
    assert s["rescued"] == 1
    assert s["residual_lose_rate"] == pytest.approx(1.0)
    assert s["fees"] == pytest.approx(0.03)
    assert s["net_total"] == pytest.approx(-0.41)
