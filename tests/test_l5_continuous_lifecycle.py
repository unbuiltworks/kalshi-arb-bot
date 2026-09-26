"""One process, many markets, a hostile venue: the whole loop end to end.

Everything the 2026-09-26 continuous arm actually did to itself, driven
through the real Runner tick: orders submitted and adopted, partial fills
becoming full ones, an order that vanishes from the open list, a venue that
answers 429, a window that expires and settles, a market that retires, and a
restart in the middle of it.

The unit tests in test_l5_costless_inventory.py hold each defect to its own
fix. This one holds the SEQUENCE together, because all four defects were
individually survivable and only became a $1.09 loss in combination: the
rate limit made the order query fail, the failed query made reconcile
release the order, the released order left quantity with no cost, and the
market that could never leave the book kept generating the requests that
caused the rate limit.
"""
import collections
import os
import tempfile
import urllib.error
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_pmus_transport as T

MKTS = ["cpc-btc-updown-15m-2026-09-26-2%s" % s
        for s in ("045z", "100z", "115z", "130z")]


def rate_limit():
    return urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)


class Venue:
    """A venue that can be told to misbehave, and that keeps real books.

    Order rows are the shape the real one returns, including the three
    side words that must agree.
    """

    def __init__(self):
        self.market_slug = MKTS[0]
        self.armed = True
        self.orders = {}          # venue id -> row
        self.open_ids = set()
        self.settled = {}         # market -> settlement price
        self.fail_order_query = set()
        self.fail_everything = 0
        self.reads = collections.Counter()
        self.limiter = T.RateLimiter(rps=1e6, burst=1e6)

    # -- venue-side bookkeeping used by the test, not by the runner
    def place(self, vid, market, side, price, qty):
        self.orders[vid] = {
            "id": vid, "client_order_id": vid, "marketSlug": market,
            "market": market, "side": side,
            "outcomeSide": ("OUTCOME_SIDE_YES" if side == "ORDER_SIDE_BUY"
                            else "OUTCOME_SIDE_NO"),
            "intent": ("ORDER_INTENT_BUY_LONG" if side == "ORDER_SIDE_BUY"
                       else "ORDER_INTENT_BUY_SHORT"),
            "price": {"value": price, "currency": "USD"},
            "quantity": qty, "remainingQuantity": qty,
            "cumQuantity": "0", "leavesQuantity": qty,
            "avgPx": {"value": "0", "currency": "USD"},
            "commissionNotionalTotalCollected": {"value": "0",
                                                 "currency": "USD"},
            "state": "ORDER_STATE_NEW"}
        self.open_ids.add(vid)
        return self.orders[vid]

    def fill(self, vid, cum, avg, *, done):
        o = self.orders[vid]
        o["cumQuantity"] = str(cum)
        o["avgPx"] = {"value": str(avg), "currency": "USD"}
        o["leavesQuantity"] = str(D(str(o["quantity"])) - D(str(cum)))
        o["remainingQuantity"] = o["leavesQuantity"]
        if done:
            o["state"] = "ORDER_STATE_FILLED"
            self.open_ids.discard(vid)

    # -- the surface the runner uses
    def _guard(self):
        self.reads["total"] += 1
        if self.fail_everything > 0:
            self.fail_everything -= 1
            raise rate_limit()

    def order(self, vid):
        self._guard()
        self.reads["order"] += 1
        if vid in self.fail_order_query:
            raise rate_limit()
        # PmusTransport.order() unwraps {"order": ...} before returning, so
        # the runner sees the row itself. The fake stands in for the
        # transport, not for the HTTP layer, so it returns the row.
        return dict(self.orders[vid])

    def open_orders(self):
        self._guard()
        self.reads["open_orders"] += 1
        return [self.orders[v] for v in sorted(self.open_ids)]

    def positions(self):
        self._guard()
        self.reads["positions"] += 1
        return []

    def settlement(self, slug):
        self._guard()
        self.reads["settlement"] += 1
        px = self.settled.get(slug)
        return {"market": slug, "state": ("MARKET_STATE_EXPIRED" if px
                                          is not None else "MARKET_STATE_OPEN"),
                "settlement_px": px, "terminal": px is not None}


def runner(tmp, venue):
    r = R.Runner.__new__(R.Runner)
    r.root = tmp
    r.store = LX.InventoryStore(os.path.join(tmp, "inv.json"))
    r.limits = LX.SafetyLimits(**R.PROBE_LIMITS)
    r.transport = venue
    r.reconciled = False
    r.last_reconcile_t = -1e9
    r.last_progress_t = 0.0
    r.now_t = 1000.0
    r.last_order_poll = {}
    r.last_settlement_poll = {}
    r.last_pending_resolve = {}
    r.stats = collections.Counter()
    r.win = collections.Counter()
    return r


def submit(r, v, vid, market, side, price, qty):
    """Place at the venue AND record locally: what submit + ACK does.

    Adoption (the venue holding an order we never recorded) is the recovery
    path and is exercised separately; this is the ordinary one.
    """
    v.place(vid, market, side, price, qty)
    own = (D(price) if side == "ORDER_SIDE_BUY" else D(1) - D(price))
    r.store.record_submit(vid, {"market": market,
                                "side": LX.canonical_side(side, doing="t"),
                                "price": own, "qty": D(qty)},
                          session_id="lifecycle-test", mode="MAKER")
    r.store.attach_venue_id(vid, vid)


def step(r, dt_s=3.0):
    """One tick of the real loop, minus the feed and the window roll."""
    r.now_t += dt_s
    if r.store.live_order_count():
        r.poll_orders()
    if r.store.pending_terminal():
        r.resolve_pending_terminal(r.now_t)
    r.sweep_settlements()
    r.reconcile_if_needed(r.now_t)


@pytest.fixture()
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield d


# =====================================================================

def test_a_partial_fill_becomes_a_full_one_and_the_market_settles(tmp):
    v = Venue()
    r = runner(tmp, v)
    v.place("V1", MKTS[0], "ORDER_SIDE_SELL", "0.80", "2.25")

    step(r)                                   # adopt from the venue
    assert r.store.live_orders()["V1"]["side"] == LX.DOWN
    assert r.store.live_orders()["V1"]["venue_side_raw"] == "ORDER_SIDE_SELL"

    v.fill("V1", "1.00", "0.80", done=False)
    step(r, dt_s=R.ORDER_POLL_S + 1)
    assert r.store.position(MKTS[0], LX.DOWN) == D("1.00")
    assert r.store.leg_cost(MKTS[0])[LX.DOWN] == D("0.20")
    assert r.store.live_order_count() == 1    # the remainder is still live

    v.fill("V1", "2.25", "0.80", done=True)
    step(r, dt_s=R.ORDER_POLL_S + 1)
    assert r.store.position(MKTS[0], LX.DOWN) == D("2.25")
    assert r.store.leg_cost(MKTS[0])[LX.DOWN] == D("0.45")
    assert r.store.live_order_count() == 0

    v.settled[MKTS[0]] = D("1.0000")          # UP won: the shorts are dust
    v.market_slug = MKTS[1]                   # the window rolled
    step(r, dt_s=R.SETTLEMENT_POLL_S + 1)
    assert MKTS[0] not in r.store.markets_with_exposure()
    assert r.store.realized_today() == D("-0.45")


def test_the_2045z_sequence_no_longer_loses_the_cost_basis(tmp):
    """THE INCIDENT, in order. The order fills, the venue drops it from the
    open list, and every query for it answers 429."""
    v = Venue()
    r = runner(tmp, v)
    v.place("CR157F2WJYC3", MKTS[0], "ORDER_SIDE_SELL", "0.8079", "2.25")
    step(r)
    assert r.store.live_order_count() == 1

    v.fill("CR157F2WJYC3", "2.25", "0.8079", done=True)   # filled, now gone
    v.fail_order_query.add("CR157F2WJYC3")                # and unreadable
    for _ in range(4):
        step(r, dt_s=R.ORDER_POLL_S + 1)

    # OLD BEHAVIOUR: released, forgotten, 2.25 shares arrive costless later.
    # NEW: parked, loud, and nothing may be submitted.
    assert "CR157F2WJYC3" in r.store.pending_terminal()
    assert r.store.orders_uncertain() is not None
    assert r.store.position(MKTS[0], LX.DOWN) == D(0)

    # the market must NOT settle while the fill is unbooked
    v.settled[MKTS[0]] = D("1.0000")
    v.market_slug = MKTS[1]
    step(r, dt_s=R.SETTLEMENT_POLL_S + 1)
    assert r.stats["retire_refused:UNBOOKED_TERMINAL_ORDERS"] >= 1
    assert r.store.realized_today() == D(0)

    # the venue comes back, and the cost lands where it always belonged
    v.fail_order_query.clear()
    step(r, dt_s=R.PENDING_RESOLVE_S + 1)
    assert r.store.pending_terminal() == {}
    assert r.store.position(MKTS[0], LX.DOWN) == D("2.25")
    assert r.store.leg_cost(MKTS[0])[LX.DOWN] == D("2.25") * D("0.1921")

    step(r, dt_s=R.SETTLEMENT_POLL_S + 1)
    assert MKTS[0] not in r.store.markets_with_exposure()
    assert r.store.realized_today() == -(D("2.25") * D("0.1921"))


def test_a_settled_market_is_settled_exactly_once_over_many_ticks(tmp):
    """2115z emitted L5_LIVE_SETTLED 40+ times at ~3s intervals."""
    v = Venue()
    r = runner(tmp, v)
    v.place("V1", MKTS[0], "ORDER_SIDE_BUY", "0.50", "2")
    step(r)
    v.fill("V1", "2", "0.50", done=True)
    step(r, dt_s=R.ORDER_POLL_S + 1)

    v.settled[MKTS[0]] = D("1.0000")
    v.market_slug = MKTS[1]
    for _ in range(40):
        step(r, dt_s=R.SETTLEMENT_POLL_S + 1)
    assert r.store.realized_today() == D("1.00")      # not 40 x 1.00
    assert list(r.store.state["retired_markets"]) == [MKTS[0]]
    # and it stopped costing venue reads entirely
    before = v.reads["settlement"]
    for _ in range(10):
        step(r, dt_s=R.SETTLEMENT_POLL_S + 1)
    assert v.reads["settlement"] == before


def test_four_markets_run_through_the_loop_without_contaminating_each_other(tmp):
    """Each market's fills land in ITS book. poll_orders used to pass the
    transport's CURRENT slug, so an order still live when the window rolled
    had its fill booked into the next market."""
    v = Venue()
    r = runner(tmp, v)
    expected = {}
    for i, m in enumerate(MKTS):
        v.market_slug = m
        vid = "V%d" % i
        submit(r, v, vid, m, "ORDER_SIDE_SELL", "0.80", "1")
        step(r)
        v.fill(vid, "1", "0.80", done=True)
        # roll the window BEFORE the fill is polled: this is the case that
        # used to misattribute.
        v.market_slug = MKTS[(i + 1) % len(MKTS)]
        step(r, dt_s=R.ORDER_POLL_S + 1)
        expected[m] = D("0.20")

    for m, cost in expected.items():
        assert r.store.position(m, LX.DOWN) == D("1"), m
        assert r.store.leg_cost(m)[LX.DOWN] == cost, m


def test_the_loop_survives_a_venue_that_answers_429_to_everything(tmp):
    """Fail closed, stay closed, recover authoritatively -- and never
    convert an unreadable order into costless inventory along the way."""
    v = Venue()
    r = runner(tmp, v)
    v.place("V1", MKTS[0], "ORDER_SIDE_SELL", "0.80", "2")
    step(r)
    v.fill("V1", "2", "0.80", done=True)

    v.fail_everything = 60                    # the venue refuses everything
    for _ in range(20):
        step(r, dt_s=R.ORDER_POLL_S + 1)
    assert r.store.orders_uncertain() is not None
    assert r.store.position(MKTS[0], LX.DOWN) == D(0)   # nothing invented
    assert r.store.realized_today() == D(0)

    v.fail_everything = 0
    for _ in range(4):
        step(r, dt_s=R.PENDING_RESOLVE_S + 1)
    assert r.store.pending_terminal() == {}
    assert r.store.position(MKTS[0], LX.DOWN) == D("2")
    assert r.store.leg_cost(MKTS[0])[LX.DOWN] == D("0.40")
    assert r.store.orders_uncertain() is None


def test_the_request_rate_is_bounded_per_thing_not_per_tick(tmp):
    """The old loop polled every live order and every open market on every
    3s tick: the busier the book, the harder we hit the venue. With eight
    orders that is ~2.7 GETs a second from one path, which is how we
    rate-limited ourselves."""
    v = Venue()
    r = runner(tmp, v)
    for i in range(8):
        v.place("V%d" % i, MKTS[0], "ORDER_SIDE_SELL", "0.80", "1")
    step(r)
    v.reads.clear()

    for _ in range(20):                       # 60 seconds of 3s ticks
        step(r, dt_s=3.0)
    # 8 orders over 60s at one read per ORDER_POLL_S is ~80; the old code
    # would have made 8 x 20 = 160.
    assert v.reads["order"] <= 8 * (60.0 / R.ORDER_POLL_S) + 8
    assert v.reads["order"] < 160


def test_a_restart_mid_flight_resumes_from_the_file_and_settles_once(tmp):
    """The process dies holding a parked order and a market that has
    expired. A new process must book the fill, settle once, and not
    re-settle anything it already retired."""
    v = Venue()
    r = runner(tmp, v)
    submit(r, v, "V1", MKTS[0], "ORDER_SIDE_SELL", "0.80", "2")
    step(r)
    v.fill("V1", "2", "0.80", done=True)
    v.fail_order_query.add("V1")
    for _ in range(4):                        # until a reconcile parks it
        step(r, dt_s=R.ORDER_POLL_S + 1)
    assert "V1" in r.store.pending_terminal()

    # --- crash here ---
    v.fail_order_query.clear()
    v.settled[MKTS[0]] = D("0.0000")          # DOWN won this time
    v.market_slug = MKTS[1]
    r2 = runner(tmp, v)                       # same directory, new process
    assert "V1" in r2.store.pending_terminal()          # survived the file

    for _ in range(3):
        step(r2, dt_s=R.SETTLEMENT_POLL_S + 1)
    assert r2.store.pending_terminal() == {}
    # 2 DOWN shares costing 0.40, redeeming $1 each
    assert r2.store.realized_today() == D("1.60")
    assert MKTS[0] not in r2.store.markets_with_exposure()

    r3 = runner(tmp, v)                       # and again
    for _ in range(5):
        step(r3, dt_s=R.SETTLEMENT_POLL_S + 1)
    assert r3.store.realized_today() == D("1.60")


def test_an_adopted_order_from_another_session_is_polled_not_stalled(tmp):
    """1830z looped every 92 seconds because adoption omitted the venue id,
    so the adopted order could never be polled, never progressed, tripped
    the 90s stall, and was re-adopted."""
    v = Venue()
    r = runner(tmp, v)
    v.place("V1", MKTS[0], "ORDER_SIDE_BUY", "0.60", "1")
    step(r)
    o = r.store.live_orders()["V1"]
    assert o["adopted"] is True
    assert o["venue_order_id"] == "V1"
    assert o["price"] == "0.60"               # own terms for an UP leg

    v.fill("V1", "1", "0.60", done=True)
    step(r, dt_s=R.ORDER_POLL_S + 1)
    assert r.store.position(MKTS[0], LX.UP) == D("1")
    assert r.store.leg_cost(MKTS[0])[LX.UP] == D("0.60")
    assert r.orders_stalled_for_s(r.now_t) == 0.0
