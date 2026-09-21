"""P0a coherent authoritative state, and P0b terminal order withdrawal.

WHAT C6 PROVED, to the millisecond:

    22:12:44.125  YES 0.01 @ 0.21 FILLED
    22:12:44.392  active_orders no longer lists it  (267ms later)
                  order_history(filled) still returns nothing
    22:12:45.110  cycle 8 decides on yes=0/no=0 and emits OPEN

The two halves of the authoritative read do not share a clock, and inventory
came from the slower half. The controller was handed a flat book 985ms after
a real fill and did exactly what a flat book implies.

WHAT C5 AND C6 PROVED ABOUT STOPPING: C5 stopped fail-closed and one of its
resting orders filled 57 seconds later; C6 stopped on budget with NO 0.69
live for a further 2m13s. STOP stopped the controller. It never stopped the
orders.
"""
from __future__ import annotations

import asyncio

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import spec
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC05M2609182215-UP"
YES_OID = "145828836251359298"          # the order that really filled
YES_COID = "l5cp-owner-2026-yes-21bd01c4"
NO_OID = "145828836251363146"           # the order left live at termination
NO_COID = "l5cp-owner-2026-no-c2dd1a33"

#: verbatim shapes: the listing returns orderId as an INT
ACTIVE_YES = {"orderId": int(YES_OID), "clientOrderId": YES_COID,
              "symbol": SYMBOL, "outcome": "yes", "price": "0.21",
              "quantity": "0.01", "status": "open"}
ACTIVE_NO = {"orderId": int(NO_OID), "clientOrderId": NO_COID,
             "symbol": SYMBOL, "outcome": "no", "price": "0.69",
             "quantity": "0.01", "status": "open"}
FILLED_YES_ROW = {"orderId": int(YES_OID), "clientOrderId": YES_COID,
                  "symbol": SYMBOL, "outcome": "yes", "price": "0.21",
                  "quantity": "0.01", "filledQuantity": "0.01",
                  "remainingQuantity": "0", "status": "filled"}
CANCEL_ACK = {"id": "x", "status": 200,
              "result": {"orderId": NO_OID, "symbol": SYMBOL,
                         "status": "CANCELLED"}}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
        ).run_until_complete(coro)


class Session:
    def __init__(self, acks=None):
        self.acks = list(acks or [])
        self.sent = []
        self.events = None

    async def request(self, method, params, **kw):
        self.sent.append((method, params))
        return self.acks.pop(0) if self.acks else CANCEL_ACK


def _driver(tmp_path, t=[1000.0]):
    led = tmp_path / "l.jsonl"
    led.write_text("", encoding="utf-8")
    clock = {"t": 1000.0}

    def now():
        clock["t"] += 1.0
        return clock["t"]
    d = WC.Driver("AUTH-COH", SYMBOL, ledger_path=str(led), now_fn=now,
                  max_actions=6)
    d.inv = CP.Inventory()
    d.state = WC.AUTHORIZED
    return d


# ================================================ P0a THE C6 TIMING, REPLAYED

def test_the_controller_is_not_called_during_the_split_read_ambiguity(
        tmp_path, monkeypatch):
    """REQUIREMENT 2. The exact C6 moment: the order is gone from
    active_orders and the fill history has not caught up. The refresh must
    NOT produce a flat snapshot, and `decide` must not be reached."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add(YES_COID, leg="YES", price="0.21", qty="0.01", now=1000.0,
                  venue_order_id=YES_OID)

    async def refresh():                       # the ambiguity, forever
        return {"fills": [], "open_orders": [], "orders_seen": [],
                "terminal_seen": [], "trades": []}

    decided = []
    monkeypatch.setattr(d, "decide", lambda *a, **k: decided.append(1))
    out = _run(d._refresh(refresh))

    assert out is None, "an incoherent snapshot must not be handed on"
    assert d.state == WC.UNKNOWN_TERMINAL
    assert YES_COID in d.why
    assert decided == [], "the controller must never see the flat book"
    # and inventory was NOT quietly rewritten to flat
    assert d.inv.report()["n_fills"] == 0 and d.inv.report()["yes_qty"] == "0"


def test_the_fill_is_applied_once_the_venue_explains_it(tmp_path, monkeypatch):
    """REQUIREMENT 1. Re-read until the venue answers, then hand the
    controller a COHERENT snapshot -- and record what L5 actually says."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add(YES_COID, leg="YES", price="0.21", qty="0.01", now=1000.0,
                  venue_order_id=YES_OID)
    d.guard = CP.WindowGuard(SYMBOL, auth_id="AUTH-COH")
    d.state = WC.BOUND

    calls = {"n": 0}

    async def refresh():
        calls["n"] += 1
        if calls["n"] < 3:                     # the 985ms of C6, as reads
            return {"fills": [], "open_orders": [], "orders_seen": [],
                    "terminal_seen": [], "trades": []}
        return {"fills": [{"leg": "YES", "qty": "0.01", "price": "0.21"}],
                "open_orders": [], "orders_seen": [FILLED_YES_ROW],
                "terminal_seen": [], "trades": []}

    out = _run(d._refresh(refresh))
    assert out is not None and out["coherent"] is True
    assert out["reconcile_attempts"] == 3
    assert d.state == WC.BOUND, "a resolved ambiguity is not a stop"

    inv = d.inv.report()
    assert inv["yes_qty"] == "0.01" and inv["no_qty"] == "0"
    assert inv["total_spend_usd"] == "0.0021"
    assert inv["directional_residual"] == "0.01"
    assert inv["inventory_imbalance"] == "0.01"
    assert inv["n_fills"] == 1

    # ONLY NOW is the unchanged controller called. This is the action C6
    # could not reach.
    book = {"readable": True, "identity_consistent": True,
            "yes": {"bid": "0.20", "ask": "0.23"},
            "no": {"bid": "0.77", "ask": "0.80"}}
    turn = d.decide(150.0, book=book)
    labels = {a["leg"]: a["action"] for a in (turn["turn"]["actions"] or [])}
    assert labels, "the controller must produce a surface from real inventory"
    assert labels.get("NO") == CP.COMPLETE, labels
    assert labels.get("YES") != CP.OPEN, labels


def test_applying_the_same_fill_twice_changes_nothing(tmp_path, monkeypatch):
    """REQUIREMENT 3. Idempotent BY CONSTRUCTION: every refresh rebuilds
    inventory from the venue's whole fill list, so there is no 'already
    seen' set to drift."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)

    async def refresh():
        return {"fills": [{"leg": "YES", "qty": "0.01", "price": "0.21"}],
                "open_orders": [], "orders_seen": [FILLED_YES_ROW],
                "terminal_seen": [], "trades": []}

    first = _run(d._refresh(refresh))
    snap = d.inv.report()
    for _ in range(4):
        _run(d._refresh(refresh))
    assert d.inv.report() == snap, "a re-read must not double-count a fill"
    assert snap["yes_qty"] == "0.01" and snap["n_fills"] == 1
    assert first["coherent"] is True


def test_a_terminal_row_explains_a_disappearance_without_inventing_a_fill(
        tmp_path, monkeypatch):
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add(NO_COID, leg="NO", price="0.69", qty="0.01", now=1000.0,
                  venue_order_id=NO_OID)

    async def refresh():
        return {"fills": [], "open_orders": [], "orders_seen": [],
                "terminal_seen": [{"orderId": int(NO_OID),
                                   "clientOrderId": NO_COID,
                                   "status": "closed", "filledQuantity": "0"}],
                "trades": []}

    out = _run(d._refresh(refresh))
    assert out is not None and out["coherent"] is True
    assert d.inv.report()["n_fills"] == 0, "a cancelled order is not a fill"
    assert d.state == WC.AUTHORIZED


def test_the_trade_record_can_explain_it_when_order_history_has_not(
        tmp_path, monkeypatch):
    """/v1/mytrades is a DIFFERENT read from order history, and it is the
    FASTER one.

    STRENGTHENED AFTER C8B. It is no longer enough that the disappearance is
    explained: the inventory must carry the execution in the SAME refresh.
    C8B had `coherent: True` beside an inventory that still read flat,
    because the gate looked at trades and `recover` looked at filled history.
    """
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add(YES_COID, leg="YES", price="0.21", qty="0.01", now=1000.0,
                  venue_order_id=YES_OID)
    # what WE placed -- the only thing that can side a trades-only execution,
    # since /v1/mytrades carries no outcome field
    d._remember_leg(YES_COID, "YES", YES_OID)

    async def refresh():
        return {"fills": [], "open_orders": [], "orders_seen": [],
                "terminal_seen": [],
                "trades": [{"order_id": YES_OID, "client_order_id": YES_COID,
                            "price": "0.21", "amount": "0.01",
                            "tid": 991, "aggressor": False,
                            "fee_amount": "0"}]}

    out = _run(d._refresh(refresh))
    assert out is not None and out["coherent"] is True
    # THE POINT: the same refresh that called it coherent carries the fill
    rep = d.inv.report()
    assert rep["yes_qty"] == "0.01" and rep["n_fills"] == 1
    assert rep["directional_residual"] == "0.01"
    assert out["n_fills"] == 1


def test_an_execution_whose_LEG_cannot_be_established_fails_closed(
        tmp_path, monkeypatch):
    """A trade row states price, quantity, fee and aggressor -- and NOT the
    outcome. Our client ids happen to contain "-yes-" and "-no-", and reading
    them would work right up until the day it silently did not. So an
    execution nobody can side is a CONTRADICTION, never a guess."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add(YES_COID, leg="YES", price="0.21", qty="0.01", now=1000.0,
                  venue_order_id=YES_OID)
    # deliberately NOT remembered, and order history has not caught up

    async def refresh():
        return {"fills": [], "open_orders": [], "orders_seen": [],
                "terminal_seen": [],
                "trades": [{"order_id": YES_OID, "client_order_id": YES_COID,
                            "price": "0.21", "amount": "0.01", "tid": 992}]}

    out = _run(d._refresh(refresh))
    assert out is None
    assert d.state in WC.TERMINAL
    assert "contradict" in (d.why or "")


def test_an_unreadable_read_still_stops_rather_than_reading_as_empty(
        tmp_path, monkeypatch):
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)

    async def refresh():
        return {"fills": None, "open_orders": None}

    assert _run(d._refresh(refresh)) is None
    assert d.state == WC.DO_NOT_TRADE


def test_a_still_resting_order_is_not_a_disappearance():
    known = {YES_COID: {"client_order_id": YES_COID,
                        "venue_order_id": YES_OID, "leg": "YES",
                        "price": "0.21"}}
    assert WC.unexplained_disappearances(
        known, {"open_orders": [ACTIVE_YES], "orders_seen": []}) == []


# ============================================ P0b TERMINAL ORDER WITHDRAWAL

def test_cleanup_cancels_what_is_resting_and_proves_it_gone(tmp_path,
                                                            monkeypatch):
    """REQUIREMENT 4. Cancel, then let the VENUE say they are gone."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add(NO_COID, leg="NO", price="0.69", qty="0.01", now=1000.0,
                  venue_order_id=NO_OID)
    session = Session()

    async def refresh():
        return {"fills": [], "open_orders": [], "orders_seen": [],
                "terminal_seen": [], "trades": []}

    rep = _run(d.withdraw_orders(session, refresh))
    assert rep["state"] == WC.STOPPED_FLAT_ORDERS
    assert rep["verdict"] == "CLEANUP_CONFIRMED"
    assert rep["remaining"] == []
    assert d.state == WC.STOPPED_FLAT_ORDERS
    # the canonical STRING, the C5 lesson, on the cleanup path too
    assert session.sent[0][1] == {"orderId": NO_OID}
    assert isinstance(session.sent[0][1]["orderId"], str)
    # SAFETY ONLY: it cancels, it never places
    assert all(m == spec.WS_CANCEL_METHOD for m, _ in session.sent)


def test_cleanup_is_budgeted_apart_from_the_strategy_slots(tmp_path,
                                                           monkeypatch):
    """C6 ended with ZERO strategy slots. A cleanup that borrowed from that
    budget could never run when it is most needed."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.budget_consumed = 6                      # strategy budget exhausted
    assert d.budget_left() == 0
    d.working.add(NO_COID, leg="NO", price="0.69", qty="0.01", now=1000.0,
                  venue_order_id=NO_OID)
    session = Session()

    async def refresh():
        return {"fills": [], "open_orders": [], "orders_seen": [],
                "terminal_seen": [], "trades": []}

    rep = _run(d.withdraw_orders(session, refresh))
    assert rep["state"] == WC.STOPPED_FLAT_ORDERS
    assert d.cleanup_consumed == 1
    assert d.budget_left() == 0, "cleanup must not touch the strategy budget"
    # the ledger's STRATEGY-slot count is untouched by a safety cancel: the
    # cleanup row exists but consumed() does not count it
    assert d.ledger.consumed() == 0, "a safety cancel is not a strategy slot"
    assert len(d.ledger.rows) >= 1
    kinds = [r["kind"] for r in d.ledger.rows]
    assert WC.CLEANUP_CANCEL in kinds


def test_cleanup_that_cannot_confirm_stays_visibly_non_inert(tmp_path,
                                                             monkeypatch):
    """REQUIREMENT 5. The order will not go away; the result must say so and
    must never be described as inert."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add(NO_COID, leg="NO", price="0.69", qty="0.01", now=1000.0,
                  venue_order_id=NO_OID)
    session = Session()

    async def refresh():                       # the venue keeps showing it
        return {"fills": [], "open_orders": [ACTIVE_NO], "orders_seen": [],
                "terminal_seen": [], "trades": []}

    rep = _run(d.withdraw_orders(session, refresh))
    assert rep["state"] == WC.STOPPED_WITH_LIVE_ORDERS
    assert rep["verdict"] == WC.CLEANUP_UNCONFIRMED
    assert rep["remaining"] and rep["remaining"][0]["client_order_id"] == NO_COID
    assert "EXECUTABLE" in rep["ALARM"]
    assert d.state == WC.STOPPED_WITH_LIVE_ORDERS
    # BOUNDED: it does not spin forever
    assert d.cleanup_consumed <= WC.MAX_CLEANUP_MUTATIONS


def test_cleanup_cannot_create_exposure():
    """Structural: the cleanup path composes no order and no sell."""
    src = open("src/gemini_execution/ws_continuous.py",
               encoding="utf-8").read()
    body = src.split("async def withdraw_orders", 1)[1].split(
        "\n    # ------------------------------------------------------------- the window",
        1)[0]
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    # the labels string in the report NAMES what it does not do, so only
    # call-shaped tokens are scanned
    for forbidden in ("build_ws_order", "WS_PLACE_METHOD", "self.place(",
                      "apply_fill", "diff_surface", "state_from_venue",
                      "guard.step"):
        assert forbidden not in code, forbidden
    # and the only venue method it can reach is the cancel
    methods = [l for l in code.splitlines() if "session.request(" in l]
    assert methods, "cleanup must actually reach the venue"
    cancel_body = src.split("async def _cleanup_cancel", 1)[1][:2000]
    assert "spec.WS_CANCEL_METHOD" in cancel_body
    assert "WS_PLACE_METHOD" not in cancel_body


def test_a_window_report_always_says_whether_anything_is_still_live():
    """`inert` is computed in _finish, so no report can omit it."""
    src = open("src/gemini_execution/ws_continuous.py",
               encoding="utf-8").read()
    assert 'report["orders_live_after_stop"]' in src
    assert 'report["inert"]' in src
    assert "FLAT ORDERS is not flat INVENTORY" in src


def test_the_envelope_did_not_move():
    # THE ENVELOPE MOVED, DELIBERATELY, on 2026-09-21: the q=1 LIVE bounds
    # joined the hash, so b3bff654... describes an envelope that no longer
    # exists. Pinning it here would now assert that no reviewed change may
    # ever land. The two invariants worth pinning are kept and are stronger:
    # the SHADOW (0.01) bounds did not move, and the authorization granted
    # against the old envelope is dead rather than silently carried forward.
    assert WC.envelope_sha() != (
        "b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17")
    # ARMED 2026-09-21 under OWNER-2026-09-21-GEMINI-Q1-LIVE-1. The grant
    # names the envelope ACTUALLY IN FORCE, so a changed bound still kills it
    # automatically, and the dual key's live half remains outside the repo.
    assert WC.AUTHORIZED_ENVELOPE == WC.envelope_sha()
    assert WC.authorized() is False
    assert str(CP.MAX_ACTIONS) == "12" and str(CP.ACTION_SIZE) == "0.01"
    assert str(CP.MAX_RESIDUAL) == "0.04" and str(CP.MAX_IMBALANCE) == "0.04"
    assert str(CP.MAX_TOTAL_SPEND) == "0.15"
    # cleanup has its own bound and it is NOT an envelope bound
    assert WC.MAX_CLEANUP_MUTATIONS == 8 and WC.MAX_RECONCILE_ATTEMPTS == 6
