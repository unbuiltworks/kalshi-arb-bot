"""The ACTUAL money path: engine -> intents -> certified transport -> venue.

Ten properties the live path must have. Each one is driven through
`NativeMoneyPath`, not through a helper that supplies the thing under test.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import pmus_l5_native_engine as E
from src import pmus_native_live_overlay as OV
from src import pmus_native_money_path as MP

MARKET = "cpc-btc-updown-15m-2026-09-28-1600z"
OPEN = {"state": "MARKET_STATE_OPEN", "t": "2026-09-28T16:00:10Z",
        "bid": "0.40", "ask": "0.60"}


class Feed:
    def __init__(self, fresh=True, age=0.5):
        self._fresh, self._age = fresh, age

    def fresh(self):
        return self._fresh

    def age_s(self):
        return self._age


class Venue:
    """A venue double with explicit, separately-controllable state."""

    def __init__(self):
        self.cancelled = []
        self._open_orders = []
        self._unbooked = []
        #: a TRUE race: the venue does not reveal the fill until we have asked
        #: for a cancel. Fills are now booked every tick, so a fill that is
        #: visible beforehand is not a race -- it is just inventory.
        self.reveal_after_cancel = False
        self._pending_reveal = []
        self.completion = None
        self.open_orders_raises = False
        self.fills_raise = False
        self.open_orders_none = False

    def cancel_resting(self, side):
        self.cancelled.append(side)
        if self.reveal_after_cancel:
            self._unbooked.extend(self._pending_reveal)
            self._pending_reveal = []

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        if self.reveal_after_cancel:
            self._unbooked.extend(self._pending_reveal)
            self._pending_reveal = []

    def filled_qty(self, oid):
        return D(0)

    def open_orders(self):
        if self.open_orders_raises:
            raise RuntimeError("venue read failed")
        if self.open_orders_none:
            return None
        return list(self._open_orders)

    def unbooked_fills(self):
        if self.fills_raise:
            raise RuntimeError("fill read failed")
        out, self._unbooked = list(self._unbooked), []
        return out

    def completion_fill(self, intent):
        return self.completion


class Transport:
    """Stands in for PairedLockTransport(MANDATE.guard(...)). Records POSTs and
    can refuse exactly as the mandate would."""

    def __init__(self, refuse_over=None):
        self.posts = []
        self.refuse_over = refuse_over

    def submit_body(self, body):
        """The seam the PRODUCTION path uses.

        These doubles only had `request`, which is exactly why the
        2345z window armed and then died: `PmusTransport` has no
        public `request`, and every test here mocked the boundary
        instead of crossing it. Delegating keeps each subclass's
        recorded behaviour (including refusals) in one place.
        """
        return self.request("POST", "/v1/orders", body)

    def request(self, method, path, body=None, **kw):
        assert method == "POST" and path == MP.ORDER_PATH, (method, path)
        b = dict(body or {})
        # the REAL builder's shape: price is {"value","currency"} and quantity
        # is a float. Asserted from OB.build rather than assumed.
        notional = D(str(b["price"]["value"])) * D(str(b["quantity"]))
        if self.refuse_over is not None and notional > self.refuse_over:
            raise RuntimeError("MANDATE_REFUSED:%s>%s"
                               % (notional, self.refuse_over))
        self.posts.append(b)
        return {"orderId": "v%d" % len(self.posts), "state": "ORDER_STATE_OPEN"}


def path(venue=None, feed=None, transport=None, clip=D(50)):
    return MP.build(market=MARKET, transport=transport or Transport(),
                    feed=feed or Feed(), venue=venue or Venue(), clip=clip)


# 1 ----------------------------------------------------------------------
def test_only_an_authenticated_maker_fill_changes_passive_inventory():
    p = path()
    p.on_book_state(OPEN)
    assert p.tp.posts, "no order was placed"
    assert p.eng.bk.q == {"up": D(0), "down": D(0)}, (
        "submitting or ACKing an order moved inventory")
    p.eng.on_fill("up", D(50), D("20.00"), maker=True)
    assert p.eng.bk.q["up"] == D(50)
    assert p.eng.bk.maker_qty == D(50)


# 2 ----------------------------------------------------------------------
def test_economic_complete_goes_out_as_taker_with_the_walked_fee_and_depth():
    v = Venue()
    p = path(venue=v)
    p.eng.on_fill("up", D(10), D("4.00"), maker=True)      # residual +10
    # complement of UP is DOWN, and buying DOWN hits the BIDS: a bid at 0.80
    # costs 0.20 in DOWN's own terms, so basis 0.40 + 0.20 + fee clears 0.96.
    ladder = {"t": OPEN["t"], "bids": [["0.80", "10"]], "offers": []}
    v.completion = (D(10), D("2.00"), D("0.07"))
    out = p.on_book_state(OPEN, ladder)
    taker = [b for b in p.tp.posts
             if b.get("participateDontInitiate") is False]
    assert taker, "the completion did not go out as a taker order"
    assert p.eng.bk.completions == 1
    assert p.eng.bk.taker_qty == D(10)
    assert p.eng.bk.fees == D("0.07"), "the walked per-level fee was not booked"
    assert out["acted"] is True


def test_a_completion_the_venue_does_not_confirm_books_nothing():
    v = Venue()
    p = path(venue=v)
    p.eng.on_fill("up", D(10), D("4.00"), maker=True)
    v.completion = None                       # unconfirmed
    p.on_book_state(OPEN, {"t": OPEN["t"], "bids": [["0.80", "10"]],
                           "offers": []})
    assert p.eng.bk.completions == 0
    assert p.eng.bk.taker_qty == D(0)


# 3 ----------------------------------------------------------------------
def _at_lock(p):
    """Inventory whose floor is already >= $5 by holding."""
    p.eng.on_fill("up", D(100), D("10.00"), maker=True)
    p.eng.on_fill("down", D(100), D("10.00"), maker=True)
    # `resting` is now owned by the EXECUTION layer: the money path syncs
    # the engine from `live_orders` before every decision, so a resting
    # order is staged there. Writing eng.resting directly would be wiped by
    # that sync -- which is the point of the ownership boundary.
    # A REAL resting order exists in BOTH places: the registry (so the
    # cancel machinery can find and terminate it) and `live_orders` (the
    # authoritative view the engine now adopts). Staging only one of them
    # models an order that cannot be cancelled, which is a different test.
    p.registry.record("staged-up", side="up", price=D("0.40"), qty=D(50),
                      kind=E.REST)
    p.live_orders["up"] = {"oid": "staged-up", "px": "0.40", "qty": "50"}
    p.eng.resting["up"] = {"px": D("0.40"), "qty": D(50)}


def test_a_lock_candidate_cannot_latch_from_cancel_submission_alone():
    v = Venue()
    v._open_orders = ["still-live"]           # the venue has NOT honoured it
    p = path(venue=v)
    _at_lock(p)
    out = p.on_book_state(OPEN)
    assert out["lock"] == OV.FAILED_CLOSED, out
    assert p.eng.locked_at is None, "latched on an unconfirmed cancellation"
    assert v.cancelled, "the cancel was never even requested"


def test_a_confirmed_cancellation_with_a_good_floor_does_latch():
    v = Venue()
    p = path(venue=v)
    _at_lock(p)
    out = p.on_book_state(OPEN)
    assert out["lock"] == OV.LATCHED, out
    assert p.eng.locked_at is not None
    assert p.eng.lock_floor >= D(5)


# 4 ----------------------------------------------------------------------
def test_a_cancel_race_fill_is_booked_before_the_lock_is_evaluated():
    v = Venue()
    p = path(venue=v)
    _at_lock(p)
    # a maker fill landed while we were cancelling -- revealed only then
    v.reveal_after_cancel = True
    v._pending_reveal = [{"side": "up", "qty": "50", "cost": "20.00",
                          "maker": True}]
    out = p.on_book_state(OPEN)
    assert p.eng.bk.q["up"] == D(150), "the race fill was not applied"
    txn = p.lock_transactions[-1]
    order = [s["step"] for s in txn["steps"]]
    assert order.index("race_fills_booked") < order.index("floor_recomputed"), (
        "the floor was recomputed before the race fill was booked: %s" % order)
    assert out["lock"] in (OV.LATCHED, OV.FAILED_CLOSED)


def test_a_race_fill_that_breaks_the_floor_prevents_the_latch():
    v = Venue()
    p = path(venue=v)
    # a floor sitting exactly AT the threshold: 100 - (47.50 + 47.50) = 5.00
    p.eng.on_fill("up", D(100), D("47.50"), maker=True)
    p.eng.on_fill("down", D(100), D("47.50"), maker=True)
    # `resting` is now owned by the EXECUTION layer: the money path syncs
    # the engine from `live_orders` before every decision, so a resting
    # order is staged there. Writing eng.resting directly would be wiped by
    # that sync -- which is the point of the ownership boundary.
    # A REAL resting order exists in BOTH places: the registry (so the
    # cancel machinery can find and terminate it) and `live_orders` (the
    # authoritative view the engine now adopts). Staging only one of them
    # models an order that cannot be cancelled, which is a different test.
    p.registry.record("staged-up", side="up", price=D("0.40"), qty=D(50),
                      kind=E.REST)
    p.live_orders["up"] = {"oid": "staged-up", "px": "0.40", "qty": "50"}
    p.eng.resting["up"] = {"px": D("0.40"), "qty": D(50)}
    # the race fill adds cost without adding to min(), so the floor breaks
    v.reveal_after_cancel = True
    v._pending_reveal = [{"side": "up", "qty": "50", "cost": "10.00",
                          "maker": True}]
    out = p.on_book_state(OPEN)
    assert out["lock"] == OV.FAILED_CLOSED, out
    assert p.eng.locked_at is None
    assert "FLOOR_BELOW_THRESHOLD_AFTER_RECONCILIATION" in \
        p.lock_transactions[-1]["reason"]


# 5 ----------------------------------------------------------------------
@pytest.mark.parametrize("break_it", ["open_orders_raises", "fills_raise",
                                      "open_orders_none"])
def test_uncertain_cancel_or_fill_state_fails_closed(break_it):
    v = Venue()
    setattr(v, break_it, True)
    p = path(venue=v)
    _at_lock(p)
    out = p.on_book_state(OPEN)
    assert out["lock"] == OV.FAILED_CLOSED, out
    assert p.eng.locked_at is None


def test_no_new_acquisition_risk_is_placed_during_the_transition():
    v = Venue()
    v.open_orders_raises = True               # transaction will fail closed
    p = path(venue=v)
    _at_lock(p)
    before = len(p.tp.posts)
    p.on_book_state(OPEN)
    assert p.eng.lock_pending is False, "the transaction never resolved"
    # the next book state must not open new risk while unresolved/aborted
    p.eng.lock_pending = True
    p.on_book_state(OPEN)
    assert len(p.tp.posts) == before, "new acquisition risk during transition"


# 6 ----------------------------------------------------------------------
def test_after_a_confirmed_lock_no_open_acquire_or_extend_reaches_post():
    v = Venue()
    p = path(venue=v)
    _at_lock(p)
    assert p.on_book_state(OPEN)["lock"] == OV.LATCHED
    n = len(p.tp.posts)
    for _ in range(3):
        p.on_book_state(OPEN)
    assert len(p.tp.posts) == n, "an order reached POST after LOCK5 latched"
    assert p.eng.resting == {"up": None, "down": None}


# 7 ----------------------------------------------------------------------
def test_a_stale_feed_cannot_trade_and_a_reconnect_alone_does_not_free_it():
    f = Feed(fresh=False, age=212.662)        # the Rung-A gap, exactly
    p = path(feed=f)
    out = p.on_book_state(OPEN)
    assert out["refused"] == "BOOK_NOT_FRESH", out
    assert p.tp.posts == [], "traded on a stale book"
    f._fresh = True                            # a genuinely fresh book arrives
    assert p.on_book_state(OPEN)["acted"] is True
    assert p.tp.posts, "a fresh book still could not trade"


def test_a_non_open_or_unreadable_book_refuses():
    p = path()
    assert p.on_book_state({**OPEN, "state": "MARKET_STATE_CLOSED"})[
        "refused"] == "STALE_BOOK"
    assert p.on_book_state({**OPEN, "bid": None, "ask": None})[
        "refused"] == "STALE_BOOK"
    assert p.tp.posts == []


# 8 ----------------------------------------------------------------------
def test_the_preexisting_inventory_guard_still_reads_canonical_sides(tmp_path):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.apply_fill(MARKET, "OUTCOME_SIDE_YES", D(1), D("0.4"), basis="OWN")
    assert list(st.state["positions"]) == ["%s|UP" % MARKET]
    asked = [EX.canonical_side(s, doing="test") for s in ("up", "down")]
    assert any(st.position(MARKET, a) != 0 for a in asked)
    assert all(st.position(MARKET, s) == 0 for s in ("up", "down"))


# 9 ----------------------------------------------------------------------
def test_the_generic_translator_is_unreachable_from_the_money_path():
    import ast
    E.assert_no_translate_fallback()
    for mod in ("src/pmus_native_money_path.py",
                "src/pmus_native_live_overlay.py"):
        tree = ast.parse(open(mod).read())
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
            assert banned not in imported, (mod, banned)


# 10 ---------------------------------------------------------------------
def test_the_mandate_bounds_every_order_path():
    """Both the passive and the taker leg go through the same transport."""
    tp = Transport(refuse_over=D("1.00"))
    v = Venue()
    p = path(venue=v, transport=tp)
    with pytest.raises(RuntimeError, match="MANDATE_REFUSED"):
        p.on_book_state(OPEN)                  # a 50-clip at 0.40 is $20
    assert tp.posts == []

    tp2 = Transport(refuse_over=D("1.00"))
    p2 = path(venue=v, transport=tp2)
    p2.eng.on_fill("up", D(10), D("4.00"), maker=True)
    v.completion = (D(10), D("2.00"), D("0.07"))
    with pytest.raises(RuntimeError, match="MANDATE_REFUSED"):
        p2.on_book_state(OPEN, {"t": OPEN["t"], "bids": [["0.80", "10"]],
                                "offers": []})
    assert tp2.posts == [], "a taker completion bypassed the mandate"


def test_every_order_is_built_by_the_certified_builder():
    p = path()
    p.on_book_state(OPEN)
    for b in p.tp.posts:
        assert b["marketSlug"] == MARKET
        assert b["outcomeSide"] in ("OUTCOME_SIDE_YES", "OUTCOME_SIDE_NO")
        assert b["action"] == "ORDER_ACTION_BUY"
        assert D(str(b["price"]["value"])) > 0
