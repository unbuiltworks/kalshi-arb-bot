"""The three-layer faithful engine: attribution, and what must never leak.

Each test exists because a specific confusion would corrupt the conclusion:
a safety refusal counted as L5 behaviour, a venue failure counted as ours,
crossing used where resting was possible, or a future price consulted.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_faithful_translation as F
from src import l5_fidelity_report as R


def row(key, side, l5px, size=10.0, bid="0.40", ask="0.60",
        bid_sh="500", ask_sh="500", age=0.5, i=0):
    return {
        "action_key": key, "kind": "l5_action", "backlog": False,
        "l5_event_utc": "2026-09-23T16:%02d:00+00:00" % i,
        "window_start_utc": "2026-09-23T16:15:00+00:00",
        "pmus_snapshot_age_s": age,
        "l5": {"pmus_side": side, "l5_price": l5px, "l5_size": size},
        "pmus": {"bbo": {"bid": bid, "ask": ask,
                         "bid_shares": bid_sh, "ask_shares": ask_sh}},
        "copyability": {"l5_price": l5px},
    }


TTP = {"klass": "TRADE_THROUGH_PRICE"}
NONE_ = {"klass": "NO_INTERACTION"}


def test_l5_native_is_never_filtered_by_our_preferences():
    # a price far outside our band, and a size nothing like q=1, is still
    # exactly what L5 chose
    d = F.l5_native(row("a", "UP", "0.03", size=900.0))
    assert d["side"] == "UP"
    assert d["price"] == D("0.03")
    assert d["l5_size"] == D("900.0")


def test_resting_is_preferred_and_costs_no_slippage():
    r = row("a", "UP", "0.50", ask="0.60")        # 0.50 < ask, restable
    tr = F.pmus_translate(F.l5_native(r), r, {"a": TTP}, D(1))
    assert tr["ok"] and tr["mode"] == F.MAKER
    assert tr["slippage"] == 0
    assert tr["price"] == D("0.50")


def test_crossing_only_when_resting_is_impossible():
    r = row("a", "UP", "0.65", ask="0.60")        # through the ask
    tr = F.pmus_translate(F.l5_native(r), r, {}, D(1))
    assert tr["ok"] and tr["mode"] == F.TAKER
    assert tr["price"] == D("0.60")               # the contemporaneous touch


def test_down_crosses_at_one_minus_bid():
    # buying NO at p is selling YES at 1-p, so the NO ask is 1 - yes_bid
    r = row("a", "DOWN", "0.70", bid="0.40")      # our NO touch is 0.60
    tr = F.pmus_translate(F.l5_native(r), r, {}, D(1))
    assert tr["ok"] and tr["mode"] == F.TAKER
    assert tr["price"] == D("0.60")


def test_a_crossed_fill_never_pays_more_than_l5():
    # definitional: we cross only when l5_price >= our touch
    r = row("a", "UP", "0.65", ask="0.60")
    tr = F.pmus_translate(F.l5_native(r), r, {}, D(1))
    assert tr["slippage"] <= 0


def test_fill_is_capped_at_displayed_depth():
    r = row("a", "UP", "0.65", ask="0.60", ask_sh="3")
    tr = F.pmus_translate(F.l5_native(r), r, {}, D(100))
    assert tr["qty"] == D("3") and tr["depth_capped"] is True


def test_no_displayed_depth_is_a_translation_failure_not_a_fill():
    r = row("a", "UP", "0.65", ask="0.60", ask_sh="0")
    tr = F.pmus_translate(F.l5_native(r), r, {}, D(1))
    assert not tr["ok"] and tr["reason"] == F.T_NO_DEPTH


def test_a_resting_order_nothing_traded_through_did_not_fill():
    r = row("a", "UP", "0.50")
    tr = F.pmus_translate(F.l5_native(r), r, {"a": NONE_}, D(1))
    assert not tr["ok"] and tr["reason"] == F.T_NO_INTERACTION


def test_missing_authenticated_evidence_is_unevaluable_not_a_fill():
    r = row("a", "UP", "0.50")
    tr = F.pmus_translate(F.l5_native(r), r, {}, D(1))
    assert not tr["ok"] and tr["reason"] == F.T_UNEVALUABLE


def test_a_stale_observation_is_a_translation_failure():
    r = row("a", "UP", "0.65", age=99.0)
    tr = F.pmus_translate(F.l5_native(r), r, {}, D(1))
    assert not tr["ok"] and tr["reason"] == F.T_STALE


def test_safety_refusal_is_attributed_to_the_overlay_never_to_l5_or_pmus():
    # price outside our band, but PMUS would have executed it
    r = row("a", "UP", "0.95", ask="0.90", i=0)
    lim = F.SafetyLimits(band=(D("0.20"), D("0.80")))
    run = F.run_window([r], {}, lam=D(1), limits=lim)
    assert run["by_layer"] == {"SAFETY_OVERLAY": 1}
    assert "SAFETY_OVERLAY:BAND" in run["refused"]
    # and the same action, venue-only, DOES reproduce
    venue = F.run_window([r], {}, lam=D(1),
                         limits=F.SafetyLimits(enabled=False))
    assert venue["reproduced"] == 1


def test_the_overlay_never_rewrites_the_native_path():
    r = row("a", "UP", "0.95", ask="0.90")
    lim = F.SafetyLimits(band=(D("0.20"), D("0.80")))
    run = F.run_window([r], {}, lam=D(1), limits=lim)
    # L5's own decision is still counted in the native path
    assert run["n_actions"] == 1
    assert run["native_sides"] == ["UP"]


def test_venue_failures_are_not_attributed_to_the_overlay():
    r = row("a", "UP", "0.65", ask="0.60", ask_sh="0")
    run = F.run_window([r], {}, lam=D(1), limits=F.SafetyLimits())
    assert run["by_layer"] == {"PMUS_TRANSLATION": 1}


def test_over_reproduction_is_a_fidelity_failure_too():
    # native never crosses zero; the track does. That is NOT agreement.
    run = {"n_actions": 2, "reproduced": 2, "by_layer": {}, "refused": {},
           "mode_mix": {}, "native_sides": ["UP", "UP"],
           "track_sides": ["UP", "DOWN"],
           "native_imbalance": [1.0, 2.0], "track_imbalance": [1.0, -3.0],
           "matched_path": [0.0, 1.0], "residual_path": [1.0, -3.0],
           "pair_cost_path": [], "up": D(1), "dn": D(3),
           "cost_up": D(0), "cost_dn": D(0), "qty_up": D(1), "qty_dn": D(3),
           "fees": D(0), "rebates": D(0), "slippage_usd": D(0),
           "committed_usd": D(0), "per_action": []}
    rep = R.report(run, yes_value=D(1), lam=D(1))
    z = rep["zero_cross_overshoot"]
    assert z["crossing_agrees"] is False
    assert z["direction"] == "TRACK_OVER_REPRODUCES"


def test_relative_sizing_changes_whether_l5_himself_crosses_zero():
    # the finding that retired an earlier claim: counting actions as q=1
    # destroys the geometry that produces L5's crossings
    q1 = [1.0, 2.0, 1.0, 0.0]            # two up, two down, never negative
    rel = [10.0, 20.0, -30.0, -80.0]     # same sides, L5's real sizes
    assert R.crossed_zero(q1) is False
    assert R.crossed_zero(rel) is True
