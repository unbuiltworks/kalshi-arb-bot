"""Regressions for the habitat selector.

The selector's whole claim is that it uses ONLY information available before
trading. These tests exist to make that claim mechanical rather than a promise:
a feature window that reaches past the decision instant, or a frozen artifact
that quietly drifts, would both be caught here.
"""
import json
import sys

import pytest

sys.path.insert(0, "/home/user/kalshi-arb-bot")
sys.path.insert(0, "/home/user/kalshi-arb-bot/analysis/pmus")

import habitat_selector as HS

FROZEN = "/home/user/kalshi-arb-bot/analysis/pmus/habitat_selector_frozen.json"


def _book(n=40, t0=0.0, step=30.0, bid=0.48, ask=0.52):
    return [{"t": t0 + i * step, "bid": bid, "ask": ask,
             "bid_qty": 100.0, "ask_qty": 100.0} for i in range(n)]


def test_feature_window_never_reaches_past_the_decision_instant():
    """A print AFTER t must not change any feature computed at t."""
    se = _book()
    bt = [o["t"] for o in se]
    base = [{"t": 100.0 + i, "px": 0.48, "size": 10.0} for i in range(8)]
    t = 700.0
    a = HS.features(se, bt, base, t)
    poisoned = base + [{"t": t + 1.0, "px": 0.52, "size": 1e6}]
    b = HS.features(se, bt, poisoned, t)
    assert a == b


def test_target_window_starts_at_the_decision_instant():
    """A print BEFORE t must not enter the target."""
    se = _book()
    bt = [o["t"] for o in se]
    fwd = [{"t": 700.0 + i, "px": 0.48 if i % 2 else 0.52, "size": 10.0} for i in range(20)]
    t = 700.0
    a = HS.target(se, bt, fwd, t)
    b = HS.target(se, bt, [{"t": 10.0, "px": 0.48, "size": 1e6}] + fwd, t)
    assert a == b


def test_direction_is_only_supported_outside_the_spread():
    se = _book(bid=0.48, ask=0.52)
    bt = [o["t"] for o in se]
    pr = [{"t": 100.0, "px": 0.48, "size": 1.0},     # at the bid -> reaches BUY YES
          {"t": 101.0, "px": 0.52, "size": 1.0},     # at the ask -> reaches BUY NO
          {"t": 102.0, "px": 0.50, "size": 1.0}]     # inside -> UNSUPPORTED
    vy, vn, ny, nn, va, na = HS.classify(pr, se, bt, 0.0, 200.0)
    assert (ny, nn, na) == (1, 1, 1)
    assert (vy, vn, va) == (1.0, 1.0, 1.0)


def test_imbalance_is_none_when_there_is_no_flow():
    assert HS.imbalance(0.0, 0.0) is None
    assert HS.imbalance(3.0, 1.0) == pytest.approx(0.5)


def test_thin_windows_are_refused_not_guessed():
    se = _book()
    bt = [o["t"] for o in se]
    thin = [{"t": 100.0 + i, "px": 0.48, "size": 1.0} for i in range(HS.MIN_PRINTS_FEATURE - 1)]
    assert HS.features(se, bt, thin, 700.0) is None
    assert HS.target(se, bt, thin, 0.0) is None


def test_frozen_artifact_matches_the_code():
    """The artifact on disk must describe the selector the code implements."""
    a = json.load(open(FROZEN))
    assert [tuple(x) for x in a["features"]] == list(HS.FROZEN_FEATURES)
    assert a["horizons"]["lookback_s"] == HS.LOOKBACK_S
    assert a["horizons"]["horizon_s"] == HS.HORIZON_S
    assert a["cutoff_quantile"] == pytest.approx(HS.FROZEN_CUTOFF_Q)
    assert "drift_abs" not in [f for f, _ in HS.FROZEN_FEATURES]


def test_frozen_selector_reproduces_from_the_artifact():
    a = json.load(open(FROZEN))
    model = {"ref": a["train_reference"], "cutoff": a["cutoff_score"]}
    row = {"mid_dist_half": 0.0, "imb_cnt_lb": 0.0, "min_side_share": 0.5}
    assert HS.score(row, model["ref"]) == pytest.approx(0.0, abs=0.05)
    assert HS.eligible(row, model)
    worst = {"mid_dist_half": 0.49, "imb_cnt_lb": 1.0, "min_side_share": 0.0}
    assert not HS.eligible(worst, model)


def test_no_economic_input_reaches_the_selector():
    """P&L, settlement and our own fills must not be feature names."""
    banned = ("pnl", "settle", "realized", "fill", "matched", "residual", "basis")
    for f, _ in HS.FROZEN_FEATURES:
        assert not any(b in f for b in banned), f
