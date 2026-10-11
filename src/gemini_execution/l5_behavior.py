"""L5's SEQUENTIAL STATE, reconstructed fill by fill.

THE QUESTION THIS SERVES. Not "does OPEN -> EXTEND -> COMPLEMENT pay",
which the frozen engine already answered in the negative, but: at every
L5 fill, given the inventory immediately beforehand, what caused the next
SIDE, PRICE, SIZE and TIMING?

So this module does one thing: turn a window's trade sequence into the
(state-before, action-taken) pairs that a behavioral model can be fitted
to and, more importantly, TESTED on out of sample. It contains no model
and no thresholds. It is deliberately separate from the frozen policy,
which remains the control and is not imported here.

TWO PAIR-COST NOTIONS, KEPT APART BECAUSE THEY ANSWER DIFFERENT THINGS:

  book_pair_cost  = avg_up + avg_down over the inventory HELD. Exact,
                    computed from L5's own fills, and the thing that
                    decides whether the book settles profitably.
  print_pair_cost = last observed UP print + last observed DOWN print.
                    An APPROXIMATION of the instantaneous market pair
                    cost, built from L5's own trades because we do not
                    have their order book. It is a lower bound on what
                    was available to them at best and a stale quote at
                    worst; it is labelled, never presented as the book.

WHAT A "SIZE" IS. Polymarket sizes are share counts and are fractional
(0.01 to 100 observed). Dollar risk is size x price, so both are carried:
a 100-share buy at 0.10 is $10, a 5-share buy at 0.90 is $4.50.

NO FROZEN PARAMETER IS READ OR WRITTEN HERE, and nothing in this module
is authorization to change one.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

UP = "UP"
DOWN = "DOWN"
SAME_SIDE = "SAME_SIDE"
SWITCH_SIDE = "SWITCH_SIDE"


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _other(side: str) -> str:
    return DOWN if side == UP else UP


def reconstruct(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(state BEFORE the fill, action OF the fill) for every fill.

    `trades` must be one window's fills in time order, each carrying
    ts, tte, price, outcome (UP/DOWN) and size.

    The first fill has no prior inventory; it is emitted with a flat
    state so entry conditions are measurable on the same footing.
    """
    qty = {UP: 0.0, DOWN: 0.0}
    cost = {UP: 0.0, DOWN: 0.0}
    last_print = {UP: None, DOWN: None}      # type: Dict[str, Optional[float]]
    prev: Optional[Dict[str, Any]] = None
    switches = 0
    since_switch = 0
    out: List[Dict[str, Any]] = []

    for i, t in enumerate(trades):
        side = str(t.get("outcome") or "").upper()
        if side not in (UP, DOWN):
            continue
        price = _f(t.get("price"))
        size = _f(t.get("size")) or 0.0
        if price is None:
            continue

        matched = min(qty[UP], qty[DOWN])
        avg_up = (cost[UP] / qty[UP]) if qty[UP] else None
        avg_dn = (cost[DOWN] / qty[DOWN]) if qty[DOWN] else None
        matched_cost = (matched * ((avg_up or 0.0) + (avg_dn or 0.0))
                        if matched else 0.0)
        resid_side = (UP if qty[UP] > qty[DOWN]
                      else (DOWN if qty[DOWN] > qty[UP] else None))
        resid_qty = abs(qty[UP] - qty[DOWN])
        resid_avg = (avg_up if resid_side == UP else
                     (avg_dn if resid_side == DOWN else None))
        book_pair = ((avg_up + avg_dn) if (avg_up is not None
                                           and avg_dn is not None) else None)
        pu, pd = last_print[UP], last_print[DOWN]
        print_pair = ((pu + pd) if (pu is not None and pd is not None)
                      else None)

        state = {
            "i": i, "tte": _f(t.get("tte")),
            "up_qty": qty[UP], "down_qty": qty[DOWN],
            "up_cost": cost[UP], "down_cost": cost[DOWN],
            "avg_up": avg_up, "avg_down": avg_dn,
            "matched_qty": matched, "matched_cost": matched_cost,
            "residual_side": resid_side, "residual_qty": resid_qty,
            "residual_avg": resid_avg,
            "residual_principal": (resid_qty * resid_avg
                                   if resid_avg is not None else 0.0),
            # imbalance: signed shares, and the SHARE of the book that is
            # unhedged, which is scale-free and comparable across windows.
            "imbalance_shares": qty[UP] - qty[DOWN],
            "imbalance_frac": ((qty[UP] - qty[DOWN])
                               / (qty[UP] + qty[DOWN])
                               if (qty[UP] + qty[DOWN]) else 0.0),
            "book_pair_cost": book_pair,
            "print_pair_cost": print_pair,
            "last_print_up": pu, "last_print_down": pd,
            "prev_side": (prev or {}).get("side"),
            "prev_price": (prev or {}).get("price"),
            "prev_size": (prev or {}).get("size"),
            "seconds_since_prev": (
                None if prev is None
                else (prev["tte"] - _f(t.get("tte"))
                      if prev.get("tte") is not None
                      and _f(t.get("tte")) is not None else None)),
            # price change on THE SIDE ABOUT TO BE BOUGHT, since the last
            # print on that side. This is the quantity a "buy it cheaper"
            # rule would key on.
            "price_change_same_side": (
                None if last_print[side] is None
                else price - last_print[side]),
            "price_change_since_prev_fill": (
                None if prev is None or prev.get("price") is None
                else price - prev["price"]),
            "switch_count": switches,
            "trades_since_switch": since_switch,
            "is_first_fill": prev is None,
        }
        action = {
            "side": side, "price": price, "size": size,
            "dollars": size * price,
            "side_relation": (SAME_SIDE if (prev and prev["side"] == side)
                              else (SWITCH_SIDE if prev else "ENTRY")),
            # the price PAID relative to our own average on that side:
            # negative means improving the average.
            "price_vs_own_avg": (
                None if (qty[side] == 0 or cost[side] == 0)
                else price - (cost[side] / qty[side])),
            "price_vs_other_side_complement": (
                None if last_print[_other(side)] is None
                else price - (1.0 - last_print[_other(side)])),
        }
        out.append({"state": state, "action": action})

        if prev is not None and prev["side"] != side:
            switches += 1
            since_switch = 0
        else:
            since_switch += 1
        qty[side] += size
        cost[side] += size * price
        last_print[side] = price
        prev = {"side": side, "price": price, "size": size,
                "tte": _f(t.get("tte"))}
    return out


def window_summary(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Terminal facts about a window, from its reconstructed steps."""
    if not steps:
        return {"n_fills": 0}
    last = steps[-1]
    s, a = last["state"], last["action"]
    up = s["up_qty"] + (a["size"] if a["side"] == UP else 0.0)
    dn = s["down_qty"] + (a["size"] if a["side"] == DOWN else 0.0)
    cu = s["up_cost"] + (a["dollars"] if a["side"] == UP else 0.0)
    cd = s["down_cost"] + (a["dollars"] if a["side"] == DOWN else 0.0)
    matched = min(up, dn)
    return {
        "n_fills": len(steps),
        "up_qty": up, "down_qty": dn, "up_cost": cu, "down_cost": cd,
        "avg_up": (cu / up if up else None),
        "avg_down": (cd / dn if dn else None),
        "final_book_pair_cost": ((cu / up + cd / dn) if up and dn else None),
        "matched_qty": matched,
        "residual_qty": abs(up - dn),
        "residual_side": (UP if up > dn else (DOWN if dn > up else None)),
        "completed": matched > 0,
        "switches": steps[-1]["state"]["switch_count"],
        "peak_imbalance_shares": max(
            abs(st["state"]["imbalance_shares"]) for st in steps),
        "peak_residual_principal": max(
            st["state"]["residual_principal"] for st in steps),
        "max_book_pair_cost": max(
            [st["state"]["book_pair_cost"] for st in steps
             if st["state"]["book_pair_cost"] is not None] or [float("nan")]),
        "dollars_deployed": cu + cd,
    }
