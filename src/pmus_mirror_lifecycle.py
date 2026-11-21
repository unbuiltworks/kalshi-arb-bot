"""MIRROR_RAW resting-order lifecycle against the AUTHENTICATED PMUS tape.

The primary experiment. For every realtime L5 action we translated into a PMUS
q=1 intent, this records the hypothetical resting order and follows the
authenticated trade tape forward until the order's life ends, then classifies
what happened with the ALREADY-FROZEN five evidence classes.

NO BACKDATING, NO FUTURE INFORMATION. An order becomes actionable at our
RECEIPT time, not at L5's event time, and only trades strictly AFTER that
instant can bear on it. A trade that happened while the news was still in
flight tells us nothing about an order we had not yet placed.

AN ORDER'S LIFE ENDS at the first of:
  * L5's next action changes the translated intent (side or price);
  * the window ends;
  * interaction evidence occurs.
Nothing extends past the window, and nothing is carried between windows.

A CLASSIFICATION IS STILL NOT A FILL. TRADE_AT_PRICE and TRADE_THROUGH_PRICE
are not converted into guaranteed fills anywhere here, because queue position
is unknown. They feed three bounds that are computed separately and NEVER
mixed:

  STRICT       TRADE_THROUGH_PRICE only. A print that traded THROUGH our price
               on the aggressor side consumed everything resting at that price
               on that side, so a resting order there was reached whatever its
               queue position. This is the only class that is queue-INDEPENDENT
               and it is therefore the only one in the strict bound.
  PLAUSIBLE    TRADE_AT_PRICE and OBSERVABLE_FILL_PLAUSIBLE -- real
               interaction at our price, but queue-DEPENDENT: the print may
               have filled only the orders ahead of us.
  COPYABILITY  every action that was maker-copyable at receipt, with no
               interaction requirement at all. The existing upper bound.

Each bound gets its own inventory and its own economics. A number that mixed
them would describe no world.

Everything is SHADOW. No order existed on either venue.
"""
from __future__ import annotations

import collections
import datetime as dt
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_fees as fees
from src import pmus_interaction_classifier as clf

STRICT = "STRICT"
PLAUSIBLE = "PLAUSIBLE"
COPYABILITY = "COPYABILITY"
BOUNDS = (STRICT, PLAUSIBLE, COPYABILITY)

#: which evidence classes admit an acquisition under each bound
BOUND_CLASSES: Dict[str, tuple] = {
    STRICT: (clf.TRADE_THROUGH_PRICE,),
    PLAUSIBLE: (clf.TRADE_AT_PRICE, clf.TRADE_THROUGH_PRICE,
                clf.FILL_PLAUSIBLE),
    COPYABILITY: (),                      # no interaction requirement
}

WINDOW_S = 900


def _epoch(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except (TypeError, ValueError):
        return None


class BoundBook:
    """One bound's inventory. Never shared, never summed with another."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.up = 0
        self.dn = 0
        self.up_cost = D("0")
        self.dn_cost = D("0")
        self.rebate = D("0")
        self.first_side: Optional[str] = None
        self.opposite_acquired = False
        self.acquisitions: List[dict] = []

    def acquire(self, side_up: bool, px: D, when: float, klass: str) -> None:
        if self.first_side is None:
            self.first_side = "UP" if side_up else "DOWN"
        elif (self.first_side == "UP") != side_up:
            self.opposite_acquired = True
        if side_up:
            self.up += 1
            self.up_cost += px
        else:
            self.dn += 1
            self.dn_cost += px
        self.rebate += fees.maker_rebate(px)
        self.acquisitions.append(
            {"t": when, "side": "UP" if side_up else "DOWN",
             "price": str(px), "evidence": klass})

    @property
    def matched(self) -> int:
        return min(self.up, self.dn)

    @property
    def residual(self) -> int:
        return self.up - self.dn

    def economics(self) -> Dict[str, Any]:
        m = self.matched
        avg_up = (self.up_cost / self.up) if self.up else D(0)
        avg_dn = (self.dn_cost / self.dn) if self.dn else D(0)
        pair_cost = (avg_up + avg_dn) if m else None
        matched_cost = (pair_cost * m) if m else D(0)
        # A matched pair pays exactly $1.00 at settlement whichever way it
        # resolves. That is the ONLY settlement figure that is knowable
        # without the outcome, which is why the residual has none.
        settle = D(m) - matched_cost
        r = self.residual
        resid_cost = (avg_up * abs(r)) if r > 0 else (avg_dn * abs(r)) if r < 0 else D(0)
        return {
            "bound": self.name,
            "first_side_acquisition": self.first_side,
            "opposite_side_acquisition": self.opposite_acquired,
            "acquisitions": len(self.acquisitions),
            "up": self.up, "dn": self.dn,
            "matched_pairs": m,
            "avg_pair_cost": (str(pair_cost.quantize(D("0.0001")))
                              if pair_cost is not None else None),
            "residual_units": abs(r),
            "residual_cost_basis_usd": str(resid_cost.quantize(D("0.0001"))),
            "residual_settlement_pnl": None,
            "residual_note": ("UNMARKED. Its value is the window's outcome, "
                              "which is not knowable from the tape, and L5's "
                              "own residual line is negative."),
            "settlement_implied_pnl_matched_usd": str(
                settle.quantize(D("0.0001"))),
            "maker_rebate_accrual_usd": str(self.rebate.quantize(D("0.0001"))),
            "taker_fees_usd": "0.0000",
            "taker_fees_note": "every intent is a maker join; nothing crosses",
            "liquidity_rewards_usd": "0.00",
            "net_shadow_economics_usd": str(
                (settle + self.rebate).quantize(D("0.0001"))),
            "net_note": ("matched settlement plus the rebate ACCRUAL. The "
                         "residual is excluded because it is unmarked, so "
                         "this is not a complete P&L."),
            "label": "SHADOW. No order existed; no fill is claimed.",
        }


def order_life(order: Mapping[str, Any],
               later_actions: Sequence[Mapping[str, Any]],
               window_end: float) -> float:
    """When the hypothetical resting order stops being ours to hold.

    The first of: L5's next action changing the translated intent, or the
    window ending. Interaction evidence ends it too, and that is handled by
    the scan itself, which stops at the first interacting print.
    """
    t0 = float(order["actionable_from"])
    side = order["side"]
    px = order["price"]
    for a in later_actions:
        t = float(a["actionable_from"])
        if t <= t0:
            continue
        if a["side"] != side or a["price"] != px:
            return min(t, window_end)
    return window_end


def scan(orders: Sequence[Mapping[str, Any]],
         prints_by_market: Mapping[str, Sequence[Mapping[str, Any]]],
         *, normalize) -> List[dict]:
    """Classify each order against the authenticated tape over its own life.

    `normalize` converts a raw trade row into the classifier's vocabulary and
    is injected so the aggressor mapping stays in one place and can be left
    UNPROVEN without touching this module.
    """
    by_market = collections.defaultdict(list)
    for o in orders:
        by_market[o["market"]].append(dict(o))
    out: List[dict] = []
    for market, os_ in by_market.items():
        os_.sort(key=lambda o: float(o["actionable_from"]))
        raw = list(prints_by_market.get(market) or [])
        norm = []
        for p in raw:
            n = normalize(p)
            e = _epoch(p.get("t"))
            if n is None or e is None:
                continue
            n["_epoch"] = e
            norm.append(n)
        norm.sort(key=lambda r: r["_epoch"])
        for i, o in enumerate(os_):
            t0 = float(o["actionable_from"])
            end = order_life(o, os_[i + 1:], float(o["window_end"]))
            # STRICTLY AFTER t0: a trade that happened while the news was in
            # flight cannot bear on an order we had not placed.
            window = [p for p in norm if t0 < p["_epoch"] <= end]
            res = clf.classify(
                {"desired_price": o["price"], "side": o["side"]},
                window, bool(window),
                queue_ahead=(D(str(o["queue_ahead"]))
                             if o.get("queue_ahead") is not None else None))
            res.update({
                "market": market,
                "actionable_from": t0,
                "life_ends": end,
                "life_s": round(end - t0, 1),
                "life_ended_by": ("window_end" if end >= float(o["window_end"])
                                  else "intent_changed"),
                "prints_considered": len(window),
                "copyable_at_receipt": o.get("copyable"),
                # IDENTITY PASS-THROUGH, no classification semantics.
                # (market, side, price, actionable_from) is NOT unique: the
                # observer snapshots PMUS once per poll, so a burst of L5
                # actions shares one receipt timestamp. Joining on that tuple
                # silently collapsed 218 of 676 orders onto 458 keys and
                # mis-attributed roughly a third of them. Carrying the row's
                # own key removes the join entirely.
                "action_key": o.get("action_key"),
                "price": o["price"], "side": o["side"],
                "l5_relation": o.get("l5_relation"),
                "is_fill": False,
                "fill_evidence_source": None,
            })
            out.append(res)
    out.sort(key=lambda r: r["actionable_from"])
    return out


def bounds(classified: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The three bounds, each on its own inventory. Never mixed."""
    books = {b: BoundBook(b) for b in BOUNDS}
    for c in classified:
        px = D(str(c["price"]))
        side_up = c["side"] == "UP"
        for b in BOUNDS:
            if b == COPYABILITY:
                if c.get("copyable_at_receipt"):
                    books[b].acquire(side_up, px, c["actionable_from"],
                                     "COPYABLE_AT_RECEIPT")
            elif c.get("klass") in BOUND_CLASSES[b]:
                books[b].acquire(side_up, px, c["actionable_from"],
                                 c["klass"])
    return {
        "label": "PMUS_MIRROR_RAW_BOUNDS_V1",
        "classes": collections.Counter(c.get("klass") for c in classified),
        "orders": len(classified),
        "bound_definitions": {
            STRICT: "TRADE_THROUGH_PRICE only -- queue-INDEPENDENT: a print "
                    "through our price consumed everything resting at it",
            PLAUSIBLE: "TRADE_AT_PRICE / TRADE_THROUGH_PRICE / "
                       "OBSERVABLE_FILL_PLAUSIBLE -- real interaction, but "
                       "queue-DEPENDENT",
            COPYABILITY: "every action maker-copyable at receipt, no "
                         "interaction requirement -- the upper bound",
        },
        "bounds": {b: books[b].economics() for b in BOUNDS},
        "never_mixed": "each bound has its own inventory; no figure crosses "
                       "between them",
        "any_actual_fill": False,
    }
