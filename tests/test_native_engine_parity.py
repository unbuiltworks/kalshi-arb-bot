"""Parity is the acceptance test: the live engine IS the frozen q50 policy.

`NativeEngine` is a port of `pmus_completion_shadow.run_window`'s decision
rules. These tests replay it over the exact 25-window frozen tape with a
simulated venue adapter and require the settled economics to reconcile to the
frozen q50 PROFIT_LOCK_5 ledger EXACTLY -- not approximately.

The separation that makes this meaningful:
  queue_aware is a FILL MODEL and lives in the ADAPTER, never in the engine.
  Live, authenticated venue fills take that seat and the engine is unchanged.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from scripts.audit_economic_complete import load, USABLE
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P


@pytest.fixture(scope="module")
def tape():
    return load()


@pytest.fixture(scope="module")
def parity(tape):
    return P.compare(tape, USABLE)


def test_every_window_reconciles_exactly(parity):
    bad = [(x["window"], str(x["delta_net"]))
           for x in parity["per_window"] if x["delta_net"] != 0]
    assert bad == [], "windows diverging from the frozen ledger: %s" % (bad,)


def test_the_mean_is_the_frozen_number(parity):
    assert parity["frozen_mean_net"] == parity["live_mean_net"]
    assert round(float(parity["live_mean_net"]), 4) == 6.1167


def test_lock_decisions_agree_window_by_window(parity):
    for x in parity["per_window"]:
        assert x["frozen_locked"] == x["live_locked"], x["window"]
        assert x["frozen_lock_via"] == x["live_lock_via"], x["window"]


def test_completion_counts_and_inventory_agree(parity):
    for x in parity["per_window"]:
        assert x["frozen_completions"] == x["live_completions"], x["window"]
        assert x["frozen_matched_qty"] == x["live_matched_qty"], x["window"]
        assert x["frozen_peak_resid"] == x["live_peak_resid"], x["window"]


# ------------------------------------------------------- the frozen constants

def test_the_frozen_parameters_are_not_tunable():
    assert (E.CLIP, E.THETA, E.VARIANT, E.HOLD_S) == (D(50), D("0.96"), "B", 60.0)
    assert (E.RESIDUAL_CAP_K, E.PROFIT_LOCK_USD) == (D(2), D(5))
    assert E.FLOOR_GUARD is False and E.COMPLEMENT_BIAS_C is None


def test_the_money_path_cannot_reach_the_l5_action_translator():
    """Rule 11. The generic touch-crossing taker fallback is prohibited."""
    # AST-based: the engine's own prose names the banned module (it has to,
    # to say it is banned), so a text scan is the wrong instrument here.
    E.assert_no_translate_fallback()

    import ast
    tree = ast.parse(open("src/pmus_l5_native_engine.py").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                imported.update(node.module.split("."))
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                imported.update(a.name.split("."))
    for banned in ("l5_faithful_translation", "l5_window_actions"):
        assert banned not in imported, banned


# --------------------------------------------------------- fail-closed shape

def test_a_book_with_neither_side_quoted_produces_no_decision():
    eng = E.NativeEngine()
    with pytest.raises(E.StaleBook):
        eng.on_book({"state": "MARKET_STATE_OPEN", "t": "2026-09-28T00:00:00Z",
                     "bid": None, "ask": None})


def test_a_non_open_market_produces_no_decision():
    eng = E.NativeEngine()
    with pytest.raises(E.StaleBook):
        eng.on_book({"state": "MARKET_STATE_CLOSED", "t": "2026-09-28T00:00:00Z",
                     "bid": "0.40", "ask": "0.60"})


def test_a_one_sided_book_still_quotes_the_side_that_is_quoted():
    """Frozen decides per side. Requiring both dropped a real clip on 0045z."""
    eng = E.NativeEngine()
    out = eng.on_book({"state": "MARKET_STATE_OPEN",
                       "t": "2026-09-28T00:00:00Z", "bid": "0.40", "ask": None})
    sides = {i["side"] for i in out if i.kind == E.REST}
    assert sides == {"up"}, out


def test_an_unconfirmed_completion_stops_the_pass():
    """No execute_taker means the fill is unconfirmed, so nothing after it in
    the row is decided on a book we only hope is correct."""
    eng = E.NativeEngine()
    eng.on_fill("up", D(10), D("4.00"), maker=True)
    row = {"state": "MARKET_STATE_OPEN", "t": "2026-09-28T00:00:00Z",
           "bid": "0.40", "ask": "0.60"}
    out = eng.on_book(row, {"t": "2026-09-28T00:00:00Z",
                            "bids": [["0.50", "100"]], "offers": []})
    kinds = [i.kind for i in out]
    if E.COMPLETE in kinds:
        assert kinds[-1] == E.COMPLETE, (
            "the pass continued past an unconfirmed completion: %s" % kinds)


def test_inventory_moves_only_on_fills():
    eng = E.NativeEngine()
    eng.on_book({"state": "MARKET_STATE_OPEN", "t": "2026-09-28T00:00:00Z",
                 "bid": "0.40", "ask": "0.60"})
    assert eng.bk.q == {"up": D(0), "down": D(0)}, (
        "resting an order moved inventory")
    eng.on_fill("up", D(50), D("20.00"), maker=True)
    assert eng.bk.q["up"] == D(50)


def test_lock_cancels_resting_and_permits_no_new_risk():
    eng = E.NativeEngine()
    eng.on_fill("up", D(100), D("10.00"), maker=True)
    eng.on_fill("down", D(100), D("10.00"), maker=True)
    row = {"state": "MARKET_STATE_OPEN", "t": "2026-09-28T00:00:00Z",
           "bid": "0.40", "ask": "0.60"}
    eng.resting["up"] = {"px": D("0.40"), "qty": D(50)}
    out = eng.on_book(row)
    assert eng.locked_at is not None and eng.lock_via == "HOLD"
    assert any(i.kind == E.CANCEL for i in out), out
    assert eng.resting["up"] is None
    assert [i for i in eng.on_book(row) if i.kind == E.REST] == []


def test_the_prospective_cap_is_tighter_than_frozen_never_looser():
    """Live decides before the venue answers, so the cap runs on the clip."""
    live = E.NativeEngine(prospective_cap=True)
    repl = E.NativeEngine(prospective_cap=False)
    for eng in (live, repl):
        eng.on_fill("up", D(50), D("20.00"), maker=True)
    row = {"state": "MARKET_STATE_OPEN", "t": "2026-09-28T00:00:00Z",
           "bid": "0.40", "ask": "0.60"}
    lr = [i for i in live.on_book(row) if i.kind == E.REST]
    rr = [i for i in repl.on_book(row) if i.kind == E.REST]
    assert len(lr) <= len(rr)
