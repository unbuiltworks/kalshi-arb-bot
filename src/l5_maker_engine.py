"""An inventory-aware RESTING LIMIT ORDER engine, with partial fills.

OWNER: "The primary hypothesis is that L5 uses an automated, inventory-aware
limit-order system with frequent partial fills. Your job is to reconstruct and
implement that behavior using our existing infrastructure."

THE EVIDENCE THIS IS BUILT ON (analysis/l5/fee_reconciliation.json,
analysis/l5/role_and_parent_orders.json), all of it measured, none assumed:

  * 77.2% of his 760,817 fills paid NO taker fee -- read from the venue's cash
    (`usdcSize == size * price`), not inferred from where a fill sat. He RESTS.
  * resting saved him $122,984.93, or $0.007827/contract, against a BTC15m net
    edge of $0.001437/contract. The maker share is 5.4x his whole edge, so an
    engine that crosses is not a worse version of him -- it is a losing one.
  * 49.7% of his fills land in multi-fill same-price runs (p90 3 fills, max
    38; p90 duration 7s), and 76.4% of those runs are entirely maker. That is
    what one resting parent order filled in pieces looks like.
  * his side choice is NOT inventory-steered: P(a fill lands on the deficient
    side) is 0.5008 / 0.4942 / 0.4932 across imbalance terciles WITHIN cells of
    (time remaining x price x recent drift), a paired difference of -0.0076,
    t = -0.27. So this engine rests on BOTH sides rather than picking one.
  * but he IS steering, through SIZE. Within those same cells his
    deficient-side fills are 1.1413x larger than his abundant-side fills
    (median 1.0567, t = 3.87, 37 of 54 cells above 1), and a matched passive
    null -- same fill count, same sizes in the same order, side flipped
    independently at his own realised rate -- puts his |residual|/matched at
    the 23rd percentile of chance (0.136 observed against a 0.335 null
    median). His book is tighter than chance, and SIZE_SKEW below is how.
  * he keeps trading after balancing (99.3-99.7% of balanced markets), so a
    completed pair does not retire the engine.

WHAT IS STILL NOT OBSERVABLE, AND IS NOT INVENTED HERE
-------------------------------------------------------
His order submissions, cancellations, replaces, limit prices and queue
position are not in any feed we hold. This engine therefore does not claim to
reproduce his orders. It implements the BEHAVIOUR the fills evidence supports
-- rest on both sides, fee-safe, reprice as the market moves, accept partial
fills -- and its own orders are its own.

THE FILL RULE IS THE EXISTING CONVENTION, AND IT IS CONSERVATIVE
-----------------------------------------------------------------
`src/pmus_l5_handoff_shadow.py` already fills a resting buy at price p from
the first later print that crosses it. This engine uses the same rule with two
deliberate tightenings, both parameters so their cost is measurable:

  * `strict_through`: whether the print must be STRICTLY through our price.
    This DEFAULTS TO FALSE, and the default is the economics, not a relaxation.
    Every row in this feed is a BUY, so each print marks a SELLER who accepted
    that price. Price priority says a seller willing to accept `p` would have
    hit our resting bid at `p' >= p` first. Requiring `p < p'` instead means
    we only ever fill on a side that is FALLING THROUGH us, which selects the
    losing side of every market -- measured, not argued: with strict=True this
    engine ends 1.91 matched against $5 of cash, because it accumulates
    whichever side is cheapening. Equality is handled by
    `queue_participation`, which is where queue uncertainty belongs.
  * `queue_participation`: we take at most this share of the printed size,
    because we do not know how much of that print was ours to take.

And an order placed in slice b can only fill in a LATER slice. A fill in its
own slice would be the within-slice look-ahead that reversed an earlier result
in this program.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from src.l5_behavior_state import UP, DOWN, L5State
except ModuleNotFoundError:
    from l5_behavior_state import UP, DOWN, L5State


def fee_safe(price_a: float, price_b: float, fee_a: float, fee_b: float
             ) -> bool:
    """Would a pair bought at these two leg prices clear its own fees?

    NOT a tuned threshold: the breakeven identity. A matched binary pair
    redeems exactly $1.00, so a pair acquired for `cost` nets
    `1.00 - cost - fees`, and buying at or above $1.00 loses by arithmetic.
    The fee term is computed per leg at the leg's own price, never averaged,
    because theta*C*p*(1-p) varies about fourfold across the range L5 trades.

    It lives HERE rather than in l5_continuous_policy because that module
    imports numpy and the deployed collector image has no numpy -- see the
    note there.
    """
    return (price_a + price_b + fee_a + fee_b) < 1.0

#: tick size on both venues
TICK = 0.01


class RestingOrder:
    """One of our limit buys, with the remaining size a partial fill leaves."""

    __slots__ = ("side", "price", "size", "filled", "placed_bin", "oid")

    def __init__(self, side: str, price: float, size: float, placed_bin: int,
                 oid: int) -> None:
        self.side = side
        self.price = round(price, 4)
        self.size = size
        self.filled = 0.0
        self.placed_bin = placed_bin
        self.oid = oid

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)

    def __repr__(self) -> str:                        # pragma: no cover
        return (f"RestingOrder({self.side} {self.remaining:.2f}"
                f"@{self.price} #{self.oid})")


class MakerEngine:
    """Keeps a two-sided resting quote, repriced on evidence, fee-safe."""

    def __init__(self, *, max_usd_per_market: float = 5.0,
                 max_abs_residual: float = 10.0,
                 clip: float = 5.0,
                 size_skew: float = 1.0,
                 reprice_ticks: float = 1.0,
                 queue_participation: float = 0.5,
                 strict_through: bool = False,
                 min_contracts: float = 0.01) -> None:
        self.max_usd = max_usd_per_market
        self.max_resid = max_abs_residual
        self.clip = clip
        #: quote this much MORE on the deficient side than the abundant one.
        #: 1.0 is the unsteered engine; 1.1413 is L5's measured ratio
        #: (analysis/l5/inventory_control_test.json). It is a measurement,
        #: not a tuned parameter, and the replay scores both.
        self.size_skew = size_skew
        self.reprice_ticks = reprice_ticks
        self.queue_participation = queue_participation
        self.strict_through = strict_through
        self.min_contracts = min_contracts
        self.orders: List[RestingOrder] = []
        self._next_oid = 0
        self.cancels = 0
        self.places = 0
        self.partials = 0
        self.fills = 0
        #: see `match`: eligibility, queue-possible and booked are different
        #: quantities and are never collapsed into one "fill" number.
        self.price_eligible_qty = 0.0
        self.queue_possible_qty = 0.0
        self.booked_qty = 0.0
        #: why a price-eligible print did NOT become a booked fill
        self.missed: Dict[str, float] = {"no_resting_order": 0.0,
                                         "cash_cap": 0.0,
                                         "residual_cap": 0.0,
                                         "not_fee_safe": 0.0}

    # ---------------------------------------------------------- order admin
    def _place(self, side: str, price: float, size: float, bin_idx: int
               ) -> RestingOrder:
        self._next_oid += 1
        o = RestingOrder(side, price, size, bin_idx, self._next_oid)
        self.orders.append(o)
        self.places += 1
        return o

    def cancel_all(self) -> int:
        n = len(self.orders)
        self.cancels += n
        self.orders = []
        return n

    def resting(self, side: str) -> Optional[RestingOrder]:
        for o in self.orders:
            if o.side == side and o.remaining > self.min_contracts:
                return o
        return None

    # -------------------------------------------------------------- quoting
    def target_price(self, side: str, ref: float) -> float:
        """Where we want to rest on `side`, given a reference UP price.

        The venue quotes one price; DOWN costs its complement. We rest one
        tick INSIDE the reference on each side, which is the cheapest place a
        resting order can be and still be a quote rather than a cross.
        """
        p = ref if side == UP else (1.0 - ref)
        return max(TICK, min(1.0 - TICK,
                             round(p - self.reprice_ticks * TICK, 4)))

    def manage(self, st: L5State, bin_idx: int, cash_spent: float,
               ref_price: float, fee_of: Any) -> Dict[str, Any]:
        """Reprice/replace our two-sided quote for this slice.

        `ref_price` is the reference UP price from evidence available STRICTLY
        BEFORE this slice. `fee_of(price, qty)` returns our fee for a leg.
        Returns a record of what the engine did, for the chronological compare.
        """
        acted = {"placed": [], "cancelled": 0, "declined": []}
        room_usd = self.max_usd - cash_spent
        if room_usd <= 1e-9:
            acted["cancelled"] += self.cancel_all()
            acted["declined"].append("CASH_CAP")
            return acted

        for side in (UP, DOWN):
            want_px = self.target_price(side, ref_price)
            other = DOWN if side == UP else UP
            # the pair this leg would complete: against our existing basis on
            # the other side when we hold one, else against the complement of
            # our own quote. Either way it must clear $1.00 after OUR fees.
            base = st.basis(other)
            complement = base if base is not None else (1.0 - want_px)
            probe = max(self.min_contracts, min(self.clip, room_usd / max(want_px, TICK)))
            if not fee_safe(want_px, complement,
                            fee_of(want_px, probe) / probe,
                            fee_of(complement, probe) / probe):
                cur = self.resting(side)
                if cur is not None:
                    self.orders.remove(cur)
                    self.cancels += 1
                    acted["cancelled"] += 1
                acted["declined"].append(f"NOT_FEE_SAFE_{side}")
                continue

            # residual headroom: never rest size that could breach the cap
            head = (self.max_resid - st.residual) if side == UP \
                else (self.max_resid + st.residual)
            deficient = st.deficient_side()
            if deficient is None or self.size_skew == 1.0:
                clip = self.clip
            elif side == deficient:
                clip = self.clip * self.size_skew
            else:
                clip = self.clip / self.size_skew
            size = min(clip, max(head, 0.0),
                       room_usd / max(want_px, TICK))
            if size < self.min_contracts:
                cur = self.resting(side)
                if cur is not None:
                    self.orders.remove(cur)
                    self.cancels += 1
                    acted["cancelled"] += 1
                acted["declined"].append(f"NO_ROOM_{side}")
                continue

            cur = self.resting(side)
            if cur is not None and abs(cur.price - want_px) < TICK / 2:
                continue                       # already resting where we want
            if cur is not None:
                self.orders.remove(cur)        # reprice = cancel + replace
                self.cancels += 1
                acted["cancelled"] += 1
            o = self._place(side, want_px, size, bin_idx)
            acted["placed"].append((side, o.price, round(size, 4)))
        return acted

    # ---------------------------------------------------------- fill engine
    def match(self, bin_idx: int, prints: Dict[str, Tuple[float, float]]
              ) -> List[Dict[str, Any]]:
        """Fill resting orders against this slice's prints. Partial fills kept.

        `prints[side] = (contracts, vwap)`. An order may only fill in a slice
        LATER than the one it was placed in.
        """
        out: List[Dict[str, Any]] = []
        for o in list(self.orders):
            if o.placed_bin >= bin_idx:
                continue                       # no same-slice fill, ever
            p = prints.get(o.side)
            if not p or p[0] <= 0:
                continue
            qty_printed, px = p
            through = (px < o.price) if self.strict_through else (px <= o.price)
            if not through:
                continue
            # THREE TIERS, reported separately, because price eligibility is
            # NOT a fill. A print at or below our bid establishes only that a
            # seller accepted that price; whether OUR order was the one served
            # depends on queue position, which no feed we hold observes.
            price_eligible = min(o.remaining, qty_printed)
            queue_possible = min(o.remaining,
                                 qty_printed * self.queue_participation)
            take = queue_possible
            self.price_eligible_qty += price_eligible
            self.queue_possible_qty += queue_possible
            if take < self.min_contracts:
                continue
            self.booked_qty += take
            o.filled += take
            self.fills += 1
            if o.remaining > self.min_contracts:
                self.partials += 1
            else:
                self.orders.remove(o)
            out.append({"side": o.side, "qty": take, "price": o.price,
                        "oid": o.oid, "bin": bin_idx,
                        "partial": o.remaining > self.min_contracts})
        return out
