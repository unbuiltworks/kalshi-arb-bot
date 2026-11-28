"""Do cheap repairs get left behind? A read-only study. No orders, no fills.

THE QUESTION
------------
Probe 08 offered a repair at 0.56 and the repairs that actually filled were
0.75, 0.76 and 0.80. One window. This asks whether that is representative by
taking every maker-copyable L5 action that WOULD be a repair under realized-
inventory Complement Repair, resting a hypothetical order at L5's own price,
and watching the authenticated tape.

WHAT COUNTS AS THE INVENTORY STATE, AND WHY
-------------------------------------------
Classifying an action as a repair needs a realized inventory to repair. We
have none of our own in these windows -- nothing was ever placed -- and
inventing one would mean inferring our own fills, which is exactly what this
study is forbidden to do.

So the trajectory is L5's OWN realized inventory. His activity rows are
completed trades, so his position at each moment is observed fact, not an
assumption about us. An action is REDUCE_RESIDUAL when it moves HIS residual
toward zero. What is hypothetical here is only the resting order we never
placed; the state it would have been repairing is real.

THE ONE CONVENTION THAT MAKES THIS TRACTABLE
--------------------------------------------
Prices are carried in OWN-SIDE terms -- what the acquirer pays for the
outcome being bought. For an UP action that is the wire price; for a DOWN
action it is 1 - wire. Under that convention a resting buy interacts with
the tape whenever

    own_side_market_price <= offer_own_price

for both sides, because a DOWN buy at q is a sell of YES at 1-q and the
inequality flips twice. AT when equal, THROUGH when strictly below.

STRICTNESS
----------
TRADE_AT_PRICE, TRADE_THROUGH_PRICE, MARKETABLE and NO_INTERACTION are four
distinct observations and none of them is a fill. A trade at our price says
someone traded there, not that we would have been at the front of that
queue. Nothing in this module returns a fill, a P&L, or a realized number.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

UP, DOWN = "UP", "DOWN"

#: the four observations, in strictly increasing order of how much they
#: would tell you -- and none of them is a fill
NO_INTERACTION = "NO_INTERACTION"
MARKETABLE = "MARKETABLE"
TRADE_AT_PRICE = "TRADE_AT_PRICE"
TRADE_THROUGH_PRICE = "TRADE_THROUGH_PRICE"


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def own_price(side: str, wire: D) -> D:
    """What the acquirer of `side` pays, from a YES-convention wire price."""
    return wire if side == UP else D(1) - wire


def classify(residual: D, side: str) -> str:
    if residual == 0:
        return "FLAT_OPEN"
    up = side == UP
    return "INCREASE_RESIDUAL" if ((residual > 0) == up) else "REDUCE_RESIDUAL"


class L5Inventory:
    """L5's own realized position through a window. Every row is a fill."""

    def __init__(self) -> None:
        self.qty = {UP: D(0), DOWN: D(0)}
        self.cost = {UP: D(0), DOWN: D(0)}

    def residual(self) -> D:
        return self.qty[UP] - self.qty[DOWN]

    def avg(self, side: str) -> Optional[D]:
        q = self.qty[side]
        return (self.cost[side] / q) if q > 0 else None

    def acquire(self, side: str, qty: D, own_px: D) -> None:
        self.qty[side] += qty
        self.cost[side] += qty * own_px


class RestingOffer:
    """A hypothetical resting order, and what the tape did around it."""

    def __init__(self, *, key: str, market: str, side: str, own_px: D,
                 qty: D, t: float, window_end: float, klass: str,
                 residual_before: D, opposite_avg: Optional[D],
                 spread: Optional[D], tte_s: Optional[float]) -> None:
        self.key, self.market, self.side = key, market, side
        self.own_px, self.qty, self.t = own_px, qty, float(t)
        self.window_end = float(window_end)
        self.klass, self.residual_before = klass, residual_before
        self.opposite_avg, self.spread, self.tte_s = opposite_avg, spread, tte_s
        self.first_at: Optional[float] = None
        self.first_through: Optional[float] = None
        self.first_marketable: Optional[float] = None
        self.max_adverse = D(0)
        self.max_favorable = D(0)
        self.last_own_px: Optional[D] = None
        self.n_at = 0
        self.n_through = 0
        self.prints = 0

    # ---------------------------------------------------------- observe

    def observe_trade(self, t: float, wire: D) -> None:
        """One authenticated print. Never a fill, whatever it touches."""
        if t < self.t or t > self.window_end:
            return
        self.prints += 1
        m = own_price(self.side, wire)
        self.last_own_px = m
        move = m - self.own_px
        if move > self.max_adverse:
            self.max_adverse = move
        if -move > self.max_favorable:
            self.max_favorable = -move
        if m == self.own_px:
            self.n_at += 1
            if self.first_at is None:
                self.first_at = t
        elif m < self.own_px:
            self.n_through += 1
            if self.first_through is None:
                self.first_through = t

    def observe_touch(self, t: float, own_side_ask: Optional[D]) -> None:
        """Marketable when the opposite touch reaches our price."""
        if own_side_ask is None or t < self.t or t > self.window_end:
            return
        if own_side_ask <= self.own_px and self.first_marketable is None:
            self.first_marketable = t

    # ----------------------------------------------------------- verdict

    def interaction(self) -> str:
        if self.first_through is not None:
            return TRADE_THROUGH_PRICE
        if self.first_at is not None:
            return TRADE_AT_PRICE
        if self.first_marketable is not None:
            return MARKETABLE
        return NO_INTERACTION

    def first_interaction_t(self) -> Optional[float]:
        ts = [x for x in (self.first_through, self.first_at) if x is not None]
        return min(ts) if ts else None

    def latency_s(self) -> Optional[float]:
        t = self.first_interaction_t()
        return None if t is None else t - self.t

    def pair_cost_at_offer(self) -> Optional[D]:
        return (None if self.opposite_avg is None
                else self.own_px + self.opposite_avg)

    def pair_cost_at(self, own_px: Optional[D]) -> Optional[D]:
        if own_px is None or self.opposite_avg is None:
            return None
        return own_px + self.opposite_avg

    def deteriorated_own_px(self) -> Optional[D]:
        """The own-side price the market ran to while we rested.

        For an offer that never interacts this is what a repair would have
        cost had it been re-offered where the market actually was. It is NOT
        a chase rule and nothing here proposes one -- it is the size of the
        move, priced.
        """
        if self.max_adverse <= 0:
            return None
        return self.own_px + self.max_adverse

    def as_dict(self) -> Dict[str, Any]:
        det = self.deteriorated_own_px()
        return {
            "key": self.key, "market": self.market, "side": self.side,
            "class": self.klass, "offer_own_px": str(self.own_px),
            "qty": str(self.qty), "residual_before": str(self.residual_before),
            "abs_residual_before": str(abs(self.residual_before)),
            "spread": None if self.spread is None else str(self.spread),
            "tte_s": self.tte_s,
            "interaction": self.interaction(),
            "latency_s": self.latency_s(),
            "n_trade_at": self.n_at, "n_trade_through": self.n_through,
            "prints_observed": self.prints,
            "max_adverse": str(self.max_adverse),
            "max_favorable": str(self.max_favorable),
            "deteriorated_own_px": None if det is None else str(det),
            "deterioration": (None if det is None
                              else str(det - self.own_px)),
            "pair_cost_at_offer": (None if self.pair_cost_at_offer() is None
                                   else str(self.pair_cost_at_offer())),
            "pair_cost_deteriorated": (
                None if self.pair_cost_at(det) is None
                else str(self.pair_cost_at(det))),
            "expired_without_interaction":
                self.interaction() in (NO_INTERACTION, MARKETABLE),
            "labels": ("an observed trade is not a fill; MARKETABLE is not a "
                       "fill; queue position is unknown and unmodelled"),
        }


def quantiles(vals: Sequence[D], qs=(D("0.5"), D("0.75"), D("0.9"))
              ) -> Dict[str, Optional[str]]:
    """Plain order statistics. No interpolation, no fitting."""
    xs = sorted(vals)
    out: Dict[str, Optional[str]] = {}
    for q in qs:
        label = "p%d" % int(q * 100)
        if not xs:
            out[label] = None
            continue
        i = int((len(xs) - 1) * q)
        out[label] = str(xs[i])
    out["n"] = str(len(xs))
    return out
