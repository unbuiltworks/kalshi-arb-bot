"""Inventory recovery and 15-second repricing, against tonight's real fills.

Two questions, on the venue book recorded for 2026-11-11 04:00Z and 04:15Z:

  A. For each fill the venue actually confirmed, what did completing the
     pair cost at each moment afterwards, and what was the held leg worth?
     (No model: our real fills against the recorded book.)

  B. What would each controller policy have done over the same two windows?
     (The policy model in `replay_20261111.py`: a bid fills only when the
     book's offer comes to it, so every order ahead of it has filled; no
     fees, no rebates.)

LIMITS OF THE EVIDENCE. Two windows. Book samples are 5 to 7 seconds apart,
so a 15-second quote is two or three samples and a move inside one sample
is not seen. `expected` values an unmatched contract at what our own 89
settled markets realized per unmatched unit (-18.55c); it is a statement
of our record, not a forecast. Nothing here is realized P&L.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List

import replay_20261111 as RP

HERE = os.path.dirname(os.path.abspath(__file__))
#: venue-confirmed: (window, held side, contracts, cash paid, last fill s)
REAL = [("0400z", "DOWN", D("1.18"), D("0.5508"), 25.0),
        ("0415z", "UP", D("0.44"), D("0.1408"), 22.0)]
HORIZONS = (10, 30, 60, 120, 300)


def events() -> List[Dict[str, Any]]:
    out = []
    for name, side, qty, paid, t0 in REAL:
        path = RP.load(name)
        avg = paid / qty
        other = "UP" if side == "DOWN" else "DOWN"
        row: Dict[str, Any] = {"window": name, "held": side, "qty": str(qty),
                               "avg_cost": str(avg.quantize(D("0.0001"))),
                               "filled_by_s": t0, "after": {}}
        def best(lo, hi):
            xs = [RP.offer(other, b, a) for t, b, a in path
                  if lo < t <= hi and D(0) < b <= a < D(1)]
            return min(xs) if xs else None
        def mark(at):
            xs = [(abs(t - at), (RP.touch(side, b, a)
                                 + RP.offer(side, b, a)) / 2)
                  for t, b, a in path if D(0) < b <= a < D(1)]
            return min(xs)[1] if xs else None
        for h in HORIZONS:
            o = best(t0, t0 + h)
            m = mark(t0 + h)
            row["after"]["+%ds" % h] = {
                "cheapest_completion_pair": (None if o is None
                                             else str(avg + o)),
                "held_leg_mark_minus_cost": (None if m is None
                                             else str((m - avg).quantize(
                                                 D("0.001")))),
            }
        for label, hi in (("to_T+600", 600.0), ("to_T+840", 840.0)):
            o = best(t0, hi)
            row["after"][label] = {"cheapest_completion_pair": (
                None if o is None else str(avg + o))}
        def first_at(ceiling, hi=900.0):
            for t, b, a in path:
                if t > t0 and t <= hi and D(0) < b <= a < D(1) and \
                        avg + RP.offer(other, b, a) <= ceiling:
                    return round(float(t), 0)
            return None
        row["first_pair_at_or_under_1.00_s"] = first_at(D("1.00"))
        row["first_pair_at_or_under_0.96_s"] = first_at(D("0.96"))
        out.append(row)
    return out


def recovery(name: str, side: str, qty: D, paid: D, t0: float, *,
             ceiling: D, terminal: float, wait: float = 14.0,
             latency: float = 0.0) -> Dict[str, Any]:
    """From the REAL fill: rest the missing leg at the dearest price the
    ceiling permits (never above the touch) and see whether the recorded
    book came to it. `wait` is the engine's own minimum rest before it
    moves a quote (REQUOTE_MIN_REST_S); `latency` delays the new quote."""
    path = RP.load(name)
    avg = paid / qty
    other = "UP" if side == "DOWN" else "DOWN"
    cap = ((ceiling - avg) / RP.TICK).to_integral_value(
        rounding="ROUND_FLOOR") * RP.TICK
    live_from = t0 + wait + latency
    resting = None
    for t, b, a in path:
        if not (D(0) < b <= a < D(1)) or t < live_from or t >= terminal:
            continue
        if resting is not None and RP.offer(other, b, a) <= resting:
            pair = avg + resting
            return {"completed_at_s": round(float(t), 0),
                    "seconds_unmatched": round(float(t) - t0, 0),
                    "price": str(resting), "pair_cost": str(pair),
                    "locked_usd": str(((D(1) - pair) * qty).quantize(
                        D("0.0001")))}
        resting = min(RP.touch(other, b, a), cap)
    return {"completed_at_s": None,
            "seconds_unmatched": round(terminal - t0, 0),
            "price": (None if resting is None else str(resting)),
            "pair_cost": None, "locked_usd": None}


def recoveries() -> List[Dict[str, Any]]:
    out = []
    for name, side, qty, paid, t0 in REAL:
        held_outcome = (qty if RP.WINDOWS[name][1] == side else D(0)) - paid
        row = {"window": name, "held": side, "qty": str(qty),
               "held_to_settlement_tonight": str(held_outcome),
               "held_on_own_record": str(
                   (qty * RP.OWN_RECORD_PER_UNMATCHED).quantize(D("0.0001"))),
               "variants": {}}
        for label, kw in (
                ("$1.00 ceiling, today's clocks",
                 dict(ceiling=D("1.00"), terminal=600.0)),
                ("$1.00 ceiling, new quote 6 s late",
                 dict(ceiling=D("1.00"), terminal=600.0, latency=6.0)),
                ("0.98 pair, today's clocks",
                 dict(ceiling=D("0.98"), terminal=600.0)),
                ("0.96 pair, today's clocks",
                 dict(ceiling=D("0.96"), terminal=600.0)),
                ("$1.00 ceiling, open to minute 14",
                 dict(ceiling=D("1.00"), terminal=840.0)),
                ("0.96 pair, open to minute 14",
                 dict(ceiling=D("0.96"), terminal=840.0))):
            row["variants"][label] = recovery(name, side, qty, paid, t0, **kw)
        out.append(row)
    return out


FAST = dict(life=15.0, attempts=16)
POLICIES = [
    ("as run: 120 s quotes, no repricing", {}),
    ("15 s quotes, 16 re-placements", dict(FAST)),
    ("15 s, a cancel takes one sample to land", dict(FAST, cancel_latency=6.0)),
    ("controller: reprice the missing leg on fill, at the touch, "
     "inside $1.00", dict(reprice_on_fill=True, hedge_depth=0,
                          rest_hedge=True)),
    ("controller + 15 s quotes", dict(FAST, reprice_on_fill=True,
                                      hedge_depth=0, rest_hedge=True)),
    ("controller + 15 s, cancel takes one sample", dict(
        FAST, reprice_on_fill=True, hedge_depth=0, rest_hedge=True,
        cancel_latency=6.0)),
    ("controller + 15 s, one round at a time", dict(
        FAST, reprice_on_fill=True, hedge_depth=0, rest_hedge=True,
        concurrent=1)),
    ("controller + 15 s, a new pair may open beside one hedged strand",
     dict(FAST, reprice_on_fill=True, hedge_depth=0, rest_hedge=True,
          heavy_cap=2)),
    ("controller + 15 s, completion open to minute 14", dict(
        FAST, reprice_on_fill=True, hedge_depth=0, rest_hedge=True,
        terminal=840.0)),
    ("controller + 15 s, one round, completion open to minute 14", dict(
        FAST, reprice_on_fill=True, hedge_depth=0, rest_hedge=True,
        concurrent=1, terminal=840.0)),
]


def policies() -> List[Dict[str, Any]]:
    out = []
    for label, over in POLICIES:
        rows = [RP.run(n, **over) for n in RP.WINDOWS]
        tot = lambda k: sum(D(str(r[k])) for r in rows)   # noqa: E731
        out.append({
            "policy": label, "orders": int(tot("orders")),
            "fills": int(tot("fills")),
            "pairs_contracts": str(tot("pairs_contracts")),
            "pair_costs": [None if r["pair_cost"] is None
                           else "%.4f" % r["pair_cost"] for r in rows],
            "unmatched_contracts": str(tot("unmatched")),
            "seconds_unmatched": [r["seconds_unmatched"] for r in rows],
            "locked_pair_pnl": str(tot("locked_pair_pnl")),
            "settled_tonight": [str(r["pnl"]) for r in rows],
            "settled_tonight_both": str(tot("pnl")),
            "expected_on_own_record": str(tot("expected_on_own_record")),
        })
    return out


def main() -> None:
    ev, pol = events(), policies()
    print("A. THE REAL FILLS AGAINST THE RECORDED BOOK")
    for e in ev:
        print("  %s held %s %s @ %s (filled by T+%ds)" % (
            e["window"], e["held"], e["qty"], e["avg_cost"],
            e["filled_by_s"]))
        for k, v in e["after"].items():
            print("     %-9s cheapest completion pair %-7s held leg mark "
                  "- cost %s" % (k, v.get("cheapest_completion_pair"),
                                 v.get("held_leg_mark_minus_cost", "")))
        print("     first pair <= 1.00 at T+%s s; first pair <= 0.96 at "
              "T+%s s" % (e["first_pair_at_or_under_1.00_s"],
                          e["first_pair_at_or_under_0.96_s"]))
    rec = recoveries()
    print("A2. RECOVERY FROM THE REAL FILLS: the missing leg rested at the "
          "ceiling")
    for r in rec:
        print("  %s held %s %s | held to settlement tonight %s | on our own "
              "record %s" % (r["window"], r["held"], r["qty"],
                             r["held_to_settlement_tonight"],
                             r["held_on_own_record"]))
        for k, v in r["variants"].items():
            print("     %-36s completed at T+%-5s unmatched %4ds  pair %-7s "
                  "locked %s" % (k, v["completed_at_s"],
                                 v["seconds_unmatched"],
                                 (v["pair_cost"] or "-")[:6],
                                 v["locked_usd"]))
    print("B. POLICIES OVER BOTH WINDOWS (modelled)")
    print("  %-66s %4s %4s %6s %-16s %6s %-12s %8s %8s %8s" % (
        "policy", "ord", "fill", "pairs", "pair cost", "unm.", "s unmatched",
        "locked", "tonight", "expect"))
    for r in pol:
        print("  %-66s %4d %4d %6s %-16s %6s %-12s %+8.4f %+8.4f %+8.4f" % (
            r["policy"][:66], r["orders"], r["fills"], r["pairs_contracts"],
            "/".join(x or "-" for x in r["pair_costs"]),
            r["unmatched_contracts"],
            "/".join("%d" % x for x in r["seconds_unmatched"]),
            D(r["locked_pair_pnl"]), D(r["settled_tonight_both"]),
            D(r["expected_on_own_record"])))
    with open(os.path.join(HERE, "inventory_replay_20261111.json"), "w") as f:
        json.dump({"events": ev, "recoveries": rec, "policies": pol,
                   "own_record_per_unmatched_contract":
                   str(RP.OWN_RECORD_PER_UNMATCHED)}, f, indent=1)


if __name__ == "__main__":
    main()
