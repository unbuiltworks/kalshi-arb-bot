"""From a logical relation to NET LOCKED DOLLARS at real depth.

THE LADDER THIS MODULE WALKS, and where it is allowed to stop:

    THEORETICAL              the relation exists in the settlement rules
    QUOTED                   touch prices alone imply a gap
    DEPTH_EXECUTABLE         the gap survives walking real L2 size, with fees
    SIMULTANEOUSLY_EXECUTABLE   all legs could be submitted together
    FILLED / SETTLED / REALIZED  requires an account. NOT REACHABLE FROM HERE.

This module can label a candidate DEPTH_EXECUTABLE and can reason about
SIMULTANEOUSLY_EXECUTABLE. It can never label one FILLED or beyond, because it
places no orders and reads no account. Nothing downstream may promote a label.

TOUCH IS NEVER FILL. Every number is computed by consuming the ladder level by
level in price order. A quantity larger than the touch pays the next level's
price, and a quantity larger than the whole visible ladder is REFUSED rather
than extrapolated.

DISPLAYED SIZE IS NOT PROMISED SIZE. The book above was observed moving between
two reads seconds apart. So capacity here means "resting when measured", and the
report says so; it is an upper bound on what a taker would actually get.

FEES ARE CHARGED PER FILL, AT THE TAKER RATE, ON EVERY LEG. See `fees.py` for
why the maker rate and every rebate are excluded.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

from src.gemini_arb_discovery import books as BK
from src.gemini_arb_discovery.fees import taker_fee

THEORETICAL = "THEORETICAL"
QUOTED = "QUOTED"
DEPTH_EXECUTABLE = "DEPTH_EXECUTABLE"

#: buying YES consumes the ask side; buying NO consumes the bid side, because
#: selling YES at the bid IS buying NO at one minus the bid on a single book.
BUY_YES = "BUY_YES"
BUY_NO = "BUY_NO"


def _unit_price(side_action: str, level_price: D) -> D:
    return level_price if side_action == BUY_YES else (D("1") - level_price)


def walk_leg(book: Dict[str, Any], side_action: str, qty: D) -> Optional[Dict[str, Any]]:
    """Cost of taking `qty` contracts, consuming the ladder in price order.

    Returns None when the visible ladder cannot supply `qty` -- the caller must
    treat that as "not executable at this size", never as a partial fill at the
    touch.

    The fee is accumulated PER LEVEL, because each level is a separate fill and
    the venue rounds each one up to the next cent independently. Summing a single
    ceil over the VWAP would under-charge a multi-level sweep.
    """
    levels = book.get("asks" if side_action == BUY_YES else "bids") or []
    remaining = qty
    cost = D("0")
    fee = D("0")
    used: List[Dict[str, str]] = []
    for price, amount in levels:
        if remaining <= 0:
            break
        take = amount if amount < remaining else remaining
        up = _unit_price(side_action, price)
        if up <= 0 or up >= 1:
            continue  # a 0 or 1 print carries no fee and no risk; skip as unusable
        cost += up * take
        fee += taker_fee(take, up)
        used.append({"level_price": str(price), "unit_price": str(up),
                     "qty": str(take)})
        remaining -= take
    if remaining > 0:
        return None
    return {"cost": cost, "fee": fee, "levels": used,
            "vwap": (cost / qty) if qty else D("0")}


def evaluate(legs: List[Dict[str, Any]], qty: D, *,
             min_payoff_per_unit: D = D("1")) -> Optional[Dict[str, Any]]:
    """Net locked profit for one basket at one size.

    `legs` is a list of {"symbol", "action", "book"}. `min_payoff_per_unit` is the
    WORST-CASE settlement payoff of the whole basket per unit -- 1 for an
    exhaustive partition, and 1 for a monotonicity pair whose payoff is 1 or 2
    depending on where the underlying lands. The worst case is what gets used;
    the upside is never priced.
    """
    gross = min_payoff_per_unit * qty
    capital = D("0")
    fees = D("0")
    detail = []
    for leg in legs:
        w = walk_leg(leg["book"], leg["action"], qty)
        if w is None:
            return None
        capital += w["cost"]
        fees += w["fee"]
        detail.append({"symbol": leg["symbol"], "action": leg["action"],
                       "cost": str(w["cost"]), "fee": str(w["fee"]),
                       "vwap": str(w["vwap"]), "levels": w["levels"]})
    net = gross - capital - fees
    return {"qty": str(qty), "capital_required": str(capital),
            "gross_locked_payoff": str(gross), "fees": str(fees),
            "net_locked_profit": str(net),
            "roi_on_capital": str((net / capital)) if capital > 0 else None,
            "legs": detail}


def _breakpoints(legs: List[Dict[str, Any]], *, increment: D, maximum: D) -> List[D]:
    """Candidate sizes worth testing: the cumulative-depth knees, and nothing else.

    WHY THIS IS EXACT, not a shortcut. Cost is piecewise linear and convex in
    size (each new level is priced worse than the last) and the guaranteed payoff
    is linear, so net profit is piecewise linear and CONCAVE. A concave piecewise
    linear function attains its maximum at a breakpoint, and the breakpoints are
    exactly the cumulative depths at which some leg starts consuming its next
    price level.

    WHY IT MATTERS HERE. Every contract on this venue quotes
    `quantityIncrement = 0.01`, so a uniform sweep to a 500-contract cap would be
    50,000 basket revaluations per candidate -- slow enough that the scanner
    would be unusable in the moment it finally had something to price. The knee
    set is typically a handful of sizes and gives the same answer.
    """
    pts = {increment}
    for leg in legs:
        levels = leg["book"].get("asks" if leg["action"] == BUY_YES else "bids") or []
        cum = D("0")
        for _, amount in levels:
            cum += amount
            if cum <= maximum:
                pts.add(cum)
    out = []
    for q in sorted(pts):
        # snap DOWN to a tradeable multiple of the increment
        k = (q / increment).to_integral_value(rounding="ROUND_FLOOR")
        qq = k * increment
        if increment <= qq <= maximum:
            out.append(qq)
    return sorted(set(out))


def best_size(legs: List[Dict[str, Any]], *, increment: D, maximum: D,
              min_payoff_per_unit: D = D("1")) -> Dict[str, Any]:
    """The size that maximises NET DOLLARS, and the largest size still above zero.

    Ranking on dollars rather than percentage is deliberate: a 40% return on two
    cents of capital is a rounding error, and the brief asks which opportunities
    are size-meaningful.
    """
    best: Optional[Dict[str, Any]] = None
    capacity: Optional[Dict[str, Any]] = None
    for q in _breakpoints(legs, increment=increment, maximum=maximum):
        ev = evaluate(legs, q, min_payoff_per_unit=min_payoff_per_unit)
        if ev is None:
            break  # ladder exhausted; deeper knees are infeasible too
        net = D(ev["net_locked_profit"])
        if net > 0:
            if best is None or net > D(best["net_locked_profit"]):
                best = ev
            if capacity is None or q > D(capacity["qty"]):
                capacity = ev
    return {"best": best, "max_profitable": capacity}
