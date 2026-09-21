"""The CONTINUOUS-5 defect: one orderId, two types, one rejected cancel.

WHAT HAPPENED. 2026-09-18T21:05:32Z the driver sent

    order.cancel {"orderId": 145828836250441342}      <- INT

and Gemini answered 400 / -1013 "Invalid parameters". The order stayed
resting and the window failed closed with three of six slots spent.

WHY. The venue publishes the same id in two types. `order.place`'s ack
returns it as a STRING; the open-orders LISTING returns it as an INT. Cycle 1
placed, so the working set held the ack's string. Cycle 2's authoritative
refresh rebuilt the working set from the listing, which replaced the string
with the int, and the cancel sent what the set held.

It could not have been seen before: CONTINUOUS-4 never refreshed mid-window,
so its working set only ever held place-ack strings. The reconciliation fix
is what first routed a listing-sourced id into a cancel.

THE FIX IS AT THE NORMALIZATION BOUNDARY, not at the cancel. A cancel that
repaired its own argument would leave the wrong shape in the working set for
everything else to read.
"""
from __future__ import annotations

import asyncio

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import spec
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC05M2609182110-UP"

#: the id at the centre of it, in the two shapes the venue really published
CANON = "145828836250441342"
FROM_PLACE_ACK = "145828836250441342"        # verbatim: a STRING
FROM_LISTING = 145828836250441342            # verbatim: an INT

#: the C5 cycle-2 listing, as `/v1/prediction-markets/orders` returned it
C5_LISTING = [
    {"orderId": 145828836250441357, "clientOrderId": "l5cp-owner-2026-no-bc908af5",
     "symbol": SYMBOL, "side": "buy", "outcome": "no", "price": "0.46",
     "quantity": "0.01", "filledQuantity": "0", "status": "open",
     "timeInForce": "maker-or-cancel"},
    {"orderId": FROM_LISTING, "clientOrderId": "l5cp-owner-2026-yes-1bc7dd72",
     "symbol": SYMBOL, "side": "buy", "outcome": "yes", "price": "0.26",
     "quantity": "0.01", "filledQuantity": "0", "status": "open",
     "timeInForce": "maker-or-cancel"},
]

CANCEL_ACK_CONFIRMED = {
    "id": "l5cp-x", "status": 200,
    "result": {"orderId": CANON, "symbol": SYMBOL, "status": "CANCELLED"}}
CANCEL_ACK_1013 = {
    "id": "l5cp-x", "status": 400,
    "error": {"code": -1013, "msg": "Invalid parameters"}}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
        ).run_until_complete(coro)


class ScriptedSession:
    def __init__(self, acks):
        self.acks = list(acks)
        self.sent = []
        self.events = None

    async def request(self, method, params, **kw):
        self.sent.append((method, params))
        return self.acks.pop(0)


def _driver(tmp_path):
    led = tmp_path / "led.jsonl"
    led.write_text("", encoding="utf-8")
    d = WC.Driver("AUTH-C5FIX", SYMBOL, ledger_path=str(led),
                  now_fn=lambda: 1000.0, max_actions=6)
    d.inv = CP.Inventory()
    d.state = WC.AUTHORIZED
    return d


# ============================================ ONE ID, ONE REPRESENTATION

def test_both_observed_venue_shapes_canonicalize_to_the_same_string():
    """The whole defect in two lines."""
    assert WC.canonical_order_id(FROM_PLACE_ACK) == CANON
    assert WC.canonical_order_id(FROM_LISTING) == CANON
    assert (WC.canonical_order_id(FROM_PLACE_ACK)
            == WC.canonical_order_id(FROM_LISTING))
    assert isinstance(WC.canonical_order_id(FROM_LISTING), str)


def test_the_digits_are_never_touched():
    """REPRESENTATION ONLY. Nothing is parsed and re-emitted, because
    re-emitting is how a float turns this id into 1.4582883625044134e+17."""
    assert WC.canonical_order_id(FROM_LISTING) == str(FROM_LISTING)
    # a string is returned as it came, so a leading zero survives and a
    # future non-numeric id is not rejected for being unfamiliar
    assert WC.canonical_order_id("0145828836250441342") == "0145828836250441342"
    assert WC.canonical_order_id("ORD-7f3a") == "ORD-7f3a"
    assert WC.canonical_order_id("  %s  " % CANON) == CANON


@pytest.mark.parametrize("bad", [
    None,                       # missing
    "",                         # empty
    "   ",                      # blank
    True, False,                # boolean -- and True is an int in Python
    0, -1,                      # not an identifier
    "0", "-4",                  # same, as text
    1.45828836250441e17,        # a float has already lost the digits
    {"orderId": CANON},         # structured
    [CANON],
])
def test_an_unusable_id_is_refused_rather_than_invented(bad):
    with pytest.raises(WC.ReconcileRequired):
        WC.canonical_order_id(bad)


# ================================================== AT THE WRITE BOUNDARY

def test_reconcile_canonicalizes_the_listings_integer():
    """REQUIREMENT 1. The venue's listing is where the int came from."""
    w = WC.WorkingOrders()
    note = w.reconcile(C5_LISTING, now=1000.0)
    assert "2 resting" in note
    ids = {r["client_order_id"]: r["venue_order_id"] for r in w.rungs()}
    assert ids["l5cp-owner-2026-yes-1bc7dd72"] == CANON
    assert ids["l5cp-owner-2026-no-bc908af5"] == "145828836250441357"
    assert all(isinstance(v, str) for v in ids.values())


def test_add_canonicalizes_too_so_both_write_paths_agree():
    """REQUIREMENT 4. The place-ack path and the listing path must produce
    the same working-set row, or the defect simply moves."""
    a, b = WC.WorkingOrders(), WC.WorkingOrders()
    a.add("coid", leg="YES", price="0.26", qty="0.01", now=1000.0,
          venue_order_id=FROM_PLACE_ACK)
    b.reconcile([{"orderId": FROM_LISTING, "clientOrderId": "coid",
                  "outcome": "yes", "price": "0.26", "quantity": "0.01",
                  "resting_since": 1000.0}], now=1000.0)
    assert a.rungs()[0]["venue_order_id"] == b.rungs()[0]["venue_order_id"]
    assert a.rungs()[0]["venue_order_id"] == CANON


def test_add_still_accepts_a_missing_id_because_recovery_depends_on_it():
    """A place ack that carried no orderId leaves the order resting with its
    id unknown, and `cancel` already sends the operator to the venue's own
    open-order list. Refusing here would delete a recovery path that works."""
    w = WC.WorkingOrders()
    w.add("coid", leg="YES", price="0.26", qty="0.01", now=1000.0)
    assert w.rungs()[0]["venue_order_id"] is None


def test_a_resting_order_with_an_unusable_id_stops_the_window():
    """REQUIREMENT 3, at the boundary that matters: an order the VENUE says
    is resting must be addressable. Skipping it would hide a live order from
    the controller, which is worse than stopping."""
    w = WC.WorkingOrders()
    with pytest.raises(WC.ReconcileRequired):
        w.reconcile([{"orderId": "", "clientOrderId": "coid",
                      "outcome": "yes", "price": "0.26", "quantity": "0.01"}],
                    now=1000.0)


def test_presence_beats_truthiness_on_the_id_key():
    """`o.get("orderId") or o.get("order_id")` fell through to the second key
    whenever the first was 0 or "" -- which is how an unusable id quietly
    becomes a plausible-looking one."""
    w = WC.WorkingOrders()
    with pytest.raises(WC.ReconcileRequired):
        w.reconcile([{"orderId": 0, "order_id": FROM_LISTING,
                      "clientOrderId": "coid", "outcome": "yes",
                      "price": "0.26", "quantity": "0.01"}], now=1000.0)


# ==================================================== WHAT GOES ON THE WIRE

def test_cancel_emits_the_string_and_never_the_integer(tmp_path, monkeypatch):
    """REQUIREMENT 5, asserted on the frame itself."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.reconcile(C5_LISTING, now=1000.0)
    order = next(r for r in d.working.rungs() if r["leg"] == "YES")
    session = ScriptedSession([CANCEL_ACK_CONFIRMED])
    _run(d.cancel(session, order))

    method, params = session.sent[0]
    assert method == spec.WS_CANCEL_METHOD
    assert params == {"orderId": CANON}
    assert isinstance(params["orderId"], str)
    assert params["orderId"] is not FROM_LISTING
    assert not isinstance(params["orderId"], int)


def test_the_c5_cycle_2_sequence_now_sends_a_cancel_the_venue_accepts(
        tmp_path, monkeypatch):
    """REQUIREMENT 6. The exact chronology that failed, replayed.

    Authoritative refresh rebuilds the working YES order FROM THE LISTING ->
    the controller wants YES 0.38 and keeps NO 0.46 -> diff gives KEEP NO,
    CANCEL YES 0.26, PLACE YES 0.38 -> the cancel carries the canonical
    string. Same prices, same plan, same KEEP; only the id's shape changed.
    """
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)

    # 1. the authoritative refresh, from the venue's own listing
    d.working.reconcile(C5_LISTING, now=1000.0)

    # 2. the surface cycle 2 actually decided
    desired = [{"leg": "YES", "price": "0.38", "qty": "0.01",
                "action": CP.OPEN},
               {"leg": "NO", "price": "0.46", "qty": "0.01",
                "action": CP.OPEN}]
    plan = WC.diff_surface(desired, d.working)

    # 3. the same plan as the live window: KEEP the unchanged NO, cancel and
    #    re-place the YES. KEEP costs no slot -- unchanged by this fix.
    assert [k["order"]["leg"] for k in plan["keep"]] == ["NO"]
    assert [c["order"]["price"] for c in plan["cancel"]] == ["0.26"]
    assert [p["rung"]["price"] for p in plan["place"]] == ["0.38"]
    assert plan["mutations"] == 2

    # 4. and the cancel that killed the window now carries the string
    session = ScriptedSession([CANCEL_ACK_CONFIRMED])
    _run(d.cancel(session, plan["cancel"][0]["order"]))
    assert session.sent[0][1] == {"orderId": CANON}


# ======================================================== STILL FAIL-CLOSED

def test_a_400_is_still_unknown_and_still_stops_the_window(tmp_path,
                                                           monkeypatch):
    """REQUIREMENT 7. The fix must not turn a rejection into a shrug."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.reconcile(C5_LISTING, now=1000.0)
    order = next(r for r in d.working.rungs() if r["leg"] == "YES")
    session = ScriptedSession([CANCEL_ACK_1013])
    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(session, order))
    assert d.state == WC.RECONCILE
    rows = d.ledger.rows
    assert rows[-1]["kind"] == WC.CANCEL and rows[-1]["outcome"] == WC.UNKNOWN
    # and the order it could not cancel is STILL in the working set
    assert any(r["leg"] == "YES" for r in d.working.rungs())


def test_an_empty_200_result_is_still_unconfirmed(tmp_path, monkeypatch):
    """CONTINUOUS-4's defect must stay fixed: a 200 with an empty result is a
    receipt, not a cancellation."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.reconcile(C5_LISTING, now=1000.0)
    order = next(r for r in d.working.rungs() if r["leg"] == "YES")
    session = ScriptedSession([{"id": "x", "status": 200,
                                "result": {"orderId": "", "symbol": "",
                                           "status": ""}}])
    _run(d.cancel(session, order))
    assert any(r["leg"] == "YES" for r in d.working.rungs()), (
        "an unconfirmed cancel must leave the order in the working set")


def test_unreadable_open_orders_are_still_not_an_empty_book():
    w = WC.WorkingOrders()
    with pytest.raises(WC.ReconcileRequired):
        w.reconcile(None, now=1000.0)


def test_a_missing_venue_id_still_sends_us_to_the_venue_not_to_a_guess(
        tmp_path, monkeypatch):
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add("coid", leg="YES", price="0.26", qty="0.01", now=1000.0)
    session = ScriptedSession([CANCEL_ACK_CONFIRMED])
    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(session, d.working.rungs()[0]))
    assert session.sent == [], "no frame may go out without an id"
    assert d.state == WC.RECONCILE


# ============================================================ NOTHING ELSE

def test_the_envelope_and_the_controller_did_not_move():
    """REQUIREMENT 8. A representation fix touches no bound and no price."""
    # THE ENVELOPE MOVED, DELIBERATELY, on 2026-09-21: the q=1 LIVE bounds
    # joined the hash, so b3bff654... describes an envelope that no longer
    # exists. Pinning it here would now assert that no reviewed change may
    # ever land. The two invariants worth pinning are kept and are stronger:
    # the SHADOW (0.01) bounds did not move, and the authorization granted
    # against the old envelope is dead rather than silently carried forward.
    assert WC.envelope_sha() != (
        "b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17")
    assert WC.AUTHORIZED_ENVELOPE is None and not WC.authorized()
    assert str(CP.MAX_ACTIONS) == "12" and str(CP.ACTION_SIZE) == "0.01"
    assert str(CP.MAX_TOTAL_SPEND) == "0.15"
    assert str(CP.MAX_RESIDUAL) == "0.04" and str(CP.MAX_IMBALANCE) == "0.04"
    assert str(CP.MAX_YES) == "0.10" and str(CP.MAX_NO) == "0.10"
    # and no pair-cost ceiling appeared while we were in here
    inv = CP.Inventory()
    inv.apply_fill("NO", "0.01", "0.14", source="test")
    assert CP.would_breach("YES", "0.91", inv) is None
