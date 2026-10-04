"""CAPACITY: what a quantity ACTUALLY costs against the published ladder.

THE RULE THIS MODULE EXISTS TO ENFORCE: never multiply top-of-book by a
quantity the displayed depth cannot support. A measured example from
GEMI-BTC15M2610040030-UP at 00:20:18Z:

    asks: 0.82 x 5 | 0.84 x 350 | 0.88 x 45 | 0.95 x 2999

Top-of-book is 0.82, but only FIVE contracts are there. q=10 pays
(5 x 0.82 + 5 x 0.84) / 10 = 0.83, and q=100 pays 0.839. Pricing q=100 at
0.82 would invent $1.90 of edge that nobody is offering.

HOW A SIDE IS ACQUIRED, and the two sides are not symmetric reads:

  * UP  is bought by walking the ASKS upward from the touch.
  * DOWN is bought through the complement identity -- DOWN acquisition at
    an UP bid of p costs (1 - p) -- so it walks the BIDS downward from the
    touch, and the CHEAPEST DOWN is at the HIGHEST bid.

The venue publishes ONE contract ladder and no separate NO ladder, which
`metadata.contract_depth` states explicitly. So DOWN depth is the bid
depth, used through the identity the book already verifies, and nothing
here invents a NO size.

SHORTFALL IS REPORTED, NEVER FILLED. If the ladder cannot supply q, the
result says how much it could supply and at what price. A quantity the
book cannot fill is not an expensive trade -- it is an unavailable one.

NO STRATEGY PARAMETER LIVES HERE. Pair ceilings and price caps come from
the frozen policy; this module answers "what would it cost" and nothing
else.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SIZES = (D("1"), D("5"), D("10"), D("25"), D("50"), D("100"))


def _d(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def _levels(side: Mapping[str, Any]) -> List[Tuple[D, D]]:
    out: List[Tuple[D, D]] = []
    for lv in (side or {}).get("levels") or []:
        p, q = _d(lv.get("price")), _d(lv.get("qty"))
        if p is not None and q is not None and q > 0:
            out.append((p, q))
    return out


def walk(levels: Sequence[Tuple[D, D]], qty: D, *,
         cost_of: Any = None,
         max_paid: Optional[D] = None) -> Dict[str, Any]:
    """Consume `qty` across `levels` in the order given.

    `cost_of` maps a level price to the price actually PAID (identity for
    UP; 1 - p for DOWN). Levels must already be ordered cheapest-first in
    PAID terms.

    `max_paid` IS THE POLICY'S OWN PRICE CAP, AND IT BINDS PER LEVEL.
    A real order is a LIMIT: an order priced at L fills against levels at
    or below L and never above, so a VWAP that only LOOKS compliant --
    inside the cap on average while having consumed a level above it -- is
    a fill no limit order could have obtained. Levels above `max_paid` are
    therefore NOT CONSUMED, and if that leaves the request short the
    result is NOT fully executable. The remainder is never manufactured.

    WHETHER THIS CHANGES ANY RECORDED NUMBER IS AN EMPIRICAL QUESTION, and
    it was measured rather than assumed: across the recorded 00:20Z ladder
    and twelve live BTC15M/BTC05M symbols, at q1..q100, there were ZERO
    cases of a capped-compliant VWAP reached through an over-cap level --
    the near-touch levels carry hundreds of contracts, so q100 never
    reaches past the second level. The cap is enforced anyway, because
    "it did not happen in the sample" is not an invariant.
    """
    cost = cost_of or (lambda p: p)
    got = D("0")
    spend = D("0")
    used: List[Dict[str, str]] = []
    refused: List[Dict[str, str]] = []
    for price, avail in levels:
        if got >= qty:
            break
        paid = cost(price)
        if max_paid is not None and paid > max_paid:
            # The policy may not pay this, so no order of ours fills here.
            refused.append({"level_price": str(price), "paid": str(paid),
                            "qty_available": str(avail)})
            continue
        take = min(avail, qty - got)
        got += take
        spend += take * paid
        used.append({"level_price": str(price), "paid": str(paid),
                     "qty": str(take)})
    filled = got
    return {"requested": str(qty), "filled": str(filled),
            "max_paid": (None if max_paid is None else str(max_paid)),
            "levels_refused_above_cap": refused,
            "n_levels_refused_above_cap": len(refused),
            "shortfall": str(qty - filled),
            "fully_executable": bool(filled >= qty),
            "spend": str(spend),
            "vwap": (None if filled == 0 else str(spend / filled)),
            "levels_consumed": used,
            "n_levels_consumed": len(used),
            "touch_paid": (None if not levels else str(cost(levels[0][0]))),
            "slippage_vs_touch": (
                None if (filled == 0 or not levels) else
                str(spend / filled - cost(levels[0][0]))),
            "labels": ("SHORTFALL IS NOT A FILL. A quantity the ladder "
                       "cannot supply is unavailable, not merely dear."),
    }


def up_acquisition(depth: Mapping[str, Any], qty: D, *,
                   max_paid: Optional[D] = None) -> Dict[str, Any]:
    """Buy UP by walking the asks upward (cheapest ask first)."""
    lv = sorted(_levels(depth.get("asks") or {}), key=lambda t: t[0])
    return walk(lv, qty, max_paid=max_paid)


def down_acquisition(depth: Mapping[str, Any], qty: D, *,
                     max_paid: Optional[D] = None) -> Dict[str, Any]:
    """Buy DOWN through the complement identity: paying (1 - bid).

    The cheapest DOWN sits at the HIGHEST bid, so the bids are walked
    downward in price -- which is cheapest-first in PAID terms.
    """
    lv = sorted(_levels(depth.get("bids") or {}), key=lambda t: -t[0])
    return walk(lv, qty, cost_of=lambda p: D("1") - p, max_paid=max_paid)


def pair_at(depth: Mapping[str, Any], qty: D, *,
            pair_ceiling: D,
            max_paid: Optional[D] = None) -> Dict[str, Any]:
    """Both legs at `qty`, and whether a matched pair is executable.

    `pair_ceiling` is passed IN by the caller from the frozen policy; this
    module does not know or restate it.
    """
    up = up_acquisition(depth, qty, max_paid=max_paid)
    dn = down_acquisition(depth, qty, max_paid=max_paid)
    both = up["fully_executable"] and dn["fully_executable"]
    pair_cost = None
    if up["vwap"] is not None and dn["vwap"] is not None:
        pair_cost = D(up["vwap"]) + D(dn["vwap"])
    # The quantity BOTH legs could actually support, which is the binding
    # constraint on a matched-inventory strategy.
    both_q = min(D(up["filled"]), D(dn["filled"]))
    return {
        "qty": str(qty), "up": up, "down": dn,
        "both_legs_executable": both,
        "executable_pair_qty": str(both_q),
        "pair_cost": (None if pair_cost is None else str(pair_cost)),
        "pair_cost_within_ceiling": (
            None if pair_cost is None else bool(pair_cost <= pair_ceiling)),
        "locked_profit_per_pair": (
            None if pair_cost is None else str(D("1") - pair_cost)),
        "gross_locked_at_this_qty": (
            None if (pair_cost is None or not both) else
            str((D("1") - pair_cost) * qty)),
        "labels": ("pair_cost is the VWAP sum at this quantity, not the "
                   "sum of the touches. A pair is only economic if BOTH "
                   "legs are executable at the size."),
    }


def capacity_curve(depth: Mapping[str, Any], *, pair_ceiling: D,
                   max_paid: Optional[D] = None,
                   sizes: Sequence[D] = SIZES) -> Dict[str, Any]:
    """The whole curve, plus the maximum DEFENSIBLE pair quantity.

    "Defensible" means: both legs fully executable from DISPLAYED depth AND
    the resulting pair cost inside the ceiling. It is a floor on capacity,
    not an estimate of it -- hidden or replenishing liquidity would only
    help, and is not counted because it was not observed.
    """
    rows = [pair_at(depth, q, pair_ceiling=pair_ceiling, max_paid=max_paid)
            for q in sizes]
    ok = [r for r in rows if r["both_legs_executable"]
          and r["pair_cost_within_ceiling"]]
    best = ok[-1] if ok else None
    return {
        "rows": rows,
        "max_defensible_pair_qty": (None if best is None else best["qty"]),
        "max_defensible_gross": (None if best is None
                                 else best["gross_locked_at_this_qty"]),
        "pair_ceiling": str(pair_ceiling),
        "depth_shown": {
            "bids": (depth.get("bids") or {}).get("qty_shown"),
            "asks": (depth.get("asks") or {}).get("qty_shown"),
            "best_bid": (depth.get("bids") or {}).get("best"),
            "best_bid_qty": (depth.get("bids") or {}).get("best_qty"),
            "best_ask": (depth.get("asks") or {}).get("best"),
            "best_ask_qty": (depth.get("asks") or {}).get("best_qty"),
        },
        "labels": ("max_defensible_pair_qty is a FLOOR from displayed "
                   "depth. Nothing here extrapolates the touch to a size "
                   "the ladder does not show."),
    }
