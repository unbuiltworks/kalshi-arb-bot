"""The restart blocker, driven exactly as it was reported, and the dollar proof.

THE REPORTED DEFECT. Across

    rest -> partial fill -> second rest -> cancel ambiguous -> process dies
    -> restart

nothing durable said what might still be resting, so the $2.50 committed cap
and the $2.00 residual cap were bounds within one process rather than across a
restart. Every test here attacks that across a REAL process boundary: the
second `Driver` is constructed from nothing but the files on disk.

NOTHING HERE ARMS ANYTHING. `_authorized` patches the module's own constants so
the mutation path is reachable inside the test process only; the shipped file
has all three as None and `test_authorization_remains_impossible` proves it.
"""
import asyncio
import itertools
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import outstanding_ledger as OL
from src.gemini_execution import ws_continuous as WC

SYM = "GEMI-BTC05M2609181200-UP"
AUTH = "OWNER-TEST-NOT-A-REAL-AUTHORIZATION"
ENV = LE.LIVE_Q1


class FakeSession:
    """Scripted acks. An Exception in the script is raised, as the socket would."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.sent = []

    async def request(self, method, params, timeout=5.0):
        self.sent.append({"method": method, "params": params})
        r = self.script.pop(0) if self.script else {"status": 200,
                                                    "result": {"orderId": 1}}
        if isinstance(r, Exception):
            raise r
        return r


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        coro)


@pytest.fixture
def _authorized(monkeypatch):
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", AUTH)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", WC.envelope_sha())
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized()


@pytest.fixture(autouse=True)
def _clean_registry():
    WC._BOUND_MARKETS.clear()
    yield
    WC._BOUND_MARKETS.clear()


def _driver(tmp_path):
    """A driver whose ONLY state is the files in tmp_path."""
    return WC.Driver(AUTH, SYM, ledger_path=str(tmp_path / "mut.jsonl"),
                     now_fn=lambda: 1_789_000_000.0, envelope=ENV)


def _rung(leg, price, qty="1"):
    return {"leg": leg, "price": price, "qty": qty, "action": CP.OPEN}


# ============================================================================
# 7. THE EXACT ADVERSARIAL SEQUENCE
# ============================================================================

def _run_the_sequence(tmp_path):
    """A rests -> A partially fills -> B rests -> A fills further
       -> cancel A ambiguous -> cancel B ambiguous -> process dies.

    Returns the session so the caller can assert what reached the venue.
    """
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    assert d.recover(venue_fills=[], venue_open=[], venue_orders=[])[
        "recovered"]
    assert d.bind()["bound"]

    s = FakeSession(script=[
        {"status": 200, "result": {"orderId": "A1"}},   # place A
        {"status": 200, "result": {"orderId": "B1"}},   # place B
        RuntimeError("socket died"),                    # cancel A ambiguous
        RuntimeError("socket died"),                    # cancel B ambiguous
    ])
    a = _run(d.place(s, _rung("YES", "0.60")))
    assert a["placed"]
    d.apply_fill(a["client_order_id"], "0.4", "0.60", partial=True)

    b = _run(d.place(s, _rung("NO", "0.30")))
    assert b["placed"]

    d.apply_fill(a["client_order_id"], "0.2", "0.60", partial=True)

    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(s, {"client_order_id": a["client_order_id"],
                          "venue_order_id": "A1"}))
    # the driver is in RECONCILE now; the operator still tries B's cancel,
    # which is exactly the "cancel ambiguous" case being tested
    d.state = WC.RUNNING
    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(s, {"client_order_id": b["client_order_id"],
                          "venue_order_id": "B1"}))
    return d, a["client_order_id"], b["client_order_id"]


def test_the_reported_sequence_refuses_order_C_after_a_restart(tmp_path,
                                                               _authorized):
    """THE HEADLINE. C MUST NOT REACH THE VENUE."""
    d1, coid_a, coid_b = _run_the_sequence(tmp_path)
    del d1
    WC._BOUND_MARKETS.clear()

    # ---- the process dies. A NEW one starts with only the files on disk.
    d2 = _driver(tmp_path)
    assert {coid_a, coid_b} <= set(d2.outstanding.orders), (
        "the new process must learn about A and B from disk alone")
    for coid in (coid_a, coid_b):
        assert d2.outstanding.orders[coid]["state"] not in OL.TERMINAL

    d2.state = WC.AUTHORIZED
    out = d2.recover(
        venue_fills=[{"leg": "YES", "qty": "0.6", "price": "0.60"}],
        venue_orders=[],
        venue_open=[
            {"clientOrderId": coid_a, "orderId": "A1", "outcome": "yes",
             "price": "0.60", "quantity": "1", "filledQuantity": "0.6"},
            {"clientOrderId": coid_b, "orderId": "B1", "outcome": "no",
             "price": "0.30", "quantity": "1", "filledQuantity": "0"},
        ])
    assert out["recovered"] is False
    assert d2.state == WC.DO_NOT_TRADE
    assert out["outstanding"]["verdict"] == OL.BLOCKED
    assert len(out["outstanding"]["blocking"]) == 2

    # ---- binding is refused, and so is order C
    assert d2.bind()["bound"] is False
    s = FakeSession()
    with pytest.raises((CP.ProbeStop, WC.ReconcileRequired)):
        _run(d2.place(s, _rung("YES", "0.50")))
    assert s.sent == [], "ORDER C MUST NOT REACH THE VENUE"


def test_restart_with_an_unreadable_active_order_read_sends_nothing(
        tmp_path, _authorized):
    d1, _, _ = _run_the_sequence(tmp_path)
    del d1
    WC._BOUND_MARKETS.clear()

    d2 = _driver(tmp_path)
    d2.state = WC.AUTHORIZED
    out = d2.recover(venue_fills=[], venue_orders=[], venue_open=None)
    assert out["recovered"] is False
    assert d2.state == WC.DO_NOT_TRADE
    assert out["outstanding"]["verdict"] == OL.UNREADABLE
    assert out["outstanding"]["committed_usd"] is None, (
        "an unreadable venue releases nothing and establishes no number")
    assert d2.bind()["bound"] is False
    s = FakeSession()
    with pytest.raises((CP.ProbeStop, WC.ReconcileRequired)):
        _run(d2.place(s, _rung("YES", "0.50")))
    assert s.sent == [], "NO ORDER MAY REACH THE VENUE"


@pytest.mark.parametrize("malformed", [
    ["not a mapping"],
    [{"clientOrderId": "x"}, 17],
])
def test_a_malformed_active_order_payload_is_unreadable_not_empty(
        tmp_path, malformed):
    led = OL.OutstandingLedger(str(tmp_path / "o.jsonl"), auth_id=AUTH,
                               symbol=SYM)
    led.intent("c1", leg="YES", price="0.60", qty="1")
    r = led.reconcile(malformed)
    assert r["verdict"] == OL.UNREADABLE and r["tradeable"] is False
    assert led.orders["c1"]["state"] not in OL.TERMINAL, (
        "a malformed read must release nothing")


def test_only_a_clean_authoritative_read_makes_a_window_eligible(
        tmp_path, _authorized):
    d1, coid_a, coid_b = _run_the_sequence(tmp_path)
    del d1
    WC._BOUND_MARKETS.clear()

    d2 = _driver(tmp_path)
    d2.state = WC.AUTHORIZED
    out = d2.recover(
        venue_fills=[{"leg": "YES", "qty": "0.6", "price": "0.60"}],
        venue_orders=[], venue_open=[])       # A and B are GONE
    assert out["recovered"] is True, out
    assert out["outstanding"]["verdict"] == OL.CLEAR
    assert sorted(out["outstanding"]["released"]) == sorted([coid_a, coid_b])
    assert out["outstanding"]["committed_usd"] == "0"
    assert d2.bind()["bound"] is True
    # inventory survived the restart and is the VENUE's, not ours
    assert d2.inv.yes_qty == D("0.6")


def test_a_cancel_ack_alone_never_releases_the_commitment(tmp_path,
                                                          _authorized):
    """Two of CONTINUOUS-4's four 'cancelled' orders had already FILLED."""
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    d.recover(venue_fills=[], venue_open=[], venue_orders=[])
    d.bind()
    s = FakeSession(script=[
        {"status": 200, "result": {"orderId": "A1"}},
        {"status": 200, "result": {"orderId": "A1"}},   # a CONFIRMED cancel
    ])
    a = _run(d.place(s, _rung("YES", "0.60")))
    out = _run(d.cancel(s, {"client_order_id": a["client_order_id"],
                            "venue_order_id": "A1"}))
    assert out["cancelled"] is True, "the ack confirmed"
    assert d.outstanding.orders[a["client_order_id"]]["state"] == \
        OL.CANCEL_ACKED
    assert d.outstanding.blocks_restart(), (
        "a confirmed cancel ACK is a receipt, not a release")
    assert d.outstanding.committed_usd()["usd"] == D("0.60")


def test_a_crash_between_intent_and_ack_leaves_the_order_live(tmp_path,
                                                              _authorized):
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    d.recover(venue_fills=[], venue_open=[], venue_orders=[])
    d.bind()
    s = FakeSession(script=[RuntimeError("died mid-send")])
    with pytest.raises(WC.ReconcileRequired):
        _run(d.place(s, _rung("YES", "0.60")))
    del d
    WC._BOUND_MARKETS.clear()

    d2 = _driver(tmp_path)
    live = d2.outstanding.potentially_live()
    assert len(live) == 1
    assert live[0]["state"] == OL.SUBMISSION_UNKNOWN
    assert d2.outstanding.committed_usd()["usd"] == D("0.60"), (
        "unknown fate commits the WHOLE notional: any part of it may fill")


def test_a_torn_final_row_is_an_order_of_unknown_fate(tmp_path):
    p = tmp_path / "o.jsonl"
    led = OL.OutstandingLedger(str(p), auth_id=AUTH, symbol=SYM)
    led.intent("c1", leg="YES", price="0.60", qty="1")
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"client_order_id": "c2", "state": "INT')
    again = OL.OutstandingLedger(str(p), auth_id=AUTH, symbol=SYM)
    torn = [o for o in again.potentially_live()
            if str(o["client_order_id"]).startswith("TORN-ROW")]
    assert len(torn) == 1 and torn[0]["state"] == OL.SUBMISSION_UNKNOWN
    assert again.committed_usd()["readable"] is False, (
        "an order whose notional cannot be computed makes the whole total "
        "unreadable; a partial total is not a total")


# ============================================================================
# 6. A PARTIAL FILL IS BOTH INVENTORY AND COMMITMENT
# ============================================================================

def test_a_partial_fill_is_inventory_AND_commitment_at_the_same_time(
        tmp_path, _authorized):
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    d.recover(venue_fills=[], venue_open=[], venue_orders=[])
    d.bind()
    s = FakeSession(script=[{"status": 200, "result": {"orderId": "A1"}}])
    a = _run(d.place(s, _rung("YES", "0.60")))
    d.apply_fill(a["client_order_id"], "0.4", "0.60", partial=True)

    assert d.inv.yes_qty == D("0.4")                 # filled -> inventory
    assert d.inv.spend() == D("0.240")
    o = d.outstanding.orders[a["client_order_id"]]
    assert d.outstanding.remaining_qty(o) == D("0.6")  # unfilled -> committed
    assert d.outstanding.remaining_committed_usd(o) == D("0.36")
    # and the two together are inside the envelope
    assert d.inv.spend() + D("0.36") <= ENV.max_committed_usd


# ============================================================================
# 8. THE DOLLAR BOUNDS, EXHAUSTIVELY
# ============================================================================

def _inv(yes_q, yes_px, no_q, no_px):
    inv = CP.Inventory()
    if yes_q:
        inv.apply_fill("YES", str(yes_q), str(yes_px), source="t")
    if no_q:
        inv.apply_fill("NO", str(no_q), str(no_px), source="t")
    return inv


PRICES = ("0.01", "0.25", "0.50", "0.75", "0.99", "1.00")
QTYS = ("0.25", "0.5", "1")


def test_no_permitted_rung_can_push_committed_cash_past_2_50():
    """EXHAUSTIVE over partial fills, resting sets and durable outstanding.

    The assertion is pure arithmetic on the scenario -- total cash that could
    leave the account -- and does not re-use the envelope's own internals, so
    a gate that forgot a term would fail here rather than agree with itself.
    """
    checked = permitted = 0
    worst = D("0")
    inventories = [(0, "0", 0, "0")]
    for q in ("0.5", "1", "2"):
        for px in ("0.50", "1.00"):
            inventories.append((q, px, 0, "0"))
            inventories.append((0, "0", q, px))
    inventories.append(("1", "1.00", "1", "1.00"))
    inventories.append(("0.5", "0.99", "0.5", "0.99"))

    resting_sets = [
        (),
        ({"leg": "NO", "price": "1.00", "qty": "1"},),
        ({"leg": "NO", "price": "0.50", "qty": "1"},),
        ({"leg": "YES", "price": "0.99", "qty": "0.5"},),
        ({"leg": "NO", "price": "1.00", "qty": "1"},
         {"leg": "YES", "price": "1.00", "qty": "1"}),
    ]
    extras = (None, "0", "0.50", "1.00", "2.00")

    for (yq, ypx, nq, npx), resting, extra, leg, px, qty in itertools.product(
            inventories, resting_sets, extras, ("YES", "NO"), PRICES, QTYS):
        inv = _inv(yq, ypx, nq, npx)
        checked += 1
        breach = LE.would_breach(leg, px, inv, qty=qty, resting=resting,
                                 extra_committed_usd=extra, env=ENV)
        if breach is not None:
            continue
        permitted += 1
        # WORST CASE CASH OUT: everything already spent, every resting order
        # fills, every durable outstanding order fills, and this rung fills.
        cash = inv.spend()
        for r in resting:
            cash += D(r["qty"]) * D(r["price"])
        cash += D(extra or "0")
        cash += D(qty) * D(px)
        worst = max(worst, cash)
        assert cash <= ENV.max_committed_usd, (
            leg, px, qty, resting, extra, inv.report(), str(cash))

    assert checked > 5000, checked
    assert permitted > 0, "a gate that permits nothing proves nothing"
    assert worst <= D("2.50")
    # the bound is REACHABLE, so this is not passing by being over-tight
    assert worst > D("2.00"), worst


def test_no_permitted_rung_can_push_directional_residual_past_2_00():
    """The residual dollar bound, computed from the scenario's own numbers."""
    checked = permitted = 0
    worst = D("0")
    inventories = [(0, "0", 0, "0")]
    for q in ("0.5", "1", "2"):
        for px in ("0.50", "0.99", "1.00"):
            inventories.append((q, px, 0, "0"))
            inventories.append((0, "0", q, px))
    inventories.append(("1", "1.00", "1", "1.00"))

    for (yq, ypx, nq, npx), leg, px, qty in itertools.product(
            inventories, ("YES", "NO"), PRICES, QTYS):
        inv = _inv(yq, ypx, nq, npx)
        checked += 1
        if LE.would_breach(leg, px, inv, qty=qty, env=ENV) is not None:
            continue
        permitted += 1
        # rebuild the post-fill book independently
        y_q, y_c = D(str(inv.yes_qty)), D(str(inv.yes_cost))
        n_q, n_c = D(str(inv.no_qty)), D(str(inv.no_cost))
        if leg == "YES":
            y_q, y_c = y_q + D(qty), y_c + D(qty) * D(px)
        else:
            n_q, n_c = n_q + D(qty), n_c + D(qty) * D(px)
        res = y_q - n_q
        if res == 0:
            continue
        held_q, held_c = (y_q, y_c) if res > 0 else (n_q, n_c)
        basis = abs(res) * (held_c / held_q)
        worst = max(worst, basis)
        assert basis <= ENV.max_residual_usd, (
            leg, px, qty, inv.report(), str(basis))

    assert checked > 500, checked
    assert permitted > 0
    assert worst <= D("2.00")
    assert worst > D("1.00"), worst


def test_the_committed_gate_refuses_an_unreadable_durable_total():
    inv = CP.Inventory()
    b = LE.would_breach("YES", "0.50", inv, qty="1",
                        extra_committed_usd="not-a-number", env=ENV)
    assert b and "unreadable" in b


# ============================================================================
# 9. AUTHORIZATION REMAINS IMPOSSIBLE
# ============================================================================

def test_the_tree_is_disarmed_and_the_q1_grant_is_spent():
    """DISARMED AGAIN 2026-09-21, after the q=1 grant was spent without
    trading.

    OWNER-2026-09-21-GEMINI-Q1-LIVE-1 was granted, armed, and consumed by run
    35570948794: the latch persisted as INTENT_PERSISTED with venue_order_id
    null and the driver then refused, because run_canary.BRANCH still named
    the instrumentation branch and the latch could not be pushed. ZERO ORDERS,
    cohort 0/12, no cash moved.

    A spent authorization never arms again -- so the constants return to the
    sentinel rather than the latch being cleared, which is the same rule
    CONTINUOUS-1 and CONTINUOUS-3 died under. Re-arming requires a NEW
    owner-granted identifier; it is not something this tree can restore to
    itself.
    """
    assert WC.REQUIRED_AUTH_ID is None
    assert WC.AUTHORIZED_SHA is None
    assert WC.AUTHORIZED_ENVELOPE is None
    assert WC.authorized() is False
    # AND THE REVIEWED TREE IS UNCHANGED UNDERNEATH. The hashes the spent
    # grant named still describe this code, so a re-grant is two identifier
    # lines and needs no re-review of the bounds.
    assert WC.executable_sha() == (
        "c1ada77a919c9c0cf226b4eb0c56aed6495f7e8fe1ece9261150aa6c26377763")
    assert WC.envelope_sha() == (
        "47f257f208017841d633c1bf5e2420eda2ca87b0ab502da34bd9d7e19690cf22")


def test_no_risk_bound_was_increased():
    assert ENV.action_size == D("1")
    assert ENV.max_committed_usd == D("2.50")
    assert ENV.max_total_spend_usd == D("2.50")
    assert ENV.max_residual_usd == D("2.00")
    assert ENV.max_imbalance == D("2")
    assert ENV.max_working_orders == 2
    assert ENV.max_concurrent_markets == 1
    assert ENV.max_actions == 12
    # and the SHADOW envelope the running experiment uses is untouched
    assert LE.SHADOW_Q001.action_size == D("0.01")
    assert LE.SHADOW_Q001.max_total_spend_usd == D("0.15")
    assert str(CP.ACTION_SIZE) == "0.01" and str(CP.MAX_RESIDUAL) == "0.04"


def test_resolving_an_attempt_from_the_venue_never_re_opens_the_budget(
        tmp_path, _authorized):
    """The read settles the OUTCOME. The slot stays spent, forever."""
    d1, coid_a, coid_b = _run_the_sequence(tmp_path)
    spent_before = d1.ledger.consumed()
    assert spent_before == 4, spent_before        # 2 places + 2 cancels
    del d1
    WC._BOUND_MARKETS.clear()

    d2 = _driver(tmp_path)
    d2.state = WC.AUTHORIZED
    out = d2.recover(venue_fills=[], venue_orders=[], venue_open=[])
    assert out["recovered"] is True
    assert d2.ledger.consumed() == spent_before, (
        "settling an outcome must never give a slot back")
    assert d2.budget_consumed == spent_before
    assert d2.ledger.unresolved() == []


def test_a_BLOCKED_read_settles_nothing_at_all(tmp_path, _authorized):
    """Fail-closed means the blocked path changes nothing further.

    A is still resting, so the window is refused and recovery returns before
    settling anything -- including B, which the same read did show gone. That
    is deliberate: the next CLEAN read settles it, and until then the safest
    state is the one where nothing was touched.
    """
    d1, coid_a, coid_b = _run_the_sequence(tmp_path)
    del d1
    WC._BOUND_MARKETS.clear()

    d2 = _driver(tmp_path)
    d2.state = WC.AUTHORIZED
    out = d2.recover(venue_fills=[], venue_orders=[], venue_open=[
        {"clientOrderId": coid_a, "orderId": "A1", "outcome": "yes",
         "price": "0.60", "quantity": "1", "filledQuantity": "0.6"}])
    assert out["recovered"] is False and d2.state == WC.DO_NOT_TRADE
    still = {r["client_order_id"] for r in d2.ledger.unresolved()
             if r.get("client_order_id")}
    assert {coid_a, coid_b} <= still, (
        "a blocked recovery settles nothing; only a clean read does")
    # A is still exposure and B's commitment was released by the read itself
    assert d2.outstanding.orders[coid_a]["state"] not in OL.TERMINAL
    assert d2.outstanding.orders[coid_b]["state"] == OL.VENUE_ABSENT


def test_cancelling_an_order_we_never_placed_stays_computable(tmp_path,
                                                              _authorized):
    """REGRESSION. An order recovered from the venue has no INTENT row of
    ours. A bare CANCEL_REQUESTED row would leave a potentially-live order
    with no size and no price, `committed_usd` would call the whole total
    unreadable, and every later placement would be refused for a reason that
    is not a risk reason at all."""
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    d.recover(venue_fills=[], venue_open=[], venue_orders=[])
    d.bind()
    d.working.add("recovered-1", leg="NO", price="0.14", qty="1",
                  now=0.0, venue_order_id="V1")
    s = FakeSession(script=[{"status": 200, "result": {"orderId": "V1"}}])
    _run(d.cancel(s, {"client_order_id": "recovered-1",
                      "venue_order_id": "V1", "leg": "NO",
                      "price": "0.14", "qty": "1"}))
    tot = d.outstanding.committed_usd()
    assert tot["readable"] is True, tot
    assert tot["usd"] == D("0.14"), (
        "a cancel ACK is not a release, so it still commits its notional")
