"""Causal persistent-order simulator on the authenticated event stream.

NO MONEY. NO LIVE ORDERS. Every fill is earned against a real print.

THE CAUSAL CONTRACT, which is the whole point of this module:

  * An order placed at T uses only events with receipt time <= T. The stream is
    consumed strictly forward and nothing looks ahead.
  * Queue-ahead is initialised from the depth DISPLAYED AT OUR EXACT PRICE at
    the moment of placement. If the book does not show our level, the queue is
    unknowable and we do not rest -- we never guess a position.
  * Queue is decremented ONLY by prints at or through our price. A print at a
    better price never reached us and is not counted. Volume is consumed once.
  * A SINGLE print both consumes the queue and fills us. The print quantity is
    applied to `queue_ahead` first; whatever remains of THAT SAME print fills
    our order, up to what we still want. No separate later print is required --
    requiring one would understate fills, since in reality the tail of the
    order that cleared the queue is what trades against us. Partial fills are
    the normal case, not an exception.
  * TOUCH IS NEVER A FILL. DISPLAYED LIQUIDITY IS NEVER A FILL.
  * A re-price sends us to the BACK of the new level's queue, because that is
    what a cancel/replace does at a venue. Persistence therefore has a real
    cost and is not free.

WHAT IT MAINTAINS: two-sided inventory continuously, aggregate economic basis
including taker fees already paid, matched and residual quantity, fees, credited
incentives kept on their own line, and settlement at the window's own outcome.

THREE MECHANISMS, switchable independently so they can be measured apart:
  persistent_maker   a complement priced from basis stays resting; it is only
                     re-priced when the basis moves it, never because the touch
                     moved.
  reactive_take      when the contemporaneous displayed depth offers the whole
                     residual at a fee-safe price, take it.
  basis_extend       same-side acquisition allowed only when it LOWERS the
                     aggregate economic basis, inside the residual bound.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_FLOOR
from typing import Any, Dict, List, Optional, Tuple

from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E
from src import l5_event_stream as ES

#: credited, sourced, maker-only. 25 passiveExecution prints, 119.49 contracts,
#: $0.42. Never modelled, never assumed -- this is what was actually paid.
SOURCED_REBATE_PER_CONTRACT = D("0.42") / D("119.49")


def _floor_tick(x: D) -> D:
    return (x / E.PRICE_TICK).to_integral_value(rounding=ROUND_FLOOR) \
        * E.PRICE_TICK


class Order:
    """One resting maker order with a causally-earned queue position."""

    __slots__ = ("side", "px", "qty", "ahead", "filled", "placed_t")

    def __init__(self, side: str, px: D, qty: D, ahead: D, t: float) -> None:
        self.side, self.px, self.qty = side, px, qty
        self.ahead, self.filled, self.placed_t = ahead, D(0), t

    def remaining(self) -> D:
        return self.qty - self.filled


class CausalSim:
    def __init__(self, *, clip: D, persistent_maker: bool = True,
                 reactive_take: bool = False, basis_extend: bool = False,
                 residual_cap_k: D = E.RESIDUAL_CAP_K) -> None:
        self.clip = D(str(clip))
        self.persistent_maker = persistent_maker
        self.reactive_take = reactive_take
        self.basis_extend = basis_extend
        self.residual_cap_k = D(str(residual_cap_k))
        self.bk = S.Book()
        self.resting: Dict[str, Optional[Order]] = {"up": None, "down": None}
        self.book: Dict[str, Optional[D]] = {"bid": None, "ask": None,
                                             "bid_qty": None, "ask_qty": None}
        #: FULL LADDER when the feed carries one: {venue_px: qty} per side.
        #: This is the upgrade that makes exact-level queue initialisation
        #: possible away from the touch -- with top of book alone we could only
        #: rest where our price happened to equal the touch.
        self.ladder: Dict[str, Dict[D, D]] = {"bids": {}, "offers": {}}
        self.fills: List[dict] = []
        self.requotes = 0
        self.cancels = 0
        self.places = 0
        self.takes = 0
        self.peak_cash = D(0)
        self.cash = D(0)
        self.worst_residual = D(0)

    # ------------------------------------------------------------ economics

    def basis(self, side: str) -> D:
        return self.bk.economic_basis(side)

    def complement_cap(self, side: str) -> Optional[D]:
        """Richest price at which resting `side` still pairs at <= theta."""
        other = "down" if side == "up" else "up"
        if self.bk.q[other] <= self.bk.q[side]:
            return None
        return _floor_tick(E.THETA - self.basis(other))

    def displayed_at(self, side: str, px: D) -> Optional[D]:
        """Depth shown AT OUR EXACT PRICE, in venue terms, right now.

        With a FULL LADDER this is a direct observation at any level. With top of
        book only it can be answered solely where our price equals the touch, and
        everywhere else the queue is unknowable -- so we return None and the
        caller refuses to rest rather than guess a position. That refusal is the
        conservative direction and it is deliberate.

        A level the FULL ladder does not show means something quite different
        from an unobservable level, and conflating the two was a real bug: with a
        complete ladder, absence is EMPTY, so an order there is ALONE AND FIRST
        IN QUEUE -- queue_ahead = 0, the best position available, not an unknown
        one. Refusing to rest there made the engine deadlock after its first
        fill, because a basis-derived complement price is usually not already
        quoted. Absence is unknowable only when the feed shows the touch alone.
        """
        venue = px if side == "up" else (D(1) - px)
        book_side = "bids" if side == "up" else "offers"
        if self.ladder[book_side]:
            return self.ladder[book_side].get(venue, D(0))
        if side == "up":
            if self.book["bid"] is not None and px == self.book["bid"]:
                return self.book["bid_qty"]
            return None
        if self.book["ask"] is not None and venue == self.book["ask"]:
            return self.book["ask_qty"]
        return None

    def _cap_permits(self, side: str, qty: D) -> bool:
        r = self.bk.residual()
        after = r + (qty if side == "up" else -qty)
        if r != 0 and abs(after) > abs(r):
            return abs(after) <= self.residual_cap_k * self.clip
        return True

    # --------------------------------------------------------------- events

    def on_book(self, row: dict, t: float) -> None:
        lad = row.get("_ladder")
        if lad is not None:
            self.ladder = lad
        self.book["bid"] = ES._d(row.get("bid"))
        self.book["ask"] = ES._d(row.get("ask"))
        self.book["bid_qty"] = ES._d(row.get("bid_qty"))
        self.book["ask_qty"] = ES._d(row.get("ask_qty"))
        if str(row.get("status") or "") not in ("OK", "ONE_SIDED"):
            return
        self._reconcile(t)
        if self.reactive_take:
            self._maybe_take(t)

    def on_trade(self, row: dict, t: float) -> None:
        """A real print. The ONLY thing that can fill us."""
        px = ES._d(row.get("px"))
        qty = ES._d(row.get("qty"))
        if px is None or qty is None or qty <= 0:
            return
        for side in ("up", "down"):
            o = self.resting.get(side)
            if o is None:
                continue
            # our own terms: buying UP prints at px; buying DOWN at 1-px
            own = S.own_price(side, px)
            if own > o.px:
                continue            # printed away from us; we were not reached
            if o.ahead > 0:
                take = qty if qty < o.ahead else o.ahead
                o.ahead -= take
                qty -= take
                if qty <= 0:
                    continue        # the queue absorbed the whole print
            got = qty if qty < o.remaining() else o.remaining()
            if got <= 0:
                continue
            if not self._cap_permits(side, got):
                continue
            cost = got * o.px
            self.bk.add(side, got, cost, maker=True)
            self.cash += cost
            if self.cash > self.peak_cash:
                self.peak_cash = self.cash
            o.filled += got
            self.fills.append({"t": t, "side": side, "px": str(o.px),
                               "qty": str(got), "maker": True,
                               "placed_t": o.placed_t})
            if o.remaining() <= 0:
                self.resting[side] = None
        r = abs(self.bk.residual())
        if r > self.worst_residual:
            self.worst_residual = r
        self._reconcile(t)

    # ------------------------------------------------------------ decisions

    def _desired(self, side: str) -> Optional[D]:
        cap = self.complement_cap(side)
        if cap is not None:
            if not self.persistent_maker:
                return None
            return cap if cap >= E.PRICE_TICK else None
        # not a complement: OPEN from flat, or an EXTEND of the long side
        own = (self.book["bid"] if side == "up" else
               (D(1) - self.book["ask"] if self.book["ask"] is not None
                else None))
        if own is None or own <= 0 or own >= 1:
            return None
        if self.bk.residual() != 0:
            rr = self.bk.residual()
            if (rr > 0) == (side == "up"):
                if not self.basis_extend:
                    return None
                # ONLY if it lowers the aggregate economic basis
                if self.bk.q[side] > 0 and own >= self.basis(side):
                    return None
                if not self._cap_permits(side, self.clip):
                    return None
        return own

    def _reconcile(self, t: float) -> None:
        for side in ("up", "down"):
            want = self._desired(side)
            cur = self.resting.get(side)
            if want is None:
                if cur is not None:
                    self.resting[side] = None
                    self.cancels += 1
                continue
            if cur is not None and cur.px == want:
                continue            # already there; keep our queue position
            shown = self.displayed_at(side, want)
            if shown is None:
                if cur is not None:
                    self.resting[side] = None
                    self.cancels += 1
                continue
            if cur is not None:
                self.requotes += 1
            self.places += 1
            # a replace joins the BACK of the new level's queue
            self.resting[side] = Order(side, want, self.clip, shown, t)

    def _maybe_take(self, t: float) -> None:
        """Cross for the whole residual when displayed depth is fee-safe."""
        r = self.bk.residual()
        if r == 0:
            return
        held = "up" if r > 0 else "down"
        comp = S.complement(held)
        want = abs(r)
        px = (self.book["ask"] if comp == "up" else
              (D(1) - self.book["bid"] if self.book["bid"] is not None
               else None))
        shown = (self.book["ask_qty"] if comp == "up" else self.book["bid_qty"])
        if px is None or shown is None or shown < want:
            return
        fee_unit = S.THETA_TAKER * px * (D(1) - px)
        if self.basis(held) + px + fee_unit > E.THETA:
            return
        cost = want * px
        fee = S.taker_fee(px, want)
        self.bk.add(comp, want, cost, maker=False, fee=fee)
        self.cash += cost
        if self.cash > self.peak_cash:
            self.peak_cash = self.cash
        self.takes += 1
        self.fills.append({"t": t, "side": comp, "px": str(px),
                           "qty": str(want), "maker": False, "placed_t": t})

    # ---------------------------------------------------------------- run

    def run(self, events, *, winner: Optional[str]) -> Dict[str, Any]:
        for t, kind, row in events:
            if kind == "book":
                self.on_book(row, t)
            else:
                self.on_trade(row, t)
        res = self.bk.settle(winner)
        mk = D(str(res["maker_qty"]))
        res["sourced_incentive"] = mk * SOURCED_REBATE_PER_CONTRACT
        res["net"] = (res["matched_pnl"] - res["fees"]
                      + (res["resid_pnl"] or D(0)))
        res["realistic_net"] = res["net"] + res["sourced_incentive"]
        res["peak_cash"] = self.peak_cash
        res["worst_residual"] = self.worst_residual
        res["n_fills"] = len(self.fills)
        res["maker_fills"] = sum(1 for f in self.fills if f["maker"])
        res["taker_fills"] = sum(1 for f in self.fills if not f["maker"])
        res["places"] = self.places
        res["requotes"] = self.requotes
        res["cancels"] = self.cancels
        res["takes"] = self.takes
        res["contracts"] = sum(D(f["qty"]) for f in self.fills)
        res["fills_detail"] = self.fills
        return res
