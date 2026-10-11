"""CANCELLATION / DEADMAN: the persistent order lifecycle.

THE REQUIRED LIFECYCLE

    SUBMITTED -> RESTING -> FILLED
or  SUBMITTED -> RESTING -> cancellation requested -> CANCEL_CONFIRMED
                        -> authoritative fill reconciliation -> terminal

THE ONE THAT IS EASY TO GET WRONG. `transport.cancel_and_confirm` reports
CANCEL_CONFIRMED when the order is GONE FROM THE ACTIVE BOOK. An order can
leave that book by FILLING. So "cancel acked" is not "did not fill", and
reading it that way is the same disappearance fallacy that cost CI15M-4 its
fill, one surface along. The authoritative terminal row is therefore read
AFTER the cancel ack, and a fill found there is carried as inventory.

An UNKNOWN cancel outcome HALTs. It never reports flat.

NO REPRICING AND NO CHASING: nothing is resubmitted, and no price is
consulted anywhere in the cancel path.

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

CID = "CI15M-CANCEL"
T1, T2 = "BTC15M2610040000", "BTC15M2610040015"
S1 = "GEMI-%s-UP" % T1
OID = 145828836725658335


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    BUD.create(CID, root=root, profile=BUD.FULL)
    BUD.open_window(CID, T1, root=root)
    BUD.consume_slot(CID, T1, {"action": "OPEN", "side": "UP",
                               "outcome": "yes", "price": "0.68",
                               "quantity": "1",
                               "unmatched_shares_after": "1",
                               "unmatched_principal_after": "0.68",
                               BUD.RECONCILED_KEY: "0"},
                     root=root, remote=None)
    BUD.record_status(CID, T1, slot=0, status="SUBMITTED",
                      detail={"venue": {"body": {
                          "orderId": OID, "symbol": S1, "status": "open",
                          "filledQuantity": "0", "price": "0.68",
                          "outcome": "yes"}}}, root=root)
    return root


async def _nosleep(_s):
    return None


def _no_writes(monkeypatch):
    async def boom(**kw):
        raise AssertionError("THE VENUE WRITE PATH WAS CONTACTED")
    monkeypatch.setattr(transport, "place", boom)


def _bucket(status, rows, *, http=200, unreadable=False):
    if unreadable:
        return {"status_filter": status, "http": http, "rows": None,
                "unreadable": True}
    return {"status_filter": status, "http": http, "rows": len(rows),
            "orders": list(rows)}


def _hist(buckets):
    bad = [b["status_filter"] for b in buckets if b.get("unreadable")]
    total = sum(b.get("rows") or 0 for b in buckets)
    return {"readable": not bad,
            "verdict": "UNREADABLE" if bad else (
                "ORDERS_FOUND" if total else "NO_ORDERS_ON_RECORD"),
            "total_rows": total, "unreadable_statuses": bad,
            "per_status": buckets}


def _row(status, filled, qty="1"):
    return {"orderId": OID, "symbol": S1, "side": "buy", "outcome": "yes",
            "status": status, "quantity": qty, "filledQuantity": filled,
            "remainingQuantity": str(D(qty) - D(filled)), "price": "0.68",
            "avgExecutionPrice": ("0.68" if D(filled) > 0 else None)}


def _venue(monkeypatch, *, hist, cancel_state="CANCEL_CONFIRMED",
           trades=None, cancel_raises=False):
    async def h(**kw):
        return hist

    async def my(sym):
        return {"rows": list(trades or []), "status": 200}

    async def pos():
        return {"ok": True, "rows": [], "status": 200, "total": 0}

    async def can(order_id, symbol, **kw):
        if cancel_raises:
            raise RuntimeError("cancel endpoint unreachable")
        return {"state": cancel_state, "order_id": order_id,
                "cancel": {"ok": cancel_state == "CANCEL_CONFIRMED"},
                "why": None if cancel_state == "CANCEL_CONFIRMED"
                       else "the venue accepted nothing readable"}
    monkeypatch.setattr(metadata, "order_history", h)
    monkeypatch.setattr(P, "_mytrades", my)
    monkeypatch.setattr(metadata, "positions", pos)
    monkeypatch.setattr(transport, "cancel_and_confirm", can)


TRADE_FULL = {"tid": 1, "order_id": str(OID), "symbol": S1, "price": "0.68",
              "amount": "1", "fee_amount": "0.02", "aggressor": True,
              "timestampms": 1}
TRADE_PARTIAL = dict(TRADE_FULL, amount="1")


# =====================================================================
# 1. the clean cancel: flat, and ONLY on the venue's filled quantity
# =====================================================================
def test_a_clean_cancel_is_flat_on_the_authoritative_row(env, monkeypatch):
    _no_writes(monkeypatch)
    _venue(monkeypatch,
           hist=_hist([_bucket("filled", []),
                       _bucket("cancelled", [_row("cancelled", "0")])]))
    got = asyncio.run(P.cancel_and_reconcile(CID, S1, T1, OID, root=env))
    assert got["ok"] is True
    assert got["state"] == P.CANCEL_CONFIRMED
    assert got["filled"] == "0"
    assert got["terminal"]["state"] == P.TERMINAL_CANCELLED
    assert BUD.halted(CID, root=env) is None


# =====================================================================
# 2. FILL-VS-CANCEL RACE: the cancel acks, but the order had filled
# =====================================================================
def test_the_fill_vs_cancel_race_carries_the_fill_not_the_cancel(env,
                                                                 monkeypatch):
    """THE CASE THAT MATTERS MOST. The cancel is confirmed -- the order is
    gone from the active book -- because it FILLED. Inventory is the fill."""
    _no_writes(monkeypatch)
    _venue(monkeypatch,
           hist=_hist([_bucket("filled", [_row("filled", "1")]),
                       _bucket("cancelled", [])]),
           trades=[TRADE_FULL])
    got = asyncio.run(P.cancel_and_reconcile(CID, S1, T1, OID, root=env))
    assert got["state"] == P.CANCEL_RACED_FILL
    assert got["ok"] is True
    assert got["filled"] == "1"
    assert got["reconciled"]["ok"] is True
    assert got["reconciled"]["signed"] == "1", (
        "a confirmed cancel must NOT be allowed to report flat when the "
        "venue says the order filled")
    assert BUD.open_state(CID, T1, root=env) == BUD.OPEN_FILLED


# =====================================================================
# 3. PARTIAL FILL -> CANCEL: the partial is carried, never rounded away
# =====================================================================
def test_a_partial_fill_before_cancellation_is_carried(env, monkeypatch):
    _no_writes(monkeypatch)
    partial = _row("cancelled", "1", qty="2")   # died holding 1 of 2
    _venue(monkeypatch,
           hist=_hist([_bucket("filled", []), _bucket("cancelled", [partial])]),
           trades=[TRADE_PARTIAL])
    got = asyncio.run(P.cancel_and_reconcile(CID, S1, T1, OID, root=env))
    assert got["state"] == P.CANCEL_RACED_FILL
    assert got["filled"] == "1", "the partial must not round to zero"
    assert got["reconciled"]["signed"] == "1"
    assert BUD.open_state(CID, T1, root=env) == BUD.OPEN_FILLED


# =====================================================================
# 4. CANCEL UNREADABLE / UNKNOWN -> HALT, never flat
# =====================================================================
def test_an_outstanding_cancel_halts_as_unknown(env, monkeypatch):
    _no_writes(monkeypatch)
    _venue(monkeypatch, hist=_hist([_bucket("filled", []),
                                    _bucket("cancelled", [])]),
           cancel_state="OUTSTANDING")
    got = asyncio.run(P.cancel_and_reconcile(CID, S1, T1, OID, root=env))
    assert got["ok"] is False
    assert got["state"] == P.CANCEL_UNKNOWN
    assert "UNKNOWN_POSITION" in str(BUD.halted(CID, root=env))


def test_a_raising_cancel_halts_as_unknown(env, monkeypatch):
    _no_writes(monkeypatch)
    _venue(monkeypatch, hist=_hist([_bucket("filled", []),
                                    _bucket("cancelled", [])]),
           cancel_raises=True)
    got = asyncio.run(P.cancel_and_reconcile(CID, S1, T1, OID, root=env))
    assert got["ok"] is False and got["state"] == P.CANCEL_UNKNOWN
    assert "UNKNOWN_POSITION" in str(BUD.halted(CID, root=env))


def test_a_confirmed_cancel_with_an_unreadable_terminal_row_is_unknown(
        env, monkeypatch):
    """The cancel acked, but we cannot read whether it filled first. That is
    UNKNOWN -- it is NOT flat."""
    _no_writes(monkeypatch)
    _venue(monkeypatch,
           hist=_hist([_bucket("filled", [], unreadable=True, http=503),
                       _bucket("cancelled", [], unreadable=True, http=503)]))
    got = asyncio.run(P.cancel_and_reconcile(CID, S1, T1, OID, root=env))
    assert got["ok"] is False
    assert got["state"] == P.CANCEL_UNKNOWN
    assert "UNKNOWN" in str(got["why"]).upper()


# =====================================================================
# 5. RESTART DURING CANCELLATION
# =====================================================================
def test_restart_during_cancellation_sees_the_request_on_disk(env,
                                                              monkeypatch):
    """The cancel request is durably recorded BEFORE the venue is asked, so
    a process that dies mid-cancel restarts knowing an order is in flight
    and still unresolved."""
    _no_writes(monkeypatch)
    _venue(monkeypatch, hist=_hist([_bucket("filled", []),
                                    _bucket("cancelled", [])]),
           cancel_state="OUTSTANDING")
    asyncio.run(P.cancel_and_reconcile(CID, S1, T1, OID, root=env))

    importlib.reload(BUD)
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [0],
                                        "count": 1, "why": None})
    rows = BUD._rows(BUD._paths(CID, env)["ledger"])
    assert any((r.get("detail") or {}).get("cancel_requested")
               for r in rows if r.get("kind") == "STATUS"), (
        "the cancel request must be on disk before the venue is asked")
    # and the window is STILL unresolved, so the guard will catch it
    assert BUD.open_state(CID, T1, root=env) == BUD.OPEN_SUBMITTED
    assert [w["ticker"] for w in R.unresolved_windows(CID, root=env)] == [T1]


# =====================================================================
# 6. WINDOW ROLLOVER WITH A RESTING PRIOR-WINDOW ORDER
# =====================================================================
def _resting_until_cancelled(monkeypatch, after_row):
    """The REAL deadman shape: while the order rests there is NO terminal
    row to read, so poll_terminal cannot resolve. The cancel is what makes
    a terminal row exist -- and `after_row` is what the venue then says
    actually happened.
    """
    state = {"cancelled": False}

    async def h(**kw):
        if not state["cancelled"]:
            return _hist([_bucket("filled", []), _bucket("cancelled", [])])
        return _hist([_bucket("filled", [after_row]
                              if str(after_row.get("status")) == "filled"
                              else []),
                      _bucket("cancelled", []
                              if str(after_row.get("status")) == "filled"
                              else [after_row])])

    async def act(symbol):
        rows = ([] if state["cancelled"]
                else [{"orderId": OID, "status": "open",
                       "filledQuantity": "0", "symbol": S1}])
        return {"ok": True, "status": 200, "classification": "ACCEPTED",
                "body": {"orders": rows}}

    async def can(order_id, symbol, **kw):
        state["cancelled"] = True
        return {"state": "CANCEL_CONFIRMED", "order_id": order_id,
                "cancel": {"ok": True}}

    async def my(sym):
        return {"rows": ([TRADE_FULL] if state["cancelled"]
                         and str(after_row.get("status")) == "filled"
                         else []), "status": 200}

    async def pos():
        return {"ok": True, "rows": [], "status": 200, "total": 0}
    monkeypatch.setattr(metadata, "order_history", h)
    monkeypatch.setattr(transport, "active_orders", act)
    monkeypatch.setattr(transport, "cancel_and_confirm", can)
    monkeypatch.setattr(P, "_mytrades", my)
    monkeypatch.setattr(metadata, "positions", pos)
    return state


def test_rollover_deadman_cancels_the_prior_window_order(env, monkeypatch):
    """The whole point. T1's order is resting and unresolvable, T2 has
    arrived. With the deadman the executor cancels T1's order, reconciles,
    and is free to roll on -- it neither stops forever nor abandons the
    order."""
    _no_writes(monkeypatch)
    st = _resting_until_cancelled(monkeypatch, _row("cancelled", "0"))
    got = asyncio.run(R.resolve_carry_over(CID, T1, deadman=True, root=env))
    assert st["cancelled"] is True, "the deadman must actually cancel"
    assert got["resolved"] is True
    assert got["state"] == P.CANCEL_CONFIRMED
    assert got["cancel"]["filled"] == "0"
    assert R.unresolved_windows(CID, root=env) == []


def test_rollover_deadman_carries_a_raced_fill_into_inventory(env,
                                                              monkeypatch):
    """Same rollover, but the order had FILLED by the time the cancel
    landed. The deadman must carry that inventory, not report flat."""
    _no_writes(monkeypatch)
    st = _resting_until_cancelled(monkeypatch, _row("filled", "1"))
    got = asyncio.run(R.resolve_carry_over(CID, T1, deadman=True, root=env))
    assert st["cancelled"] is True
    assert got["resolved"] is True
    assert got["state"] == P.CANCEL_RACED_FILL
    assert got["carried_inventory"]["signed"] == "1"
    assert BUD.open_state(CID, T1, root=env) == BUD.OPEN_FILLED


def test_without_the_deadman_a_resting_order_still_blocks_rather_than_cancels(
        env, monkeypatch):
    """The deadman is OPT-IN. Unarmed, the engine still refuses to roll on,
    and it does not cancel anything on its own initiative."""
    _no_writes(monkeypatch)
    cancelled = {"n": 0}

    async def can(order_id, symbol, **kw):
        cancelled["n"] += 1
        return {"state": "CANCEL_CONFIRMED"}
    monkeypatch.setattr(transport, "cancel_and_confirm", can)

    async def h(**kw):
        return _hist([_bucket("filled", []), _bucket("cancelled", [])])

    async def act(symbol):
        return {"ok": True, "status": 200, "classification": "ACCEPTED",
                "body": {"orders": [{"orderId": OID, "status": "open",
                                     "filledQuantity": "0", "symbol": S1}]}}
    monkeypatch.setattr(metadata, "order_history", h)
    monkeypatch.setattr(transport, "active_orders", act)
    got = asyncio.run(R.resolve_carry_over(CID, T1, deadman=False, root=env))
    assert got["resolved"] is False
    assert cancelled["n"] == 0, (
        "an unarmed engine must not cancel on its own initiative")


# =====================================================================
# THE CONTRACT: no chase, no reprice, and shadow cancels nothing
# =====================================================================
def test_the_cancel_path_never_reprices_or_resubmits():
    import inspect
    src = inspect.getsource(P.cancel_and_reconcile)
    for banned in ("transport.place", "_write(", "submit(", "taker_price",
                   "maker_price", "decide_once"):
        assert banned not in src, (
            "the cancel path must not resubmit or consult a price: %s"
            % banned)


def test_shadow_cancel_sends_nothing(monkeypatch):
    async def boom(order_id, symbol, **kw):
        raise AssertionError("THE VENUE CANCEL PATH WAS CONTACTED")
    monkeypatch.setattr(transport, "cancel_and_confirm", boom)
    P.set_cancel_sink(P.shadow_cancel_sink())
    try:
        got = asyncio.run(P._cancel(OID, S1))
        assert got["shadow"] is True
        assert got["state"] == "CANCEL_CONFIRMED"
    finally:
        P.set_cancel_sink(None)


def test_the_frozen_policy_fingerprint_is_untouched_by_cancellation():
    from src.gemini_execution.ci15m_policy import fingerprint
    assert fingerprint() == (
        "5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329")
