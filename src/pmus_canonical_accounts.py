"""ONE answer to "what do we own, what did it cost, and does the venue agree?".

WHY THIS EXISTS. The R5 window (2026-09-29 1115z) printed, in a single
report, `inventory.dn = 20.0000` and `drift.ours = "0"` for the same market
and the same side, alongside `filled: 0` while eight orders had filled and
$38.30 had left the account. Three accessors, three answers:

  * `filled` / `fills_from_venue` / `fees_from_venue` / `filled_at_venue`
    / `inventory.*` all read the L5-ACTION lane's store, which the native
    money path never writes to. The money moved through the native lane, so
    these reported zero while 80 contracts traded.
  * `drift` compared our GROSS DOWN accumulator against the venue's NET
    position. The venue nets the two outcome tokens: we held UP 30 and
    DOWN 50, the venue said DOWN 20, and a per-key compare called that a
    disagreement when it is the identity net = long - short holding exactly.
  * `inventory.dn` read 20 only because the reconciler had already adopted
    that venue net into our book as a cost-basis-less position.

None of those three was a second BOOK; they were three readings of
different moments and different representations, presented as if they were
one. This module is the single feed. Everything downstream reads it, so
there is one place that can be right or wrong and no way for two numbers
to disagree.

THE AUTHORITATIVE INPUT is the list of booked fills the native money path
accumulates in `booked_fills`: one row per authoritative
`GET /v1/order/{id}` that reported new filled quantity, carrying side,
quantity, own-terms price, cost and commission. A fill that is not in that
list was never confirmed by the venue, and nothing here infers one.

REPRESENTATION. We keep GROSS (both legs) because the matched pair is the
half of the position whose economics are locked, and collapsing to net
deletes it. The venue reports NET. The two are reconciled by the identity

    our_net = gross_long_down - gross_long_up  ==  venue_net_down

and NEVER by comparing the venue's net against one gross-side accumulator.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

#: the two legs, in our own gross representation
UP = "UP"
DOWN = "DOWN"

#: how the money path spells the sides on a booked-fill row
_SIDE = {"up": UP, "down": DOWN, "UP": UP, "DOWN": DOWN}


def _d(v: Any) -> D:
    if v is None:
        return D(0)
    return D(str(v))


class CanonicalAccounts:
    """Gross book, matched/residual decomposition, cost basis and fees.

    Derived ONLY from authoritative booked fills. There is no setter: to
    change what this says, book a fill.
    """

    __slots__ = ("market", "gross", "cost", "fees", "fill_rows", "orders")

    def __init__(self, market: str) -> None:
        self.market = market
        self.gross: Dict[str, D] = {UP: D(0), DOWN: D(0)}
        self.cost: Dict[str, D] = {UP: D(0), DOWN: D(0)}
        self.fees = D(0)
        self.fill_rows = 0
        self.orders: Dict[str, D] = {}

    # ---- construction ---------------------------------------------------
    @classmethod
    def from_booked_fills(cls, fills: Iterable[Mapping[str, Any]], *,
                          market: str) -> "CanonicalAccounts":
        self = cls(market)
        for f in fills:
            side = _SIDE.get(str(f.get("side")))
            if side is None:
                raise ValueError("unrecognised fill side %r" % (f.get("side"),))
            qty = _d(f.get("qty"))
            if qty <= 0:
                continue
            self.gross[side] += qty
            self.cost[side] += _d(f.get("cost"))
            # the venue's signed commission field. A credit arrives negative
            # and is NOT renamed a rebate here: it is reported as the venue
            # reported it, and whether it was credited is a settlement
            # question this module does not answer.
            self.fees += _d(f.get("commission"))
            self.fill_rows += 1
            oid = str(f.get("oid") or "")
            if oid:
                self.orders[oid] = self.orders.get(oid, D(0)) + qty
        return self

    # ---- the derived quantities, each with exactly one definition -------
    @property
    def filled_contracts(self) -> D:
        return self.gross[UP] + self.gross[DOWN]

    @property
    def filled_orders(self) -> int:
        """Distinct venue orders that contributed confirmed quantity."""
        return len(self.orders)

    @property
    def matched(self) -> D:
        return min(self.gross[UP], self.gross[DOWN])

    @property
    def residual(self) -> D:
        """Signed in DOWN terms, matching the venue's own sign convention.

        Positive means we are net long DOWN, negative net long UP.
        """
        return self.gross[DOWN] - self.gross[UP]

    @property
    def residual_side(self) -> Optional[str]:
        r = self.residual
        return None if r == 0 else (DOWN if r > 0 else UP)

    @property
    def filled_cash(self) -> D:
        """Cash paid for quantity that actually filled. Fees excluded."""
        return self.cost[UP] + self.cost[DOWN]

    def avg_price(self, side: str) -> Optional[D]:
        q = self.gross[side]
        return None if q == 0 else self.cost[side] / q

    @property
    def our_net_down(self) -> D:
        """The identity the venue's net must equal. Never a gross leg."""
        return self.residual

    def as_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "source": "AUTHORITATIVE_BOOKED_FILLS",
            "filled_orders": self.filled_orders,
            "filled_contracts": str(self.filled_contracts),
            "gross": {UP: str(self.gross[UP]), DOWN: str(self.gross[DOWN])},
            "matched": str(self.matched),
            "residual": str(self.residual),
            "residual_side": self.residual_side,
            "cost_basis": {UP: str(self.cost[UP]), DOWN: str(self.cost[DOWN])},
            "filled_cash": str(self.filled_cash),
            "avg_price": {UP: (None if self.avg_price(UP) is None
                               else str(self.avg_price(UP))),
                          DOWN: (None if self.avg_price(DOWN) is None
                                 else str(self.avg_price(DOWN)))},
            "fees_venue_signed": str(self.fees),
            "our_net_down": str(self.our_net_down),
            "labels": ("gross keeps BOTH legs because the matched pair is a "
                       "locked economic result; the venue's net is compared "
                       "against our_net_down (= gross DOWN - gross UP), "
                       "never against a single gross leg. fees are the "
                       "venue's signed commission field, not a credited "
                       "rebate."),
        }


def venue_net_down(positions: Sequence[Mapping[str, Any]], *,
                   market: str) -> Optional[D]:
    """The venue's net for one market, expressed in DOWN terms.

    `None` means the venue reported nothing for this market, which is the
    ordinary post-settlement state and is NOT divergence.
    """
    seen = False
    net = D(0)
    for p in positions:
        if str(p.get("market")) != market:
            continue
        seen = True
        side = _SIDE.get(str(p.get("side")))
        qty = _d(p.get("qty"))
        if side == DOWN:
            net += qty
        elif side == UP:
            net -= qty
        else:
            raise ValueError("unrecognised venue position side %r"
                             % (p.get("side"),))
    return net if seen else None


def reconcile(accounts: CanonicalAccounts, *,
              venue_net: Optional[D],
              open_orders_at_venue: int,
              submitted_orders: int,
              terminal_filled_orders: int,
              rejected_orders: int,
              no_fill_terminal_orders: int) -> Dict[str, Any]:
    """Coherent ONLY when every quantity agrees. Genuine disagreement fails.

    The checks are named individually so a failure says which identity broke
    rather than reporting a single opaque boolean.
    """
    checks: Dict[str, Any] = {}

    # 1. the representation identity, the one R5 got wrong
    if venue_net is None:
        checks["venue_net_matches_our_net"] = {
            "ok": True, "venue_net": None,
            "our_net_down": str(accounts.our_net_down),
            "note": ("the venue reports nothing for this market, which is "
                     "the ordinary post-settlement state, not divergence"),
        }
    else:
        checks["venue_net_matches_our_net"] = {
            "ok": venue_net == accounts.our_net_down,
            "venue_net": str(venue_net),
            "our_net_down": str(accounts.our_net_down),
            "identity": "gross DOWN - gross UP == venue net (DOWN terms)",
        }

    # 2. gross decomposes into matched + residual without loss
    lhs = accounts.matched * 2 + abs(accounts.residual)
    checks["gross_decomposes"] = {
        "ok": lhs == accounts.filled_contracts,
        "matched_x2_plus_abs_residual": str(lhs),
        "filled_contracts": str(accounts.filled_contracts),
    }

    # 3. every order that filled is counted once, and the order census closes
    checks["filled_orders_agree"] = {
        "ok": accounts.filled_orders == terminal_filled_orders,
        "from_fills": accounts.filled_orders,
        "terminal_filled": terminal_filled_orders,
    }
    census = (terminal_filled_orders + rejected_orders
              + no_fill_terminal_orders + open_orders_at_venue)
    checks["order_census_closes"] = {
        "ok": census == submitted_orders,
        "filled_plus_rejected_plus_nofill_plus_open": census,
        "submitted": submitted_orders,
    }

    # 4. THE R4/R5 INVARIANT, kept: money moved but nothing was submitted is
    #    a contradiction, and so is the reverse.
    moved = accounts.filled_contracts > 0
    checks["submission_explains_position"] = {
        "ok": (submitted_orders > 0) if moved else True,
        "filled_contracts": str(accounts.filled_contracts),
        "submitted": submitted_orders,
    }

    # 5. a cost basis exists for every share we claim to hold
    missing = [s for s in (UP, DOWN)
               if accounts.gross[s] > 0 and accounts.cost[s] <= 0]
    checks["cost_basis_present"] = {"ok": not missing,
                                    "sides_without_cost": missing}

    failed = sorted(k for k, v in checks.items() if not v["ok"])
    return {
        "coherent": not failed,
        "failed_checks": failed,
        "checks": checks,
        "accounts": accounts.as_dict(),
        "authority": "AUTHORITATIVE_BOOKED_FILLS + VENUE_NET",
        "labels": ("coherent is true only when every named identity holds. "
                   "A genuine disagreement fails closed here rather than "
                   "being averaged or overwritten."),
    }
