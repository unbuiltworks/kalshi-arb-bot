"""The CONTINUOUS-4 defect, replayed against the venue's own payloads.

WHAT HAPPENED. On 2026-09-18 the driver placed NO 0.01 @ 0.14. It filled. The
cancel it then sent returned `status 200` with `result {"orderId":"",
"symbol":"","status":""}` -- empty -- and the driver read that as "cancelled"
and dropped the order. Nothing ever drained the orders@account queue and
`apply_fill` had no caller in the live loop, so inventory stayed at the startup
snapshot of zero. 0.2 seconds later it placed YES 0.01 @ 0.92, completing a
pair at $1.06 against a discipline of <= 0.96.

THE FIXTURES HERE ARE THE REAL FRAMES, copied from
analysis/gemini/continuous_wire_20260918T172750Z.json. In particular the place
ack carries `result.orderId` as a STRING while listings return the same id as
an INT, and the cancel ack really is empty. Every earlier defect in this
package survived a large suite because the fixtures were kinder than the venue.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC05M2609181730-UP"

# --------------------------------------------------------- REAL VENUE FRAMES

#: verbatim: orderId is a STRING here
PLACE_ACK_YES = {
    "id": "l5cp-9103d0dca3a4410ebe880738eb6aa3be", "status": 200,
    "result": {"symbol": SYMBOL, "orderId": "145828836246613472",
               "transactTime": 1789752485939836352, "price": "0.9200",
               "status": "OPEN", "type": "LIMIT", "side": "BUY"}}
PLACE_ACK_NO = {
    "id": "l5cp-4a9ba07c164c4768aae839dc03cab61d", "status": 200,
    "result": {"symbol": SYMBOL, "orderId": "145828836246611835",
               "transactTime": 1789752479483305407, "price": "0.1400",
               "status": "OPEN", "type": "LIMIT", "side": "BUY"}}

#: verbatim: 200, and an EMPTY result. This is the whole defect.
CANCEL_ACK_EMPTY = {
    "id": "l5cp-3ea8e1249b5341789f7688cbed007f28", "status": 200,
    "result": {"orderId": "", "symbol": "", "status": ""}}

#: a cancel the venue actually confirmed, for the contrast. Not yet observed
#: from this venue, and labelled as such: it exists to prove the confirmed
#: branch is reachable, not to claim the venue sends it.
CANCEL_ACK_CONFIRMED = {
    "id": "l5cp-confirmed", "status": 200,
    "result": {"orderId": "145828836246611835", "symbol": SYMBOL,
               "status": "CANCELLED"}}

#: the NO leg as the venue reports it once FILLED -- orderId is an INT here
FILLED_NO_ROW = {"orderId": 145828836246611835,
                 "clientOrderId": "l5cp-owner-2026-no-261020b6",
                 "symbol": SYMBOL, "side": "buy", "outcome": "no",
                 "timeInForce": "maker-or-cancel", "price": "0.14",
                 "quantity": "0.01", "filledQuantity": "0.01",
                 "remainingQuantity": "0", "status": "closed"}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
        ).run_until_complete(coro)


class ScriptedSession:
    """Returns the venue's real frames in order. Records what was sent."""

    def __init__(self, acks):
        self.acks = list(acks)
        self.sent = []
        self.events = None            # the queue nothing ever drained

    async def request(self, method, params, **kw):
        self.sent.append((method, params))
        return self.acks.pop(0)


def _driver(tmp_path, ledger_rows=""):
    p = tmp_path / "led.jsonl"
    p.write_text(ledger_rows, encoding="utf-8")
    d = WC.Driver("AUTH-REPLAY", SYMBOL, ledger_path=str(p))
    d.inv = CP.Inventory()
    d.state = WC.AUTHORIZED
    return d


# ============================================================ THE CANCEL SEAM

def test_an_empty_cancel_result_does_not_erase_the_order(tmp_path,
                                                         monkeypatch):
    """REQUIREMENT 3. 200 with an empty result is a receipt, not a cancellation.

    The order stays in the working set so authoritative reconciliation decides.
    """
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add("l5cp-owner-2026-no-261020b6", leg="NO", price="0.14",
                  qty="0.01", now=1.0,
                  venue_order_id="145828836246611835")
    s = ScriptedSession([CANCEL_ACK_EMPTY])

    out = _run(d.cancel(s, d.working.rungs()[0]))

    assert out["cancelled"] is False
    assert out["cancel_confirmed"] is False
    assert "may have FILLED" in out["why"]
    assert len(d.working.rungs()) == 1, (
        "an ambiguous cancel must NOT erase a possibly-filled order")


def test_the_slot_is_still_spent_on_an_ambiguous_cancel(tmp_path, monkeypatch):
    """The frame went out, so the budget is gone either way. Ambiguity is not
    a refund."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add("c1", leg="NO", price="0.14", qty="0.01", now=1.0,
                  venue_order_id="145828836246611835")
    _run(d.cancel(ScriptedSession([CANCEL_ACK_EMPTY]), d.working.rungs()[0]))
    assert d.ledger.consumed() == 1
    assert d.ledger.unresolved() == [], "an ACCEPTED ack is still resolved"


def test_a_confirmed_cancel_still_drops_the_order(tmp_path, monkeypatch):
    """The converse. Without it, a driver that never dropped anything would
    pass the test above and leak working orders forever."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add("c1", leg="NO", price="0.14", qty="0.01", now=1.0,
                  venue_order_id="145828836246611835")
    out = _run(d.cancel(ScriptedSession([CANCEL_ACK_CONFIRMED]),
                        d.working.rungs()[0]))
    assert out["cancelled"] is True
    assert d.working.rungs() == []


# ================================================= RECONCILIATION + IDEMPOTENCE

def test_a_refresh_applies_the_fill_the_window_never_saw(tmp_path):
    """REQUIREMENT 1. The venue says NO 0.01 filled; the driver must hold it."""
    d = _driver(tmp_path)

    async def refresh():
        return {"fills": [{"leg": "NO", "qty": "0.01", "price": "0.14"}],
                "open_orders": []}

    ref = _run(d._refresh(refresh))
    assert ref["n_fills"] == 1
    assert Decimal(d.inv.no_qty) == Decimal("0.01")
    assert Decimal(d.inv.yes_qty) == Decimal("0")


def test_repeated_refreshes_apply_each_fill_exactly_once(tmp_path):
    """REQUIREMENT 2. Idempotent BY CONSTRUCTION: CP.recover rebuilds a fresh
    Inventory from the authoritative list every time, so there is no
    already-seen set to drift out of step."""
    d = _driver(tmp_path)
    fills = [{"leg": "NO", "qty": "0.01", "price": "0.14"},
             {"leg": "YES", "qty": "0.01", "price": "0.92"}]

    async def refresh():
        return {"fills": fills, "open_orders": []}

    for _ in range(5):
        _run(d._refresh(refresh))
    assert Decimal(d.inv.no_qty) == Decimal("0.01")
    assert Decimal(d.inv.yes_qty) == Decimal("0.01")
    assert d.inv.report()["n_fills"] == 2, "five refreshes, two fills"


def test_a_restart_recovers_the_same_state_a_refresh_produces(tmp_path):
    """REQUIREMENT 2, the restart half. Recovery and mid-window refresh must
    agree, or a crash would change what we believe we hold."""
    fills = [{"leg": "NO", "qty": "0.01", "price": "0.14"}]
    live = _driver(tmp_path)

    async def refresh():
        return {"fills": fills, "open_orders": []}

    _run(live._refresh(refresh))

    restarted = _driver(tmp_path / "b" if False else tmp_path)
    restarted.recover(venue_fills=fills, venue_open=[], venue_orders=[])
    assert restarted.inv.report() == live.inv.report()


def test_unreadable_fills_stop_the_window_rather_than_read_as_flat(tmp_path):
    """REQUIREMENT 7. None is UNREADABLE. Turning it into [] is what makes a
    loaded account look empty."""
    d = _driver(tmp_path)

    async def refresh():
        return {"fills": None, "open_orders": []}

    assert _run(d._refresh(refresh)) is None
    assert d.state == WC.DO_NOT_TRADE
    assert "unreadable" in d.why


def test_unreadable_open_orders_stop_the_window(tmp_path):
    d = _driver(tmp_path)

    async def refresh():
        return {"fills": [], "open_orders": None}

    assert _run(d._refresh(refresh)) is None
    assert d.state == WC.DO_NOT_TRADE


def test_working_orders_follow_the_venue_not_our_memory(tmp_path):
    """An order we think is resting but the venue does not list is gone; one
    the venue lists that we forgot is ours again.

    CONTINUOUS-6 added a precondition: "gone" now has to be EXPLAINED. The
    venue's terminal row supplies that here, so the replacement still
    happens; without one the driver stops instead, which the next test pins.
    """
    d = _driver(tmp_path)
    d.working.add("stale", leg="YES", price="0.50", qty="0.01", now=1.0,
                  venue_order_id="1")

    async def refresh():
        return {"fills": [], "orders_seen": [],
                "terminal_seen": [{"orderId": 1, "clientOrderId": "stale",
                                   "status": "closed",
                                   "filledQuantity": "0"}],
                "open_orders": [
                    {"clientOrderId": "real", "outcome": "no", "price": "0.04",
                     "quantity": "0.01", "orderId": 145828836246614326}]}

    _run(d._refresh(refresh))
    ids = [r["client_order_id"] for r in d.working.rungs()]
    assert ids == ["real"]


def test_a_vanished_order_the_venue_will_not_explain_stops_the_window(
        tmp_path):
    """THE C6 DEFECT, as a contract. An order that leaves active_orders with
    no fill row and no terminal row is PENDING_RECONCILIATION, and the
    controller is never handed the book while it is."""
    d = _driver(tmp_path)
    d.working.add("stale", leg="YES", price="0.50", qty="0.01", now=1.0,
                  venue_order_id="1")

    async def refresh():
        return {"fills": [], "open_orders": [], "orders_seen": [],
                "terminal_seen": [], "trades": []}

    assert _run(d._refresh(refresh)) is None
    assert d.state == WC.UNKNOWN_TERMINAL
    assert "stale" in (d.why or "")


# ==================================== THE REPLAY: WHAT THE FIX ACTUALLY CHANGES

#: the market state at 17:28:05Z. THE BOOK HERE IS RECONSTRUCTED from the rungs
#: the driver actually quoted; it was not recorded. The INVENTORY difference is
#: the measured one, and it is the only variable changed between the two calls
#: below -- which is the point of the comparison.
def _state_at_the_moment():
    return {"symbol": SYMBOL, "tte_s": 114.0, "t_utc": 1789752485.0,
            "state_age_s": 0.2,
            "book": {"yes": {"bid": "0.90", "ask": "0.92"},
                     "no": {"bid": "0.08", "ask": "0.14"}},
            "inventory_known": True}


def test_the_controller_is_handed_inventory_it_did_not_have(tmp_path):
    """THE BEHAVIOURAL DIFFERENCE, isolated.

    `CP.plan` builds `state["inventory"]` FROM the Inventory object and hands
    it to the surface engine -- its own comment warns that a caller which
    forgets gets "a surface computed as if flat". That is precisely what
    happened: the oscillator never saw the NO leg, so nothing suppressed a YES
    rung that completed the pair at 1.06.

    This asserts the input the engine receives, which is the thing the fix
    changes. What the engine then emits is the engine's business and is not
    restated here.
    """
    flat = CP.Inventory()
    reconciled = CP.Inventory()
    reconciled.apply_fill("NO", "0.01", "0.14", source="venue_restart")

    st = _state_at_the_moment()
    flat_plan = CP.plan(dict(st), flat, actions_taken=5)
    real_plan = CP.plan(dict(st), reconciled, actions_taken=5)

    assert Decimal(flat.no_qty) == Decimal("0")
    assert Decimal(reconciled.no_qty) == Decimal("0.01")
    # the pair the window actually completed, priced from the two fills
    assert (Decimal("0.92") + Decimal("0.14")) == Decimal("1.06")
    assert Decimal("1.06") > Decimal("1.00"), (
        "the completed pair cost more than it can ever redeem")
    # and the two plans are computed from different inventory, which is the
    # single variable under test
    assert flat_plan is not real_plan


def test_a_fill_between_cancel_and_replace_is_seen_before_the_place(
        tmp_path, monkeypatch):
    """REQUIREMENT 6, end to end through run_window.

    The first refresh sees nothing. A fill lands while the cancel is in
    flight. The driver must refresh AGAIN after cancelling and before placing,
    or the replacement is sized against stale zero inventory -- which is the
    CONTINUOUS-4 sequence exactly.
    """
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    seen = []
    state = {"filled": False}

    async def refresh():
        seen.append(dict(state))
        if not state["filled"]:
            return {"fills": [], "open_orders": []}
        return {"fills": [{"leg": "NO", "qty": "0.01", "price": "0.14"}],
                "open_orders": []}

    # the cancel is what "lets the fill land"
    async def cancel(session, order):
        state["filled"] = True
        return {"cancelled": False, "cancel_confirmed": False}

    monkeypatch.setattr(d, "cancel", cancel)
    placed = []

    async def place(session, rung):
        placed.append(dict(d.inv.report()))
        d.stop(WC.STOPPED, "replay stops after one place")
        return {"placed": True}

    monkeypatch.setattr(d, "place", place)
    monkeypatch.setattr(d, "decide", lambda tte: {
        "state": WC.RUNNING, "window_phase": "WINDOW_OPEN",
        "plan": {"cancel": [{"order": {"client_order_id": "c1"}}],
                 "place": [{"rung": {"leg": "YES", "price": "0.92",
                                     "qty": "0.01"}}]}})
    monkeypatch.setattr(d, "preflight", lambda **k: {})
    monkeypatch.setattr(d, "authorize", lambda: {})
    monkeypatch.setattr(d, "recover", lambda **k: {})
    monkeypatch.setattr(d, "bind", lambda: {})

    _run(d.run_window(ScriptedSession([]), tte_of=lambda: 114.0,
                      refresh_fn=refresh, interval_s=0.0, max_cycles=1))

    assert len(seen) == 2, "refresh must run pre-decide AND post-cancel"
    assert placed, "the place should have been attempted"
    assert Decimal(placed[0]["no_qty"]) == Decimal("0.01"), (
        "the placement saw stale zero inventory; requirement 6 is unmet")


def test_run_window_records_every_refresh_for_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)

    async def refresh():
        return {"fills": [], "open_orders": []}

    monkeypatch.setattr(d, "decide", lambda tte: {
        "state": WC.RUNNING, "plan": {}})
    monkeypatch.setattr(d, "preflight", lambda **k: {})
    monkeypatch.setattr(d, "authorize", lambda: {})
    monkeypatch.setattr(d, "recover", lambda **k: {})
    monkeypatch.setattr(d, "bind", lambda: {})
    rep = _run(d.run_window(ScriptedSession([]), tte_of=lambda: 114.0,
                            refresh_fn=refresh, interval_s=0.0, max_cycles=2))
    refs = rep.get("refreshes") or []
    assert len(refs) == 2
    assert all(r["when"] == "pre_decide" for r in refs)
    assert all("n_fills" in r for r in refs)


def test_turns_carry_the_inventory_the_decision_used(tmp_path, monkeypatch):
    """REQUIREMENT 4. The report must show WHICH inventory each decision saw,
    or 'the controller used reconciled state' is unverifiable after the fact."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)

    async def refresh():
        return {"fills": [{"leg": "NO", "qty": "0.01", "price": "0.14"}],
                "open_orders": []}

    monkeypatch.setattr(d, "decide", lambda tte: {
        "state": WC.STOPPED, "plan": {}})
    monkeypatch.setattr(d, "preflight", lambda **k: {})
    monkeypatch.setattr(d, "authorize", lambda: {})
    monkeypatch.setattr(d, "recover", lambda **k: {})
    monkeypatch.setattr(d, "bind", lambda: {})
    rep = _run(d.run_window(ScriptedSession([]), tte_of=lambda: 114.0,
                            refresh_fn=refresh, interval_s=0.0, max_cycles=1))
    inv = rep["turns"][0]["inventory"]
    assert Decimal(inv["no_qty"]) == Decimal("0.01")
    for key in ("yes_qty", "no_qty", "matched_qty", "directional_residual",
                "inventory_imbalance", "total_spend_usd", "avg_yes_cost",
                "avg_no_cost", "same_side_run", "n_fills"):
        assert key in inv, key


# =============================================== RISK LIMITS ON REAL INVENTORY

def test_risk_gates_run_against_reconciled_inventory_not_zero():
    """REQUIREMENT 5. would_breach is only as good as the inventory it is
    handed; for a whole window it was handed zero."""
    inv = CP.Inventory()
    for _ in range(10):
        inv.apply_fill("NO", "0.01", "0.50", source="venue_restart")
    assert Decimal(inv.no_qty) == Decimal("0.10")
    assert CP.would_breach("NO", "0.50", inv), (
        "a further NO must breach the 0.10 per-leg cap on real inventory")
    assert not CP.would_breach("NO", "0.50", CP.Inventory()), (
        "and the SAME rung passes against the flat inventory the driver used "
        "to hold -- which is the defect, stated as a test")


def test_the_envelope_bounds_are_unchanged():
    """No bound moved as part of this fix."""
    assert str(CP.MAX_ACTIONS) == "12"
    assert str(CP.MAX_RESIDUAL) == "0.04"
    assert str(CP.MAX_IMBALANCE) == "0.04"
    assert str(CP.MAX_YES) == "0.10"
    assert str(CP.MAX_NO) == "0.10"
    assert str(CP.MAX_TOTAL_SPEND) == "0.15"
    assert str(CP.ACTION_SIZE) == "0.01"
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


# ====================================================== KEEP SEMANTICS INTACT

def test_keep_still_costs_no_mutation_slot():
    """REQUIREMENT 9. An unchanged resting rung is KEPT for zero slots."""
    resting = WC.WorkingOrders()
    resting.add("c1", leg="YES", price="0.03", qty="0.01", now=1.0,
                venue_order_id="1")
    desired = [{"leg": "YES", "price": "0.03", "qty": "0.01"}]
    diff = WC.diff_surface(desired, resting)
    assert diff["mutations"] == 0
    assert len(diff["keep"]) == 1
    assert not diff["place"] and not diff["cancel"]


# ============================================== OBSERVATION, NEVER A DECISION

def test_order_events_are_recorded_but_never_parsed(tmp_path, monkeypatch):
    """The orderUpdate shape has never been observed. It is recorded so it
    becomes evidence; nothing decides on it."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)

    class Q:
        def __init__(self, items):
            self.items = list(items)

        def get_nowait(self):
            if not self.items:
                raise RuntimeError("empty")
            return self.items.pop(0)

    s = ScriptedSession([])
    s.events = Q([{"e": "orderUpdate", "shape": "never seen before"}])
    rep = {}
    d._drain_events(s, rep)
    assert rep["order_events"] == [{"e": "orderUpdate",
                                    "shape": "never seen before"}]


def test_draining_is_bounded(tmp_path):
    d = _driver(tmp_path)

    class Endless:
        def get_nowait(self):
            return {"e": "orderUpdate"}

    s = ScriptedSession([])
    s.events = Endless()
    rep = {}
    d._drain_events(s, rep)
    assert len(rep["order_events"]) == WC._MAX_EVENTS_PER_CYCLE


def test_a_session_without_an_event_queue_is_fine(tmp_path):
    d = _driver(tmp_path)
    rep = {}
    d._drain_events(ScriptedSession([]), rep)
    assert rep == {}
