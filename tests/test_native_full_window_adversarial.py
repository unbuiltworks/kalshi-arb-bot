"""A whole window against a HOSTILE venue, with a real mandate budget.

This is the test that should have existed before any live attempt. It drives
`NativeMoneyPath` over a full 15-minute BTC15m window through a venue that is
adversarial about the four seams where a simulator and a real venue differ:

  1. an ACK is not a fill, and fills arrive LATE and out of band;
  2. a cancel request is not a cancellation -- sometimes it does not take;
  3. an order can PARTIALLY fill, and the remainder stays live;
  4. every submission costs budget the moment it is admitted.

The mandate is the REAL `pmus_live_mandate` guard over a real state dir, so
budget exhaustion is measured rather than assumed.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import pmus_l5_native_engine as E
from src import pmus_live_mandate as MANDATE
from src import pmus_native_money_path as MP

MARKET = "cpc-btc-updown-15m-2026-09-28-1830z"
MANDATE_RAW = {
    "authorization_id": "OWNER-ADVERSARIAL", "authorized_at": "2026-09-28T00:00:00Z",
    "service": "l5-live-probe-oneshot",
    "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
    "strategy_version": "adversarial",
    "max_markets": 1, "max_total_markets": 1,
    "max_spend_per_market": "81.00", "max_total_spend": "81.00",
    "max_residual": "20.00", "max_daily_realized_loss": "20.00",
    "expiry": "2099-01-01T00:00:00Z",
}


class HostileVenue:
    """Everything a real venue does that a simulator does not."""

    def __init__(self, *, fill_every=4, partial=False, cancel_sticks=True,
                 late_fills=True):
        self.orders = {}          # oid -> {side, px, qty, filled, live}
        self.fill_every = fill_every
        self.partial = partial
        self.cancel_sticks = cancel_sticks
        self.late_fills = late_fills
        self.resting_ids = {"up": None, "down": None}
        self._reported = set()
        self._n = 0
        self.cancel_calls = 0

    # -- what the money path calls
    def cancel_order(self, oid):
        self.cancel_calls += 1
        o = self.orders.get(str(oid))
        if o and self.cancel_sticks:
            o["live"] = False

    def cancel_resting(self, side):
        oid = self.resting_ids.get(side)
        if oid:
            self.cancel_order(oid)

    def open_orders(self):
        return [{"orderId": k, "marketSlug": MARKET}
                for k, o in self.orders.items() if o["live"]]

    def unbooked_fills(self):
        out = []
        for k, o in self.orders.items():
            if o["filled"] > 0 and k not in self._reported:
                self._reported.add(k)
                out.append({"side": o["side"], "qty": o["filled"],
                            "cost": o["filled"] * o["px"], "maker": True,
                            "fee": None})
        return out

    def filled_qty(self, oid):
        o = self.orders.get(str(oid))
        return None if o is None else o["filled"]

    def completion_fill(self, intent):
        return D(str(intent["qty"])), D(str(intent["cost"])), D(str(intent["fee"]))

    # -- the venue's own clock
    def accept(self, body):
        self._n += 1
        oid = "v%d" % self._n
        side = "up" if body["outcomeSide"] == "OUTCOME_SIDE_YES" else "down"
        wire = D(str(body["price"]["value"]))
        own = wire if side == "up" else (D(1) - wire)
        self.orders[oid] = {"side": side, "px": own,
                            "qty": D(str(body["quantity"])), "filled": D(0),
                            "live": True}
        # every Nth order fills, LATE (reported on a later tick, not this one)
        if self.fill_every and self._n % self.fill_every == 0:
            o = self.orders[oid]
            o["filled"] = (o["qty"] / 2) if self.partial else o["qty"]
            if not self.partial:
                o["live"] = False
        return {"orderId": oid, "state": "ORDER_STATE_OPEN"}


class GuardedVenueTransport:
    """The REAL mandate guard in front of the hostile venue."""

    def __init__(self, venue, state_dir, mandate):
        self.venue = venue
        self.inner = self
        self.g = MANDATE.guard(self, mandate, state_dir,
                               log=lambda k, **kw: None)
        self.refusals = []

    def request(self, method, path, body=None, **kw):
        return self.venue.accept(body)


def _window_rows(n=180, start=1790612200.0):
    """A moving two-sided book: the touch walks, so the engine re-quotes."""
    rows = []
    for i in range(n):
        bid = D("0.40") + (D(i % 7) * D("0.01"))
        ask = bid + D("0.02")
        rows.append({"state": "MARKET_STATE_OPEN",
                     "t": "2026-09-28T18:%02d:%02dZ" % (30 + i // 12, (i * 5) % 60),
                     "bid": str(bid), "ask": str(ask)})
    return rows


def _run_window(tmp_path, venue, clip=D(10), rows=None):
    m, err = MANDATE.parse_mandate(json.dumps(MANDATE_RAW))
    assert m is not None, err
    gt = GuardedVenueTransport(venue, tmp_path, m)
    mp = MP.NativeMoneyPath(
        market=MARKET,
        engine=E.NativeEngine(clip=clip, lock_gate=lambda *a, **k: False),
        transport=gt.g, feed=None, venue=venue)
    refused = 0
    for row in (rows or _window_rows()):
        try:
            mp.on_book_state(row, {"t": row["t"], "bids": [["0.80", "50"]],
                                   "offers": [["0.20", "50"]]})
        except MANDATE.MandateRefusal:
            refused += 1
    sweep = mp.sweep_all()
    return mp, sweep, refused


# 1 ------------------------------------------------- fills are actually booked
def test_authenticated_maker_fills_reach_the_book_during_ordinary_operation(
        tmp_path):
    """The defect this file was written for: fills were booked only inside a
    LOCK transaction, so inventory stayed at zero all window."""
    v = HostileVenue(fill_every=3)
    mp, sweep, _ = _run_window(tmp_path, v)
    assert mp.booked_fills, "no authenticated fill ever reached the book"
    # NOT end-of-window inventory: theta-completions retire matched pairs, so
    # finishing flat is the policy working. What must be non-zero is the
    # cumulative maker quantity the book actually absorbed.
    assert mp.eng.bk.maker_qty > 0, "fills were reported but never absorbed"
    assert mp.eng.bk.completions > 0 or mp.eng.bk.q["up"] + mp.eng.bk.q["down"] > 0


# 2 ---------------------------------------------- cancels are real, and confirmed
def test_requoting_cancels_the_previous_order_at_the_venue(tmp_path):
    v = HostileVenue(fill_every=0)
    mp, sweep, _ = _run_window(tmp_path, v)
    assert v.cancel_calls > 0, "re-quoting never cancelled anything"
    assert sweep["clean"] is True, sweep
    assert v.open_orders() == [], v.open_orders()


def test_a_cancel_that_does_not_take_is_not_treated_as_cancelled(tmp_path):
    v = HostileVenue(fill_every=0, cancel_sticks=False)
    mp, sweep, _ = _run_window(tmp_path, v)
    assert sweep["clean"] is False, "a stuck cancel was reported clean"
    assert any(c.get("state") == "STILL_LIVE"
               for c in sweep["cancelled"]), sweep["cancelled"]
    # and the order stays TRACKED and charged, not forgotten
    assert any(mp.live_orders[s] for s in ("up", "down"))


# 3 ------------------------------------------------------- budget is released
def test_a_confirmed_cancel_releases_its_unfilled_budget(tmp_path):
    v = HostileVenue(fill_every=0)
    mp, sweep, refused = _run_window(tmp_path, v)
    assert mp.terminals, "no terminal row was ever written"
    used = MANDATE.consumption(tmp_path, "OWNER-ADVERSARIAL")
    spent = D(str(used["per_market"].get(MARKET, "0")))
    assert spent == 0, (
        "cancelled, never-filled quotes still consume %s of budget" % spent)


def test_the_window_is_not_starved_by_quote_commitment(tmp_path):
    """THE DEFECT THAT WOULD HAVE WASTED THE LIVE RUN: without release, ~16
    quotes exhaust $81 and the rest of the window refuses."""
    v = HostileVenue(fill_every=0)
    mp, sweep, refused = _run_window(tmp_path, v)
    assert len(mp.submitted) > 30, (
        "only %d orders got out before the budget starved" % len(mp.submitted))
    assert refused == 0, "%d submissions were refused for budget" % refused


def test_a_fill_is_permanent_and_is_never_released(tmp_path):
    v = HostileVenue(fill_every=3)
    mp, sweep, _ = _run_window(tmp_path, v)
    used = MANDATE.consumption(tmp_path, "OWNER-ADVERSARIAL")
    spent = D(str(used["per_market"].get(MARKET, "0")))
    assert spent > 0, "filled inventory released its cash, which is wrong"


# 4 --------------------------------------------------- the mandate still binds
def test_the_mandate_is_never_exceeded_however_the_window_goes(tmp_path):
    for kw in ({"fill_every": 1}, {"fill_every": 2, "partial": True},
               {"fill_every": 0, "cancel_sticks": False}):
        d = tmp_path / ("m%d" % abs(hash(str(kw))))
        d.mkdir()
        v = HostileVenue(**kw)
        mp, sweep, refused = _run_window(d, v)
        used = MANDATE.consumption(d, "OWNER-ADVERSARIAL")
        spent = D(str(used["per_market"].get(MARKET, "0")))
        assert spent <= D("81.00"), (kw, str(spent))


# 5 ------------------------------------------------- nothing is left resting
def test_no_order_is_left_resting_after_the_window(tmp_path):
    v = HostileVenue(fill_every=5)
    mp, sweep, _ = _run_window(tmp_path, v)
    assert sweep["open_orders_after"] == 0, sweep
    assert all(mp.live_orders[s] is None for s in ("up", "down"))


def test_a_partial_fill_leaves_no_untracked_remainder(tmp_path):
    v = HostileVenue(fill_every=2, partial=True)
    mp, sweep, _ = _run_window(tmp_path, v)
    assert sweep["open_orders_after"] == 0, sweep
    assert mp.eng.bk.maker_qty > 0, "the partial fills were never booked"


# 6 ----------------------------------- THE REAL FEED ROW SHAPE, not a hand dict
#
# The 1830z live probe submitted nothing, refused nothing and proved nothing
# because `AuthenticatedBookFeed` rows come from `l5_window_actions.book_row`,
# which carries `status` (OK / ONE_SIDED / NO_BOOK) and NO `state` key, while
# the engine required `state == "MARKET_STATE_OPEN"`. Every tick raised
# StaleBook. Every test above had fed it a hand-written dict carrying the field
# the engine wanted -- my assumption tested against itself.
#
# These use the production builder. If the row shape changes, these fail.

from src import l5_window_actions as WA           # noqa: E402
from src import pmus_l5_native_engine as _E       # noqa: E402


def _live_row(bid="0.40", ask="0.60"):
    return WA.book_row(t=1790612400.0, bid=bid, ask=ask,
                       bid_qty="50", ask_qty="50")


def test_the_real_feed_row_is_usable_by_the_engine():
    row = _live_row()
    assert "state" not in row, "premise: the live row has no `state` key"
    assert _E.usable_book(row) is True, row
    eng = _E.NativeEngine(clip=D(10))
    out = eng.on_book(row)
    rests = [i for i in out if i.kind == _E.REST]
    assert len(rests) == 2, out


def test_a_one_sided_real_row_still_quotes_the_quoted_side():
    eng = _E.NativeEngine(clip=D(10))
    out = eng.on_book(_live_row(ask=None))
    assert {i["side"] for i in out if i.kind == _E.REST} == {"up"}, out


def test_a_no_book_real_row_fails_closed():
    eng = _E.NativeEngine(clip=D(10))
    with pytest.raises(_E.StaleBook):
        eng.on_book(_live_row(bid=None, ask=None))


def test_a_row_stating_neither_state_nor_status_fails_closed():
    eng = _E.NativeEngine(clip=D(10))
    with pytest.raises(_E.StaleBook):
        eng.on_book({"t": 1.0, "bid": "0.40", "ask": "0.60"})


def test_a_full_window_of_real_feed_rows_actually_submits(tmp_path):
    """The end-to-end claim the 1830z run failed to make."""
    rows = []
    for i in range(60):
        bid = D("0.40") + (D(i % 5) * D("0.01"))
        rows.append(WA.book_row(t=1790612400.0 + i * 5, bid=str(bid),
                                ask=str(bid + D("0.02")),
                                bid_qty="50", ask_qty="50"))
    v = HostileVenue(fill_every=4)
    mp, sweep, refused = _run_window(tmp_path, v, rows=rows)
    # re-quotes happen only when the touch MOVES, so the count tracks the
    # book's own movement rather than the tick count. Both sides must trade.
    assert len(mp.submitted) >= 10, (
        "only %d orders on real feed rows" % len(mp.submitted))
    assert {r["side"] for r in mp.submitted} == {"up", "down"}
    assert mp.booked_fills, "no fill was booked on real feed rows"
    assert sweep["clean"] is True, sweep
    assert refused == 0, refused
