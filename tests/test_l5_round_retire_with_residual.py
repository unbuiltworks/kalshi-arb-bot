"""Regression for ONE FILL PER WINDOW -- the four-window campaign deadlock.

On 2026-11-02 the owner-authorized BTC15m campaign (2215z / 2230z / 2245z /
2300z) produced EXACTLY ONE venue-confirmed fill per window and ZERO
completed pairs:

    2215z  DOWN 1.92 @ own 0.3900   DFWP527012WE
    2230z  UP   2.04 @ own 0.4700   DFWWGBW012WP
    2245z  DOWN 2.00 @ own 0.4500   DFX3B39012WQ
    2300z  UP   1.68 @ own 0.2100   DFXA7J9G12WP

Each window's heartbeat then read, for the rest of the window:

    "two_sided_round": {"round": 0, "retired": false,
                        "plan_unexecutable": "LEG_WOULD_CROSS:DOWN",
                        "retirement_blocked_by": "LEG_HOLDS_UNMATCHED:DOWN"}
    "accumulation_round": 0, "accumulation_max_rounds": 8
    "legs_claimed": {"UP": false, "DOWN": true}

Eight rounds were authorized and one was ever reachable.

THE DEADLOCK IS FOUR LINKS, all in `l5_live_execution`:

  1. `probe_store_plan(market, plan, rnd)` stores a plan for the CURRENT
     round only.
  2. `rnd` advances only by `probe_retire_round` followed by re-reading
     `probe_round`.
  3. That pair runs only `if not _blocked`.
  4. `probe_round_is_safe_to_retire` returned LEG_HOLDS_UNMATCHED whenever
     `up != dn`.

One leg filling alone makes `up != dn`. If the complement is then refused
-- and it is refused precisely because the market moved, which is WHY the
maker leg filled -- the round can never retire, `rnd` never advances, no
further plan is ever stored, and `probe_round` returns 0 forever because
round 0 still has an unspent leg. The market is dead for the window after
one fill.

These tests reproduce that freeze against a real `FaithfulExecutor` and a
real `InventoryStore`, then prove the flag lifts it WITHOUT touching the two
guards that stop a round walking away from an order the venue may still hold.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_live_validation_gates as VG
from tests import test_l5_live_execution as H

SLUG = "cpc-btc-updown-15m-2026-11-02-2215z"
FLAG = E.ROUND_RETIRE_RESIDUAL_ENV

#: The 2215z plan, at the prices the window actually quoted.
PLAN0 = {"pair_cost": "0.9500",
         "legs": [{"side": "DOWN", "price": "0.3900", "qty": "1.92",
                   "mode": "MAKER"},
                  {"side": "UP", "price": "0.5600", "qty": "1.92",
                   "mode": "MAKER"}]}
#: What a SECOND round would plan once the book has moved.
PLAN1 = {"pair_cost": "0.9500",
         "legs": [{"side": "DOWN", "price": "0.0900", "qty": "2.00",
                   "mode": "MAKER"},
                  {"side": "UP", "price": "0.8600", "qty": "2.00",
                   "mode": "MAKER"}]}


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "EXP-ONEFILL")
    monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "TS-ONEFILL")
    monkeypatch.setenv("L5_WINDOW_STRATEGY", "TWO_SIDED")
    monkeypatch.setenv("L5_TS_MARKET", SLUG)
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    t = H.FakeTransport(fill_all=True)
    t.tick_size = D("0.01")
    st = H.store(tmp_path)
    ex = E.FaithfulExecutor(st, H.limits(), transport=t, now_fn=H.NOW,
                            maker_only=True)
    ex.validation_gates = VG.ValidationGates(max_cash_deployed_usd=D("12"))
    ex.available_cash_usd = D("300")
    ex.probe_store_plan(SLUG, PLAN0, 0)
    return {"ex": ex, "store": st, "mp": monkeypatch}


def one_leg_fills(rig_):
    """The 2215z fill, exactly: DOWN 1.92 at own 0.3900, UP never fills."""
    rig_["store"].apply_fill(SLUG, "DOWN", D("1.92"), D("0.3900"),
                             basis="OWN")


def advance(rig_):
    """The runner's own sequence: retire if safe, then re-read the round.

    This is lines 6932-6934 reduced to the two calls that move `rnd`.
    """
    ex = rig_["ex"]
    blocked = ex.probe_round_is_safe_to_retire(SLUG, ex.probe_round(SLUG))
    if not blocked:
        ex.probe_retire_round(SLUG, ex.probe_round(SLUG),
                              "LEG_WOULD_CROSS:DOWN")
    return ex.probe_round(SLUG), blocked


# =================================================== 1. REPRODUCE THE FREEZE

def test_one_fill_freezes_the_market_at_round_zero(rig):
    """THE OBSERVED BUG. Default configuration, one leg filled, 40 ticks."""
    one_leg_fills(rig)
    seen = [advance(rig) for _ in range(40)]
    assert {r for r, _ in seen} == {0}, "round must never advance"
    assert {b for _, b in seen} == {"LEG_HOLDS_UNMATCHED:DOWN"}
    # and because plans are stored per round, round 1 never gets one
    assert rig["ex"].probe_stored_plan(SLUG, 1) is None
    # seven of the eight authorized rounds were never reachable
    assert rig["ex"].acc_max_rounds() >= 8


def test_the_frozen_round_still_reports_an_unspent_leg(rig):
    """Why `probe_round` pins at 0: round 0 has an unspent UP leg forever."""
    one_leg_fills(rig)
    assert rig["ex"].probe_legs_claimed(SLUG, 0)["UP"] is False
    assert rig["ex"].probe_round(SLUG) == 0


def test_the_freeze_needs_no_imbalance_larger_than_one_fill(rig):
    """Any unmatched quantity at all does it -- 0.01 contracts is enough."""
    rig["store"].apply_fill(SLUG, "DOWN", D("0.01"), D("0.3900"),
                            basis="OWN")
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "LEG_HOLDS_UNMATCHED:DOWN"


# ====================================================== 2. THE FIX LIFTS IT

def test_the_flag_lets_the_round_retire_and_advance(rig):
    one_leg_fills(rig)
    rig["mp"].setenv(FLAG, "YES")
    rnd, blocked = advance(rig)
    assert blocked is None
    assert rnd == 1
    assert rig["ex"].probe_round_retired(SLUG, 0) is True


def test_the_next_round_gets_its_own_plan_at_the_new_book(rig):
    """The point of advancing: a fresh pair planned at the book as it NOW is,
    which is the repeated independently tracked intent the freeze prevented.
    """
    one_leg_fills(rig)
    rig["mp"].setenv(FLAG, "YES")
    rnd, _ = advance(rig)
    rig["ex"].probe_store_plan(SLUG, PLAN1, rnd)
    assert rig["ex"].probe_stored_plan(SLUG, rnd) == PLAN1
    # round 0's plan is untouched: leg two of round 0 still matches leg one
    assert rig["ex"].probe_stored_plan(SLUG, 0) == PLAN0
    # and round 1's legs are independently tracked, both unspent
    assert rig["ex"].probe_legs_claimed(SLUG, rnd) == {"UP": False,
                                                       "DOWN": False}


def test_rounds_keep_advancing_across_repeated_stranded_fills(rig):
    """Four stranded first legs in one window must yield four rounds, not
    one. This is the behaviour the campaign could not produce."""
    rig["mp"].setenv(FLAG, "YES")
    rounds = []
    for i, (side, px) in enumerate([("DOWN", "0.3900"), ("UP", "0.4700"),
                                    ("DOWN", "0.4500"), ("UP", "0.2100")]):
        r = rig["ex"].probe_round(SLUG)
        rounds.append(r)
        rig["ex"].probe_store_plan(SLUG, PLAN0, r)
        rig["store"].apply_fill(SLUG, side, D("1.00"), D(px), basis="OWN")
        advance(rig)
    assert rounds == [0, 1, 2, 3]


def test_retirement_is_not_consumed_when_the_round_is_balanced(rig):
    """The flag changes the IMBALANCE branch only; a matched round retired
    before the fix and still does."""
    rig["store"].apply_fill(SLUG, "DOWN", D("1.92"), D("0.3900"), basis="OWN")
    rig["store"].apply_fill(SLUG, "UP", D("1.92"), D("0.5600"), basis="OWN")
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) is None
    rig["mp"].setenv(FLAG, "YES")
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) is None


# ============================ 3. THE ORDER GUARDS ARE UNCHANGED BY THE FLAG

@pytest.mark.parametrize("flag", [None, "YES"])
def test_a_live_order_blocks_retirement_with_or_without_the_flag(rig, flag):
    """No round may walk away from an order the venue still holds."""
    one_leg_fills(rig)
    if flag:
        rig["mp"].setenv(FLAG, flag)
    rig["ex"].store.live_order_count = lambda: 1
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "ORDER_STILL_LIVE"


@pytest.mark.parametrize("flag", [None, "YES"])
def test_a_still_fillable_order_blocks_retirement_either_way(rig, flag):
    one_leg_fills(rig)
    if flag:
        rig["mp"].setenv(FLAG, flag)
    rig["ex"].store.live_order_count = lambda: 0
    rig["ex"].store.still_fillable_order_count = lambda: 1
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "ORDER_STILL_FILLABLE"


@pytest.mark.parametrize("attr", ["live_order_count",
                                  "still_fillable_order_count",
                                  "position"])
def test_unreadable_state_still_forbids_retirement_under_the_flag(rig, attr):
    one_leg_fills(rig)
    rig["mp"].setenv(FLAG, "YES")

    def boom(*_a, **_k):
        raise RuntimeError("store exploded")

    setattr(rig["ex"].store, attr, boom)
    why = rig["ex"].probe_round_is_safe_to_retire(SLUG, 0)
    assert why is not None and why.startswith("STATE_UNREADABLE")


# ================================= 4. THE RESIDUAL IS CARRIED, NOT ABANDONED

def test_retiring_keeps_the_stranded_inventory_on_the_books(rig):
    """"Retire the round" must not mean "forget the leg" -- the position and
    the residual both survive, which is what keeps it inside every bound."""
    one_leg_fills(rig)
    rig["mp"].setenv(FLAG, "YES")
    advance(rig)
    assert rig["store"].position(SLUG, "DOWN") == D("1.92")
    assert rig["store"].position(SLUG, "UP") == D(0)
    assert abs(rig["store"].residual(SLUG)) == D("1.92")


def test_the_carried_residual_still_trips_the_residual_ceiling(rig):
    """The bound that makes carrying it safe. Retirement buys a round slot,
    never residual headroom."""
    one_leg_fills(rig)
    rig["mp"].setenv(FLAG, "YES")
    advance(rig)
    lim = H.limits(max_abs_residual=D("2"))
    assert lim.refuse(price=D("0.40"), qty=D("1.00"), market=SLUG,
                      committed_by_market={}, residual_after=D("-2.92"),
                      realized_today=D(0), open_markets=[SLUG],
                      outstanding_by_market={}, live_order_count=0) == \
        "MAX_ABS_RESIDUAL"


def test_retirement_does_not_refund_committed_cash_under_the_flag(rig):
    one_leg_fills(rig)
    rig["mp"].setenv(FLAG, "YES")
    before = dict(rig["store"].state.get("committed") or {})
    advance(rig)
    assert dict(rig["store"].state.get("committed") or {}) == before


def test_retirement_writes_only_the_additive_marker(rig):
    one_leg_fills(rig)
    rig["mp"].setenv(FLAG, "YES")
    before = {k: v for k, v in rig["store"].state.items()
              if k != "probe_rounds_retired"}
    advance(rig)
    after = {k: v for k, v in rig["store"].state.items()
             if k != "probe_rounds_retired"}
    assert after == before


# ========================================= 5. DEFAULT OFF MEANS UNCHANGED

@pytest.mark.parametrize("val", [None, "", "no", "NO", "0", "false", "true",
                                 "1", "yes ", "Y"])
def test_only_an_explicit_YES_changes_behaviour(rig, val):
    """Anything but YES keeps the deployed engine exactly as it shipped."""
    one_leg_fills(rig)
    if val is None:
        rig["mp"].delenv(FLAG, raising=False)
    else:
        rig["mp"].setenv(FLAG, val)
    expect = None if (val or "").strip().upper() == "YES" \
        else "LEG_HOLDS_UNMATCHED:DOWN"
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == expect
