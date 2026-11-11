"""The signal-gated replay must not change the paired controller.

No network and no corpus dependency: every window here is built in the test.
"""
from __future__ import annotations

import pytest

from analysis.l5.btc15m_signal_replay import (DIRECTIONAL_CAP,
                                              UNDERDOG_MAX_PRICE, SignalBook,
                                              _simulate, max_drawdown,
                                              paired_delta, summarize)
from analysis.l5.favorite_accumulation import (CASH_CAP, ENTRY_CUTOFF_S,
                                               EXPOSURE_CAP, policy_paired)

WS = 1788000000.0


def _t(off, outcome, px, winner="Up", slug="btc-updown-15m-1788000000"):
    return {"slug": slug, "ws": WS, "t_off": float(off), "side": "BUY",
            "outcome": outcome, "px": float(px), "size": 1.0,
            "winner": winner}


def _book(points):
    return SignalBook({"btc-updown-15m-1788000000":
                       [(WS + off, p) for off, p in points]})


# ------------------------------------------------- A is preserved exactly
WINDOWS = [
    [_t(30, "Up", 0.45), _t(90, "Down", 0.48), _t(150, "Up", 0.47)],
    [_t(10, "Down", 0.30), _t(60, "Down", 0.28), _t(120, "Up", 0.60),
     _t(300, "Down", 0.35), _t(700, "Up", 0.50)],
    [_t(5, "Up", 0.20, winner="Down"), _t(400, "Up", 0.22, winner="Down"),
     _t(500, "Down", 0.70, winner="Down")],
    [_t(i * 40, "Up" if i % 2 else "Down", 0.40 + 0.01 * i) for i in range(12)],
    [_t(900, "Up", 0.10), _t(1000, "Down", 0.10)],       # all past the cutoffs
]


@pytest.mark.parametrize("ts", WINDOWS)
@pytest.mark.parametrize("part", [1.0, 0.5, 0.25])
def test_signal_off_reproduces_the_deployed_paired_controller(ts, part):
    a = policy_paired(ts, participation=part)
    b = _simulate(ts, signal=None, participation=part)
    for k in ("pairs", "residual", "spend", "actions", "realized",
              "if_other_outcome", "worst_case"):
        assert b[k] == pytest.approx(a[k]), k
    assert b["directional"] == 0


@pytest.mark.parametrize("ts", WINDOWS)
def test_an_unreachable_threshold_also_reproduces_a(ts):
    book = _book([(off, 0.5) for off in range(60, 840, 60)])
    a = policy_paired(ts)
    b = _simulate(ts, signal=book, threshold=1.0)
    assert b["realized"] == pytest.approx(a["realized"])
    assert b["directional"] == 0


def test_a_flat_half_probability_is_not_a_no_op_so_threshold_is_not_a_skill_gate():
    """The threshold alone gates nothing, and this pins why.

    An underdog is priced below $0.50 by construction, so a completely
    uninformative p_up = 0.5 already clears `threshold` on every underdog
    cheaper than 0.50 - threshold - margin. The model therefore only earns
    its keep through DEPARTURES from 0.5, and the margin is the only
    parameter that makes the gate require actual confidence.
    """
    ts = _directional_window()                    # a $0.30 DOWN underdog
    flat = _book([(60, 0.5), (90, 0.5)])
    assert _simulate(ts, signal=flat, threshold=0.05)["directional"] == 1
    # 0.5 - 0.30 = 0.20, so a margin above 0.15 is what finally refuses it
    assert _simulate(ts, signal=flat, threshold=0.05,
                     margin=0.16)["directional"] == 0


# ------------------------------------------------------- the added branch
def _directional_window():
    """Two same-side cheap legs: the second breaches the paired residual cap."""
    return [_t(30, "Down", 0.30, winner="Down"),
            _t(90, "Down", 0.30, winner="Down")]


def test_the_signal_can_add_exactly_one_bounded_directional_leg():
    ts = _directional_window()
    assert policy_paired(ts)["actions"] == 1
    book = _book([(60, 0.05), (90, 0.05)])        # DOWN is near-certain
    b = _simulate(ts, signal=book, threshold=0.05)
    assert b["directional"] == 1
    assert b["actions"] == 2


def test_the_directional_cap_is_respected():
    ts = [_t(30 + 30 * i, "Down", 0.30, winner="Down") for i in range(6)]
    book = _book([(60 + 30 * i, 0.02) for i in range(12)])
    b = _simulate(ts, signal=book, threshold=0.05, directional_cap=1)
    assert b["directional"] == 1
    b2 = _simulate(ts, signal=book, threshold=0.05, directional_cap=2)
    assert b2["directional"] == 2


def test_the_favourite_side_is_never_a_selective_underdog():
    ts = [_t(30, "Up", 0.70), _t(90, "Up", 0.70)]
    book = _book([(60, 0.99), (90, 0.99)])        # UP near-certain and dear
    b = _simulate(ts, signal=book, threshold=0.05)
    assert b["directional"] == 0
    assert ts[0]["px"] > UNDERDOG_MAX_PRICE


def test_a_missing_signal_means_no_action_not_a_default_probability():
    ts = _directional_window()
    b = _simulate(ts, signal=SignalBook({}), threshold=0.05)
    assert b["directional"] == 0


def test_the_signal_is_causal_only_earlier_points_are_visible():
    book = _book([(600, 0.01)])                   # fires only from t+600
    ts = _directional_window()                    # both legs before t+600
    assert _simulate(ts, signal=book, threshold=0.05)["directional"] == 0


def test_the_margin_subtracts_before_the_threshold():
    ts = _directional_window()
    book = _book([(60, 0.30), (90, 0.30)])        # p(Down) = 0.70, px = 0.30
    assert _simulate(ts, signal=book, threshold=0.05,
                     margin=0.0)["directional"] == 1
    assert _simulate(ts, signal=book, threshold=0.05,
                     margin=0.40)["directional"] == 0


def test_the_entry_cutoff_still_binds_on_a_directional_leg():
    ts = [_t(30, "Down", 0.30, winner="Down"),
          _t(ENTRY_CUTOFF_S + 60, "Down", 0.30, winner="Down")]
    book = _book([(60, 0.01), (ENTRY_CUTOFF_S + 60, 0.01)])
    assert _simulate(ts, signal=book, threshold=0.05)["directional"] == 0


def test_the_cash_cap_still_binds_on_a_directional_leg():
    ts = _directional_window()
    book = _book([(60, 0.01), (90, 0.01)])
    b = _simulate(ts, signal=book, threshold=0.05)
    assert b["spend"] <= CASH_CAP
    assert max(b["pairs"], b["residual"]) <= EXPOSURE_CAP


# ------------------------------------------------------------- statistics
def test_max_drawdown_is_peak_to_trough_and_never_positive():
    assert max_drawdown([1.0, 1.0, -3.0, 1.0]) == pytest.approx(-3.0)
    assert max_drawdown([1.0, 2.0]) == 0.0
    assert max_drawdown([]) == 0.0
    assert max_drawdown([-1.0, -1.0]) == pytest.approx(-2.0)


def test_paired_delta_pairs_windows_and_counts_direction():
    a = [(1.0, {"realized": 0.0}), (2.0, {"realized": 1.0})]
    b = [(1.0, {"realized": 0.5}), (2.0, {"realized": 0.0})]
    d = paired_delta(a, b)
    assert d["windows"] == 2
    assert d["total"] == pytest.approx(-0.5)
    assert (d["better"], d["worse"], d["unchanged"]) == (1, 1, 0)


def test_paired_delta_ignores_windows_absent_from_either_side():
    a = [(1.0, {"realized": 0.0}), (2.0, {"realized": 1.0})]
    b = [(1.0, {"realized": 0.5})]
    assert paired_delta(a, b)["windows"] == 1


def test_summarize_reports_the_residual_lose_rate():
    rows = [(1.0, {"realized": -1.0, "residual": 2.0, "residual_won": 0.0,
                   "residual_lost": 2.0, "pairs": 0, "actions": 2,
                   "directional": 0, "directional_rows": []}),
            (2.0, {"realized": 1.0, "residual": 2.0, "residual_won": 2.0,
                   "residual_lost": 0.0, "pairs": 0, "actions": 2,
                   "directional": 0, "directional_rows": []})]
    s = summarize(rows)
    assert s["residual_lose_rate"] == pytest.approx(0.5)
    assert s["losing_windows"] == 1
    assert s["net_total"] == pytest.approx(0.0)


def test_summarize_does_not_credit_a_matched_directional_leg_to_net():
    # the standalone figure is explicitly counterfactual, so it must not
    # equal net when the leg was matched
    rows = [(1.0, {"realized": 0.04, "residual": 0.0, "residual_won": 0.0,
                   "residual_lost": 0.0, "pairs": 1, "actions": 2,
                   "directional": 1,
                   "directional_rows": [{"slug": "s", "outcome": "Down",
                                         "px": 0.3, "off": 30.0,
                                         "won": True}]})]
    s = summarize(rows)
    assert s["directional_standalone_net_if_unmatched"] == pytest.approx(0.7)
    assert s["net_total"] == pytest.approx(0.04)


def test_default_directional_cap_is_one_contract():
    assert DIRECTIONAL_CAP == 1
