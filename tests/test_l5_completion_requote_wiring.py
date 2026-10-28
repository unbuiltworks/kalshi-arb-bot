"""P2 integration: the requote CANCELS; the complement path re-places.

THE SHAPE OF THE INTEGRATION, and why it is this small. `complement_order`
already prices against the CURRENT book from the store's realised leg costs,
so a complement re-placed after a cancellation is automatically repriced and
resized on the inventory that remains. The defect was never the pricing of a
NEW complement -- it was that a RESTING one was left frozen while the market
walked 12 to 18 ticks away from it. So the integration adds a cancellation
and nothing else.

WHY TWO COMPETING ORDERS ARE STRUCTURALLY IMPOSSIBLE, not merely avoided.
The replacement is the next ATTEMPT of the same leg of the same round.
`acc_leg_attempt` returns None -- refusing the placement outright -- while
any order is live, and charges the attempt to the REPLACE budget once it is
not. The only route to a replacement is therefore through a cancellation
this method has already proved by re-read.

These drive the REAL Runner.maybe_completion_requote.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TSP

SLUG = "cpc-btc-updown-15m-2026-10-28-1215z"
WS = 1_793_189_700.0
NOW = WS + 533.0


class FakeTransport:
    tick_size = D("0.01")
    quantity_increment = D("0.01")

    def __init__(self):
        self.market_slug = SLUG
        self.armed = True
        self.cancelled = []
        self.reread = 0

    def cancel(self, venue_order_id, market_slug=None):
        self.cancelled.append(venue_order_id)
        return {"ok": True, "venue_order_id": venue_order_id}

    def order(self, venue_order_id):
        self.reread += 1
        return {"id": venue_order_id, "state": "ORDER_STATE_CANCELED",
                "cumQuantity": 0, "leavesQuantity": 0,
                "avgPx": {"value": "0", "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}


class FakeStore:
    """Only what maybe_completion_requote touches."""

    def __init__(self, orders=None, up="4.21", dn="1.92",
                 up_cost="1.5547", dn_cost="0.7488", uncertain=None):
        self._orders = orders if orders is not None else {
            "coid-1": {"venue_order_id": "v1", "market": SLUG,
                       "side": "DOWN", "qty": "2.29", "price": "0.47",
                       "remaining": "2.29"}}
        self._pos = {"UP": D(up), "DOWN": D(dn)}
        self._legs = {"UP": D(up_cost), "DOWN": D(dn_cost)}
        self._uncertain = uncertain
        self.uncertain = []
        self.applied = []

    def live_orders(self):
        return {k: dict(v) for k, v in self._orders.items()}

    def position(self, market, side):
        return self._pos[side]

    def leg_cost(self, market):
        return dict(self._legs)

    def residual(self, market):
        return self._pos["UP"] - self._pos["DOWN"]

    def outstanding_residual(self, market):
        r = D(0)
        for o in self._orders.values():
            rem = D(str(o.get("remaining") or 0))
            r += rem if o.get("side") == "UP" else -rem
        return r

    def orders_uncertain(self):
        return self._uncertain

    def mark_uncertain(self, why):
        self.uncertain.append(why)
        self._uncertain = why

    def apply_order_progress(self, *a, **k):
        """`deadman` reads the booked delta off this, so a double that
        returns None is not modelling the store -- it is how the first draft
        of these tests accused the policy of an AttributeError it had not
        made. The real method books the fill and reports the delta."""
        self.applied.append((a, k))
        return {"filled_delta": "0", "delta": "0"}

    def save(self):
        return None


class Book:
    def __init__(self, bid="0.4900", ask="0.5100", age="0.05", gap=False):
        self._s = {"bid": bid, "ask": ask, "stream_age_s": age,
                   "gap_suspected": gap}

    def snapshot(self, slug):
        return dict(self._s)


class Executor:
    def __init__(self, reducing="DOWN", deficit="3.00"):
        self._red = reducing

        class VG:
            max_worst_case_deficit_usd = D(deficit)
        self.validation_gates = VG()

    def probe_reducing_side(self, market):
        return self._red


class Stub:
    """Only the attributes the real method reads."""

    def __init__(self, **kw):
        import collections
        self.transport = FakeTransport()
        self.store = FakeStore(**{k: v for k, v in kw.items()
                                  if k in ("orders", "up", "dn", "up_cost",
                                           "dn_cost", "uncertain")})
        self.book = Book(**{k: v for k, v in kw.items()
                            if k in ("bid", "ask", "age", "gap")})
        self.executor = Executor(**{k: v for k, v in kw.items()
                                    if k in ("reducing", "deficit")})
        self.stats = collections.Counter()


@pytest.fixture(autouse=True)
def armed(monkeypatch):
    monkeypatch.setenv(TSP.EXPERIMENT_ENV, "TS-REQUOTE")
    monkeypatch.setenv(TSP.MARKET_ENV, SLUG)
    monkeypatch.setenv(R.COMPLETION_REQUOTE_ENV, "1")
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start", lambda s: WS)
    emitted = []
    monkeypatch.setattr(R, "emit",
                        lambda label, rec: emitted.append((label, rec)))
    return emitted


def run(**kw):
    s = Stub(**kw)
    return s, R.Runner.maybe_completion_requote(s, NOW)


# ------------------------------------------------------- it acts

def test_it_cancels_the_exact_order_by_venue_id_and_proves_it(armed):
    s, rec = run()
    assert rec is not None, s.stats
    assert s.transport.cancelled == ["v1"]       # by VENUE id, exactly one
    assert s.transport.reread == 1               # and proved by re-read
    assert rec["from_price"] == "0.47"
    assert D(rec["to_price"]) == D("0.49")
    assert [l for l, _ in armed] == ["L5_TS_REQUOTE"]


def test_it_submits_NOTHING(armed):
    """The cancel is the whole action. A replacement that raced this would
    be two competing completion orders."""
    s, rec = run()
    assert rec is not None
    assert not hasattr(s.transport, "sent")
    assert "submit" not in str(rec).lower() or "No fill" in str(rec)


# ------------------------------------------------- it must not act

def test_it_is_INERT_without_the_flag(monkeypatch):
    monkeypatch.delenv(R.COMPLETION_REQUOTE_ENV, raising=False)
    s, rec = run()
    assert rec is None
    assert s.transport.cancelled == []


def test_a_disarmed_transport_does_nothing():
    s = Stub()
    s.transport.armed = False
    assert R.Runner.maybe_completion_requote(s, NOW) is None
    assert s.transport.cancelled == []


def test_an_uncertain_order_state_refuses():
    s, rec = run(uncertain="UNVERIFIED_SUBMIT")
    assert rec is None
    assert s.transport.cancelled == []
    assert s.stats["requote_refused:ORDERS_UNCERTAIN"] == 1


def test_a_stale_book_refuses():
    s, rec = run(age="9.0")
    assert rec is None and s.stats["requote_refused:BOOK_STALE"] == 1


def test_a_suspected_gap_refuses():
    s, rec = run(gap=True)
    assert rec is None and s.stats["requote_refused:GAP_SUSPECTED"] == 1


def test_an_order_with_no_venue_id_refuses():
    s, rec = run(orders={"c": {"venue_order_id": "", "market": SLUG,
                               "side": "DOWN", "qty": "2.29",
                               "price": "0.47", "remaining": "2.29"}})
    assert rec is None
    assert s.stats["requote_refused:NO_VENUE_ID"] == 1


def test_two_resting_orders_refuse_rather_than_guess():
    o = {"a": {"venue_order_id": "v1", "market": SLUG, "side": "DOWN",
               "qty": "1", "price": "0.47", "remaining": "1"},
         "b": {"venue_order_id": "v2", "market": SLUG, "side": "DOWN",
               "qty": "1", "price": "0.47", "remaining": "1"}}
    s, rec = run(orders=o)
    assert rec is None and s.transport.cancelled == []


def test_no_resting_order_is_nothing_to_requote():
    s, rec = run(orders={})
    assert rec is None and s.transport.cancelled == []


def test_a_first_leg_is_not_a_completion():
    """The resting order is on the side we already HOLD, so it adds to the
    residual rather than reducing it: an opening that has not filled, owned
    by the TTL and terminal deadlines. Modelled on the order's side against
    the realised residual, which is what the runner reads."""
    s, rec = run(orders={"c": {"venue_order_id": "v1", "market": SLUG,
                               "side": "UP", "qty": "1", "price": "0.40",
                               "remaining": "1"}})
    assert rec is None
    assert s.stats["requote_refused:NOT_THE_REDUCING_LEG"] == 1


def test_a_matched_book_refuses():
    s, rec = run(up="1.92", dn="1.92")
    assert rec is None and s.transport.cancelled == []


def test_an_order_in_another_market_is_untouched():
    s, rec = run(orders={"c": {"venue_order_id": "v9", "market": "other",
                               "side": "DOWN", "qty": "1", "price": "0.47",
                               "remaining": "1"}})
    assert rec is None and s.transport.cancelled == []


def test_a_quote_already_at_the_touch_is_left_alone():
    s, rec = run(orders={"c": {"venue_order_id": "v1", "market": SLUG,
                               "side": "DOWN", "qty": "2.29",
                               "price": "0.49", "remaining": "2.29"}})
    assert rec is None
    assert s.stats["requote_refused:ALREADY_NEAR_TOUCH"] == 1


# ---------------------------------- partial fills and stale acks

def test_a_partial_fill_requotes_only_the_working_remainder():
    """1.00 of 2.29 filled: DOWN now holds 1.00, so the residual is 1.29
    and the replacement may carry no more."""
    s, rec = run(dn="2.92", up="4.21",
                 orders={"c": {"venue_order_id": "v1", "market": SLUG,
                               "side": "DOWN", "qty": "2.29",
                               "price": "0.47", "remaining": "1.29"}})
    assert rec is not None, s.stats
    assert D(rec["qty"]) <= D("1.29")
    assert D(rec["unmatched"]) == D("1.29")


def test_a_fully_filled_order_is_not_requoted():
    """The cancellation race: it filled between the read and the decision."""
    s, rec = run(dn="4.21", up="4.21",
                 orders={"c": {"venue_order_id": "v1", "market": SLUG,
                               "side": "DOWN", "qty": "2.29",
                               "price": "0.47", "remaining": "0"}})
    assert rec is None
    assert s.transport.cancelled == []


def test_an_unreadable_remaining_is_not_treated_as_zero():
    """`worst_case_remaining` takes the conservative reading, so an
    unreadable row still counts as working rather than vanishing."""
    s, rec = run(orders={"c": {"venue_order_id": "v1", "market": SLUG,
                              "side": "DOWN", "qty": "2.29",
                              "price": "0.47"}})
    assert rec is not None, s.stats
    assert D(rec["qty"]) > 0


def test_a_cancel_whose_reread_says_still_working_marks_uncertain():
    """A stale acknowledgment is the dangerous case. deadman marks the store
    UNCERTAIN, which refuses every later submission -- so a failed requote
    cannot leave a replacement racing a live order."""
    s = Stub()

    def still_working(vid):
        return {"id": vid, "state": "ORDER_STATE_NEW",
                "cumQuantity": 0, "leavesQuantity": 2.29,
                "avgPx": {"value": "0", "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}
    s.transport.order = still_working
    R.Runner.maybe_completion_requote(s, NOW)
    assert s.store.uncertain, "an unproven cancel must mark UNCERTAIN"


# --------------------------------------------- timing and runway

def test_past_the_completion_cutoff_it_refuses():
    s = Stub()
    rec = R.Runner.maybe_completion_requote(
        s, WS + TSP.COMPLETE_CUTOFF_S + 1.0)
    assert rec is None
    assert s.transport.cancelled == []


def test_too_little_runway_to_cancel_and_replace_refuses():
    s = Stub()
    rec = R.Runner.maybe_completion_requote(
        s, TSP.cancel_cutoff(WS + TSP.WINDOW_S) - 5.0)
    assert rec is None
    assert s.stats["requote_refused:INSUFFICIENT_REQUOTE_RUNWAY"] == 1


def test_repeated_requotes_each_need_their_own_eligibility():
    """Called again with the quote now AT the touch, it declines: the rule
    cannot ratchet on a quote it has already fixed."""
    s, rec = run()
    assert rec is not None
    s.store._orders = {"c": dict(s.store._orders.get("coid-1", {}),
                                 price=rec["to_price"],
                                 venue_order_id="v2")}
    again = R.Runner.maybe_completion_requote(s, NOW)
    assert again is None


def test_an_unreadable_window_refuses(monkeypatch):
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start", lambda s: None)
    s, rec = run()
    assert rec is None
    assert s.stats["requote_refused:WINDOW_UNKNOWN"] == 1


def test_an_exception_anywhere_is_counted_and_never_raised():
    s = Stub()

    def boom(*a, **k):
        raise RuntimeError("book exploded")
    s.book.snapshot = boom
    assert R.Runner.maybe_completion_requote(s, NOW) is None
    assert any(k.startswith("requote_error:") for k in s.stats)


def test_the_replacement_price_would_not_cross(armed):
    """The cancel half must only ever target a passive price."""
    s, rec = run()
    assert rec is not None
    assert TSP.leg_crosses("DOWN", D(rec["to_price"]),
                           bid=D("0.4900"), ask=D("0.5100")) is False
