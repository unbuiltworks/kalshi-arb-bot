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


# ------------------------------------------------------------------
# C. ONE ROUND AT A TIME, FROM THE REAL FIRST FILL OF EACH WINDOW
#
# With one round open, the real event in each window is its FIRST fill and
# the quote resting opposite it. Venue-confirmed: 0400z order DN6CYDFG14EX,
# DOWN 0.40 at 0.48, its sibling UP resting at 0.47; 0415z order
# DN6KVVR0144P, UP 0.44 at 0.32, its sibling DOWN resting at 0.63.
#: (window, held side, contracts, price paid, filled by s, sibling price,
#:  sibling placed at s)
FIRST = [("0400z", "DOWN", D("0.40"), D("0.48"), 25.0, D("0.47"), 10.0),
         ("0415z", "UP", D("0.44"), D("0.32"), 22.0, D("0.63"), 18.0)]
#: the venue's published coefficient; every fill of ours was charged 0.0000
VENUE_FEE_COEFFICIENT = D("0.095")


def _floor(x: D) -> D:
    return (x / RP.TICK).to_integral_value(rounding="ROUND_FLOOR") * RP.TICK


def one_round(name: str, side: str, qty: D, px0: D, t0: float, sib_px: D,
              sib_t: float, *, policy: str, target: D = D("0.98"),
              delay: float = 8.0, latency: float = 0.0,
              touch_fill: D = D("1"), fee: D = D("0"),
              terminal: float = 600.0, life: float = 120.0
              ) -> Dict[str, Any]:
    """The missing leg after one real fill, on the recorded book.

    `policy` DEPLOYED: the sibling stays at its opening price until its
    120 s lifetime ends, then a completion is quoted at the touch whenever
    that pair is at or under $1.00. RECOVERY: the sibling is withdrawn
    `delay` seconds after the fill (8 s measured in the runner) and the
    missing leg rests at the dearer of nothing and min(touch, target -
    paid). `latency` keeps a withdrawn quote fillable and delays its
    replacement. `touch_fill` is the share that fills when the offer only
    REACHES our price (1 = all of it, 0 = it must trade through).
    """
    path = RP.load(name)
    other = "UP" if side == "DOWN" else "DOWN"
    cap = _floor((target if policy == "RECOVERY" else D("1.00")) - px0)
    remaining, got, spent, touched = qty, D(0), D(0), set()
    done_t = None
    resting = None                    # a quote of ours that is already live
    for t, b, a in path:
        if not (D(0) < b <= a < D(1)) or t <= t0:
            continue
        if t >= terminal or remaining <= 0:
            break
        tch, off = RP.touch(other, b, a), RP.offer(other, b, a)
        quotes = []
        sib_live = t < (t0 + delay + latency if policy == "RECOVERY"
                        else sib_t + life)
        if sib_live:
            quotes.append(sib_px)
        else:
            # A RESTING QUOTE STAYS WHERE IT IS. It is checked against this
            # sample's offer at the price it already had, and only then
            # moved: up toward the touch (never past the cap, never onto
            # the offer), and never down -- a bid the market has come back
            # to is the best bid, not a stale one.
            if resting is not None:
                quotes.append(resting)
            want = None
            if policy == "RECOVERY":
                want = min(tch, cap)
            elif tch <= cap:
                want = tch
            if want is not None and want > 0:
                want = min(want, off - RP.TICK)
                if resting is None or want > resting:
                    resting = want
        for q in quotes:
            if q <= 0:
                continue
            if off < q:
                fill = remaining
            elif off == q and (q, "t") not in touched:
                fill = (remaining * touch_fill).quantize(
                    RP.TICK, rounding="ROUND_FLOOR")
                touched.add((q, "t"))
            else:
                fill = D(0)
            if fill > 0:
                got += fill
                spent += fill * q
                remaining -= fill
                if remaining <= 0:
                    done_t = float(t)
    def fee_of(px: D, n: D) -> D:
        return fee * px * (D(1) - px) * n
    fees = fee_of(px0, qty) + (fee_of(spent / got, got) if got > 0 else D(0))
    pair = (px0 + spent / got) if got > 0 else None
    locked = (got * (D(1) - px0) - spent) if got > 0 else D(0)
    won = RP.WINDOWS[name][1] == side
    tonight = locked + remaining * ((D(1) if won else D(0)) - px0) - fees
    expect = locked + remaining * RP.OWN_RECORD_PER_UNMATCHED - fees
    return {"window": name, "pairs": got, "pair_cost": pair,
            "unmatched": remaining,
            "seconds_unmatched": round((done_t or terminal) - t0, 0),
            "fees": fees, "locked": locked, "tonight": tonight,
            "expected": expect}


ONE_ROUND = [
    ("deployed: sibling left 120 s, then touch inside $1.00",
     dict(policy="DEPLOYED")),
    ("recovery, 0.98 target", dict(policy="RECOVERY")),
    ("recovery, 0.98, cancel 6 s late", dict(policy="RECOVERY", latency=6.0)),
    ("recovery, 0.98, half fills at a touch",
     dict(policy="RECOVERY", touch_fill=D("0.5"))),
    ("recovery, 0.98, must trade through",
     dict(policy="RECOVERY", touch_fill=D("0"))),
    ("recovery, 0.98, fees charged at the venue coefficient",
     dict(policy="RECOVERY", fee=VENUE_FEE_COEFFICIENT)),
    ("recovery, 0.98, late + through + fees",
     dict(policy="RECOVERY", latency=6.0, touch_fill=D("0"),
          fee=VENUE_FEE_COEFFICIENT)),
    ("recovery, 0.98, open to minute 14",
     dict(policy="RECOVERY", terminal=840.0)),
    ("recovery, $1.00 target", dict(policy="RECOVERY", target=D("1.00"))),
    ("recovery, $1.00, late + through + fees",
     dict(policy="RECOVERY", target=D("1.00"), latency=6.0,
          touch_fill=D("0"), fee=VENUE_FEE_COEFFICIENT)),
    ("recovery, $1.00, open to minute 14",
     dict(policy="RECOVERY", target=D("1.00"), terminal=840.0)),
]


def one_round_table() -> List[Dict[str, Any]]:
    out = []
    for label, kw in ONE_ROUND:
        rows = [one_round(*ev, **kw) for ev in FIRST]
        out.append({
            "policy": label,
            "windows": [{k: (None if v is None else str(v))
                         for k, v in r.items()} for r in rows],
            "pairs": str(sum(r["pairs"] for r in rows)),
            "unmatched": str(sum(r["unmatched"] for r in rows)),
            "fees": str(sum(r["fees"] for r in rows)),
            "locked": str(sum(r["locked"] for r in rows)),
            "tonight": str(sum(r["tonight"] for r in rows)),
            "expected": str(sum(r["expected"] for r in rows))})
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
    one = one_round_table()
    print("C. ONE ROUND AT A TIME, FROM THE REAL FIRST FILL (0400z DOWN 0.40 "
          "@ 0.48; 0415z UP 0.44 @ 0.32)")
    print("  %-52s %-11s %-13s %-9s %-11s %7s %8s %8s %8s" % (
        "policy", "pairs", "pair cost", "unmatched", "s unmatched", "fees",
        "locked", "tonight", "expect"))
    for r in one:
        w = r["windows"]
        print("  %-52s %-11s %-13s %-9s %-11s %7.4f %+8.4f %+8.4f %+8.4f" % (
            r["policy"][:52], "/".join(x["pairs"] for x in w),
            "/".join((x["pair_cost"] or "-")[:6] for x in w),
            "/".join(x["unmatched"] for x in w),
            "/".join(x["seconds_unmatched"][:-2] for x in w),
            D(r["fees"]), D(r["locked"]), D(r["tonight"]), D(r["expected"])))
    with open(os.path.join(HERE, "inventory_replay_20261111.json"), "w") as f:
        json.dump({"events": ev, "recoveries": rec, "one_round": one,
                   "policies": pol,
                   "own_record_per_unmatched_contract":
                   str(RP.OWN_RECORD_PER_UNMATCHED)}, f, indent=1)


if __name__ == "__main__":
    main()
