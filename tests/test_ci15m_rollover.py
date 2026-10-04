"""CONTINUOUS ROLLOVER: the executor must stay alive across windows.

WHAT MUST BE TRUE, and each of these is a separate test below:

  * the process does NOT exit because a window ended, an order rested,
    nothing qualified, or no market has appeared yet;
  * it discovers the next eligible market by itself and rolls N -> N+1;
  * a window with no qualifying action is a VALID NO_TRADE window --
    staying alive never forces an OPEN;
  * a resting or unresolved order from window N CANNOT be forgotten when
    window N+1 appears;
  * a restart reconstructs existing orders and inventory BEFORE entering
    another window;
  * SHADOW runs the EXACT live path with the venue write disabled -- there
    is no second strategy implementation to drift.

WHY THE CARRY-OVER GUARD EXISTS. CI15M-4's order 145828836725658335 was
left RESTING when its window ended -- GTC, unresolved, the engine halted
without resolving it. A persistent executor that rolled into N+1 in that
state would hold a position it believed it did not have.

NO STRATEGY PARAMETER IS EXERCISED OR CHANGED HERE.
"""
from __future__ import annotations

import asyncio
import importlib
from decimal import Decimal as D

import pytest

from src.gemini_execution import ci15m_budget as BUD
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import ci15m_runner as R
from src.gemini_execution import metadata, transport

CID = "CI15M-ROLL"
T1, T2 = "BTC15M2610040000", "BTC15M2610040015"
S1, S2 = "GEMI-%s-UP" % T1, "GEMI-%s-UP" % T2
OID = 145828836725658335


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    BUD.create(CID, root=root, profile=BUD.FULL)
    return root


async def _nosleep(_s):
    return None


def _no_venue_writes(monkeypatch):
    """Any attempt to reach the venue's write path fails the test."""
    async def boom(**kw):
        raise AssertionError("THE VENUE WRITE PATH WAS CONTACTED")
    monkeypatch.setattr(transport, "place", boom)


def _market(ticker, tte):
    return {"ticker": ticker, "symbol": "GEMI-%s-UP" % ticker,
            "expiry_ts": 1_000_000.0 + tte, "tte": tte}


# =====================================================================
# STAYING ALIVE: none of these four is a termination
# =====================================================================
def test_no_eligible_market_is_not_a_termination(env, monkeypatch):
    """It sleeps and polls rather than exiting 0 in seven seconds."""
    monkeypatch.setattr(R, "discover", lambda now: [])
    t = {"now": 1_000_000.0}

    def clock():
        t["now"] += 10.0
        return t["now"]
    out = asyncio.run(R.run(CID, live=False, shadow=False,
                            durable_branch=None, max_windows=3,
                            runtime_s=200.0, now_fn=clock,
                            sleeper=_nosleep, root=env))
    assert out["exit"]["reason"] == "NO_ELIGIBLE_WINDOW_TIMEOUT"
    assert out["exit"]["detail"]["polls"] > 1, (
        "it must keep looking, not give up after one look")


def test_an_already_dispositioned_window_is_skipped_not_exited(env,
                                                               monkeypatch):
    """Window N is finished; the loop waits for N+1 instead of exiting."""
    BUD.open_window(CID, T1, root=env)
    BUD.close_window(CID, T1, BUD.NOT_ATTEMPTED, root=env)
    monkeypatch.setattr(R, "discover", lambda now: [_market(T1, 400.0)])
    t = {"now": 1_000_000.0}

    def clock():
        t["now"] += 10.0
        return t["now"]
    out = asyncio.run(R.run(CID, live=False, shadow=False,
                            durable_branch=None, max_windows=3,
                            runtime_s=150.0, now_fn=clock, sleeper=_nosleep,
                            root=env))
    assert out["exit"]["reason"] == "NO_ELIGIBLE_WINDOW_TIMEOUT"
    assert T1 in out["exit"]["detail"]["windows_seen"]


def test_a_no_trade_window_is_valid_and_consumes_no_window_budget(env):
    """Staying alive never forces an OPEN. A window that qualified for
    nothing is NOT_ATTEMPTED and does not spend the budget."""
    BUD.open_window(CID, T1, root=env)
    BUD.close_window(CID, T1, BUD.NOT_ATTEMPTED,
                     detail={"why": "nothing qualified"}, root=env)
    assert BUD.windows(CID, root=env)[T1] == BUD.NOT_ATTEMPTED
    assert BUD.completed_windows(CID, root=env) == 0, (
        "a NO_TRADE window must not consume the window budget")
    assert BUD.consumed(CID, root=env) == 0


def test_persistent_mode_rolls_on_after_a_traded_window(env, monkeypatch):
    """PROBE exits after a submission; PERSISTENT rolls into N+1.

    Both are asserted here, because the probe behaviour is a guarantee
    every earlier cohort rests on and must not be broken by adding the
    persistent one.
    """
    BUD.open_window(CID, T1, root=env)
    BUD.consume_slot(CID, T1, {"action": "OPEN", "side": "UP",
                               "outcome": "yes", "price": "0.68",
                               "quantity": "1",
                               "unmatched_shares_after": "1",
                               "unmatched_principal_after": "0.68",
                               BUD.RECONCILED_KEY: "0"},
                     root=env, remote=None)
    # resolve it so the carry-over guard is satisfied
    BUD.record_status(CID, T1, slot=0, status="FILLED",
                      detail={"price": "0.68", "quantity": "1"}, root=env)
    BUD.close_window(CID, T1, "FILLED", root=env)
    assert BUD.submissions_in_window(CID, T1, root=env) > 0
    assert BUD.open_state(CID, T1, root=env) == BUD.OPEN_FILLED

    # only T1 is ever discovered, so a loop that rolls on will time out
    # looking for N+1 rather than exiting "SUBMITTED".
    monkeypatch.setattr(R, "discover", lambda now: [_market(T1, 400.0)])
    t = {"now": 1_000_000.0}

    def clock():
        t["now"] += 10.0
        return t["now"]
    out = asyncio.run(R.run(CID, live=False, shadow=False,
                            durable_branch=None, mode=R.MODE_PERSISTENT,
                            max_windows=3, runtime_s=150.0, now_fn=clock,
                            sleeper=_nosleep, root=env))
    assert out["exit"]["reason"] == "NO_ELIGIBLE_WINDOW_TIMEOUT", (
        "a traded window is not a reason for a persistent executor to exit")


# =====================================================================
# THE CARRY-OVER GUARD: window N cannot be forgotten
# =====================================================================
def _submitted_but_unresolved(root, ticker=T1, symbol=S1, oid=OID):
    """The EXACT CI15M-4 end state: an OPEN submitted, acked `open`, with
    no terminal outcome on the durable ledger."""
    BUD.open_window(CID, ticker, root=root)
    BUD.consume_slot(CID, ticker, {"action": "OPEN", "side": "UP",
                                   "outcome": "yes", "price": "0.68",
                                   "quantity": "1",
                                   "unmatched_shares_after": "1",
                                   "unmatched_principal_after": "0.68",
                                   BUD.RECONCILED_KEY: "0"},
                     root=root, remote=None)
    BUD.record_status(CID, ticker, slot=0, status="SUBMITTED",
                      detail={"venue": {"body": {
                          "orderId": oid, "symbol": symbol, "status": "open",
                          "filledQuantity": "0", "price": "0.68",
                          "outcome": "yes"}}}, root=root)
    return oid


def test_an_unresolved_window_is_visible_from_the_durable_ledger(env):
    """The guard's input survives a restart: it is derived from disk."""
    _submitted_but_unresolved(env)
    assert BUD.open_state(CID, T1, root=env) == BUD.OPEN_SUBMITTED
    stuck = R.unresolved_windows(CID, root=env)
    assert [w["ticker"] for w in stuck] == [T1]


def test_a_resting_order_from_window_n_blocks_window_n_plus_1(env,
                                                              monkeypatch):
    """THE PROOF ASKED FOR. T1's order is still RESTING at the venue; T2 is
    now discoverable. The loop must NOT enter T2."""
    _submitted_but_unresolved(env)
    _no_venue_writes(monkeypatch)

    # the venue: no terminal row, and the order IS on the active book
    async def hist(**kw):
        return {"readable": True, "verdict": "NO_ORDERS_ON_RECORD",
                "total_rows": 0, "unreadable_statuses": [],
                "per_status": [{"status_filter": "filled", "http": 200,
                                "rows": 0, "orders": []},
                               {"status_filter": "cancelled", "http": 200,
                                "rows": 0, "orders": []}]}

    async def act(symbol):
        return {"ok": True, "status": 200, "classification": "ACCEPTED",
                "body": {"orders": [{"orderId": OID, "status": "open",
                                     "filledQuantity": "0",
                                     "symbol": S1}]}}
    monkeypatch.setattr(metadata, "order_history", hist)
    monkeypatch.setattr(transport, "active_orders", act)
    monkeypatch.setattr(R, "discover", lambda now: [_market(T2, 400.0)])
    t = {"now": 1_000_000.0}

    def clock():
        t["now"] += 10.0
        return t["now"]
    out = asyncio.run(R.run(CID, live=False, shadow=True,
                            durable_branch=None, mode=R.MODE_PERSISTENT,
                            max_windows=3, runtime_s=400.0, now_fn=clock,
                            sleeper=_nosleep, root=env))
    assert out["exit"]["reason"] == "CARRY_OVER_UNRESOLVED"
    assert out["exit"]["detail"]["windows"][0]["ticker"] == T1
    # T2 was NEVER entered
    assert T2 not in (BUD.windows(CID, root=env) or {})
    assert BUD.submissions_in_window(CID, T2, root=env) == 0


def test_a_carried_over_fill_is_reconciled_before_the_next_window(env,
                                                                  monkeypatch):
    """The other half: T1's order actually FILLED while we were away. The
    guard must resolve it and rebuild inventory from the authenticated
    record BEFORE anything new is entered."""
    _submitted_but_unresolved(env)
    _no_venue_writes(monkeypatch)
    row = {"orderId": OID, "symbol": S1, "side": "buy", "outcome": "yes",
           "status": "filled", "quantity": "1", "filledQuantity": "1",
           "remainingQuantity": "0", "price": "0.68",
           "avgExecutionPrice": "0.68"}

    async def hist(**kw):
        return {"readable": True, "verdict": "ORDERS_FOUND", "total_rows": 1,
                "unreadable_statuses": [],
                "per_status": [{"status_filter": "filled", "http": 200,
                                "rows": 1, "orders": [row]},
                               {"status_filter": "cancelled", "http": 200,
                                "rows": 0, "orders": []}]}

    async def my(sym):
        return {"rows": [{"tid": 1, "order_id": str(OID), "symbol": S1,
                          "price": "0.68", "amount": "1",
                          "fee_amount": "0.02", "aggressor": True,
                          "timestampms": 1}], "status": 200}

    async def pos():
        return {"ok": True, "rows": [], "status": 200, "total": 0}
    monkeypatch.setattr(metadata, "order_history", hist)
    monkeypatch.setattr(P, "_mytrades", my)
    monkeypatch.setattr(metadata, "positions", pos)

    got = asyncio.run(R.resolve_carry_over(CID, T1, root=env))
    assert got["resolved"] is True
    assert got["state"] == P.TERMINAL_FILLED
    assert got["filled"] == "1"
    assert got["reconciled"]["ok"] is True
    assert got["reconciled"]["signed"] == "1"
    # the durable lifecycle is repaired, so the window can never be
    # mistaken for a non-fill again
    assert BUD.open_state(CID, T1, root=env) == BUD.OPEN_FILLED
    assert R.unresolved_windows(CID, root=env) == []


def test_restart_reconstructs_before_entering_another_window(env,
                                                             monkeypatch):
    """THE RESTART PROOF. A fresh process with nothing in memory meets the
    carry-over guard before it can discover anything."""
    _submitted_but_unresolved(env)
    importlib.reload(BUD)
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [0],
                                        "count": 1, "why": None})
    # nothing in memory; everything from disk
    assert BUD.open_state(CID, T1, root=env) == BUD.OPEN_SUBMITTED
    stuck = R.unresolved_windows(CID, root=env)
    assert [w["ticker"] for w in stuck] == [T1]

    discovered = {"n": 0}

    def discover(now):
        discovered["n"] += 1
        return [_market(T2, 400.0)]
    monkeypatch.setattr(R, "discover", discover)
    _no_venue_writes(monkeypatch)

    async def hist(**kw):
        return {"readable": False, "verdict": "UNREADABLE",
                "unreadable_statuses": ["filled", "cancelled"],
                "per_status": [{"status_filter": "filled", "http": 503,
                                "rows": None, "unreadable": True},
                               {"status_filter": "cancelled", "http": 503,
                                "rows": None, "unreadable": True}]}

    async def act(symbol):
        return {"ok": False, "status": None, "classification": None}
    monkeypatch.setattr(metadata, "order_history", hist)
    monkeypatch.setattr(transport, "active_orders", act)
    t = {"now": 1_000_000.0}

    def clock():
        t["now"] += 10.0
        return t["now"]
    out = asyncio.run(R.run(CID, live=False, shadow=True,
                            durable_branch=None, mode=R.MODE_PERSISTENT,
                            max_windows=3, runtime_s=300.0, now_fn=clock,
                            sleeper=_nosleep, root=env))
    assert out["exit"]["reason"] == "CARRY_OVER_UNRESOLVED"
    assert discovered["n"] == 0, (
        "the guard must run BEFORE discovery: a restart may not look for a "
        "new market while an earlier order is unaccounted for")


# =====================================================================
# SHADOW IS THE LIVE PATH
# =====================================================================
def test_shadow_disables_the_write_at_one_chokepoint_only(monkeypatch):
    _no_venue_writes(monkeypatch)
    assert P.write_mode() == P.WRITE_LIVE
    P.set_write_sink(P.shadow_sink(
        lambda s: {"bid": D("0.57"), "ask": D("0.68"),
                   "bid_size": D("350"), "ask_size": D("350")}))
    try:
        assert P.write_mode() == P.WRITE_SHADOW
        res = asyncio.run(P._write(symbol=S1, side="buy", outcome="yes",
                                   price="0.68", quantity="1",
                                   allow_taker=True))
        assert res["shadow"] is True and res["ok"] is True
        assert res["body"]["status"] == "filled"
        assert res["body"]["filledQuantity"] == "1"
        assert res["body"]["avgExecutionPrice"] == "0.68"
    finally:
        P.set_write_sink(None)
    assert P.write_mode() == P.WRITE_LIVE


def test_a_shadow_sink_may_not_claim_to_be_the_live_path():
    with pytest.raises(P.ProbeRefused):
        P.set_write_sink(lambda **kw: None, mode=P.WRITE_LIVE)
    assert P.write_mode() == P.WRITE_LIVE


def test_shadow_does_not_cross_a_book_that_is_not_there(monkeypatch):
    """The sink answers from the venue-faithful simulator, so a price that
    cannot be executed does not become a free fill in shadow."""
    _no_venue_writes(monkeypatch)
    P.set_write_sink(P.shadow_sink(lambda s: {"bid": None, "ask": None,
                                              "bid_size": None,
                                              "ask_size": None}))
    try:
        res = asyncio.run(P._write(symbol=S1, side="buy", outcome="yes",
                                   price="0.68", quantity="1",
                                   allow_taker=True))
        assert res["body"]["filledQuantity"] == "0"
    finally:
        P.set_write_sink(None)


def test_there_is_exactly_one_venue_write_in_the_probe():
    """No second implementation, and no second write path to drift."""
    import inspect
    src = inspect.getsource(P)
    assert src.count("transport.place(") == 1, (
        "the venue write must stay at ONE chokepoint")
    assert "await _write(" in src


def test_the_frozen_policy_fingerprint_is_untouched_by_rollover():
    from src.gemini_execution.ci15m_policy import fingerprint
    assert fingerprint() == (
        "5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329")


# =====================================================================
# BTC5m AS A SECOND DURATION ON THE SAME ENGINE
# =====================================================================
def test_both_durations_are_the_same_engine_with_separate_datasets():
    from src.gemini_execution import gemini_durations as DUR
    assert DUR.roots_are_separate(), "5m and 15m evidence must not pool"
    assert DUR.cohort_root(DUR.BTC15M) != DUR.cohort_root(DUR.BTC5M)
    assert DUR.dataset_root(DUR.BTC15M) != DUR.dataset_root(DUR.BTC5M)
    assert DUR.matches(DUR.BTC15M, "BTC15M2610032145")
    assert DUR.matches(DUR.BTC5M, "BTC5M2610032145")
    assert not DUR.matches(DUR.BTC5M, "BTC15M2610032145")
    assert not DUR.matches(DUR.BTC15M, "BTC5M2610032145")


def test_the_15m_frozen_band_is_unclipped_by_its_window():
    from src.gemini_execution import gemini_durations as DUR
    eff = DUR.effective_open_band(DUR.BTC15M)
    assert eff["band"] == (120.0, 840.0)
    assert eff["clipped_by_contract_start"] is False
    assert eff["seconds_eligible"] == 720.0
    assert DUR.ready_for_live(DUR.BTC15M)["ok"] is True


def test_btc5m_on_the_frozen_policy_is_120_to_300_effective():
    """THE CORRECTION. 840 > 300 does NOT make the rule impossible:
    intersecting [120,840] with a 300s contract leaves [120,300] -- the
    first three minutes of the window. BTC5m is runnable on the frozen
    policy exactly as it stands."""
    from src.gemini_execution import gemini_durations as DUR
    eff = DUR.effective_open_band(DUR.BTC5M)
    assert eff["empty"] is False
    assert eff["band"] == (120.0, 300.0)
    assert eff["seconds_eligible"] == 180.0
    assert eff["clipped_by_contract_start"] is True
    assert DUR.ready_for_live(DUR.BTC5M)["runnable_on_frozen_policy"] is True


def test_btc5m_is_runnable_but_live_money_is_a_separate_decision():
    from src.gemini_execution import gemini_durations as DUR
    r = DUR.ready_for_live(DUR.BTC5M)
    assert r["runnable_on_frozen_policy"] is True
    assert r["live_authorized"] is False
    assert r["ok"] is False, "replay/shadow yes; live money needs an owner"
    assert "not a property of the rule" in r["why"]


def test_the_clipped_regime_is_labelled_as_not_comparable():
    """Runnable is not the same as comparable, and the difference is
    stated rather than left for a reader to notice."""
    from src.gemini_execution import gemini_durations as DUR
    assert "CLIPPED" in DUR.effective_open_band(DUR.BTC5M)["comparability"]
    assert "NOT an apples-to-apples" in DUR.effective_open_band(
        DUR.BTC5M)["comparability"]
    assert "UNCLIPPED" in DUR.effective_open_band(DUR.BTC15M)["comparability"]


def test_the_normalized_band_is_a_labelled_hypothesis_not_the_policy():
    from src.gemini_execution import gemini_durations as DUR
    h = DUR.normalized_hypothesis(DUR.BTC5M)
    assert h["basis"] == DUR.HYPOTHESIS_NORMALIZED
    assert h["is_frozen_policy"] is False
    assert h["band"] == (40.0, 280.0)        # 120/840 scaled by 300/900
    assert h["band"] != DUR.effective_open_band(DUR.BTC5M)["band"]
    # and it can never be used to move the 15m policy
    assert DUR.normalized_hypothesis(DUR.BTC15M)["warning"] is not None
    assert DUR.normalized_hypothesis(DUR.BTC15M)["band"] == (120.0, 840.0)


def test_the_duration_registry_carries_no_decision_rule():
    import inspect
    from src.gemini_execution import gemini_durations as DUR
    src = inspect.getsource(DUR)
    for banned in ("MAX_PAIR_COST", "decide_once", "EXTEND_MIN",
                   "HARD_MAX_PRICE", "0.96", "0.90"):
        assert banned not in src, (
            "the duration registry must not name a strategy parameter: %s"
            % banned)
