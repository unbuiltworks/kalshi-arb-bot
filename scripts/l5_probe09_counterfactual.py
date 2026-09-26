"""Probe 09 candidate, replayed against Probe 08's own stream. Offline.

THE MODEL, AND WHERE IT COMES FROM
----------------------------------
The frozen journal records `residual_before` at every decision, so the
booking lag is not assumed -- it is read:

    seq 1 DOWN   realized  0        (nothing booked yet)
    seq 2 DOWN   realized  0        the first DOWN had NOT yet booked
    seq 3 UP     realized -3.93     both DOWN fills booked by here
    seq 4..8 UP  realized -3.93     no UP fill booked during the window

So fills book late, and under the REALIZED basis the classifier saw a static
-3.93 for every repair decision it ever made. The counterfactual keeps that
timing and changes only what the classifier counts.

An order that is placed in the counterfactual and was placed in reality is
given its REAL outcome -- filled or never filled, at its real price. An order
the rule suppresses does not exist. Nothing is invented.
"""
import json
from decimal import Decimal as D

from src.l5_expectancy_replay import Fill, InventoryWalk, UP, DOWN
from src.l5_prospective_class import (OutstandingBook, decide, REALIZED,
                                      PROSPECTIVE)

SRC = "analysis/pmus/evidence/probe08/probe08b_sequence.json"
WINNER = UP          # settlementPx 1.0000. Scoring only.

#: which decision index each fill had been BOOKED by, read off the journal's
#: own residual_before column. None = never booked during the window.
BOOKED_BEFORE = {1: 3, 2: 3, 6: None, 7: None, 8: None}


def run(basis):
    doc = json.load(open(SRC))
    orders = sorted(doc["orders"], key=lambda o: o["seq"])
    book = OutstandingBook()
    realized = {"UP": D(0), "DOWN": D(0)}
    placed, suppressed = [], []
    rows = []

    def realized_residual():
        return realized["UP"] - realized["DOWN"]

    for o in orders:
        # book any fill whose booking point has arrived
        for p in placed:
            b = BOOKED_BEFORE.get(p["seq"])
            if b is not None and o["seq"] >= b and not p.get("booked"):
                realized[p["side"]] += D(p["cum_qty"])
                book.fill(p["venue_order_id"], D(p["qty"]))
                p["booked"] = True
        d = decide(realized_residual(), book.signed(), o["side"], basis=basis)
        rows.append({"seq": o["seq"], "side": o["side"],
                     "own_price": o["own_price"], "qty": o["qty"],
                     "realized": d["realized_residual"],
                     "outstanding": d["outstanding_residual"],
                     "prospective": d["prospective_residual"],
                     "klass": d["inventory_class"],
                     "permitted": d["permitted"],
                     "real_outcome": o["venue_state"]})
        if d["permitted"]:
            p = dict(o)
            placed.append(p)
            book.place(o["venue_order_id"], o["side"], D(o["qty"]))
        else:
            suppressed.append(o)

    # settle: an order that was placed gets its REAL outcome
    walk = InventoryWalk(doc["market"])
    for p in sorted(placed, key=lambda x: x["seq"]):
        if p["venue_state"] != "ORDER_STATE_FILLED":
            continue
        side = UP if p["side"] == "UP" else DOWN
        walk.accept(Fill(p["l5_ts"], side, D(p["own_price"]),
                         D(p["cum_qty"]), rebate=-D(p["commission"]),
                         ref=p["venue_order_id"]))
    return rows, placed, suppressed, walk.settle(WINNER)


def show(basis, label):
    rows, placed, suppressed, res = run(basis)
    print("=" * 78)
    print("%s   basis = %s" % (label, basis))
    print("=" * 78)
    h = ("%-4s %-5s %7s %6s | %9s %11s %11s | %-18s %s"
         % ("seq", "side", "px", "qty", "realized", "outstanding",
            "prospective", "class", "verdict"))
    print(h); print("-" * len(h))
    for r in rows:
        print("%-4d %-5s %7s %6s | %9s %11s %11s | %-18s %s"
              % (r["seq"], r["side"], r["own_price"], r["qty"], r["realized"],
                 r["outstanding"], r["prospective"], r["klass"],
                 "placed" if r["permitted"] else "SUPPRESSED"))
    filled = [p for p in placed if p["venue_state"] == "ORDER_STATE_FILLED"]
    rr = [p for p in placed if p["inventory_class"] == "REDUCE_RESIDUAL"]
    rrf = [p for p in rr if p["venue_state"] == "ORDER_STATE_FILLED"]
    print()
    print("  placed %d | suppressed %d | of placed, filled %d"
          % (len(placed), len(suppressed), len(filled)))
    print("  repair orders placed %d, of which filled %d" % (len(rr), len(rrf)))
    for k in ("matched_qty", "matched_pnl", "avg_pair_cost", "residual_qty",
              "residual_side", "residual_pnl", "maker_rebates", "total"):
        print("  %-18s %s" % (k, res[k]))
    print()
    return res


def main():
    a = show(REALIZED, "PROBE 08 AS RUN (control)")
    b = show(PROSPECTIVE, "PROBE 09 CANDIDATE")
    print("=" * 78)
    print("DELTA, candidate minus control")
    print("=" * 78)
    for k in ("matched_qty", "matched_pnl", "residual_qty", "residual_pnl",
              "maker_rebates", "total"):
        x, y = a[k], b[k]
        if isinstance(x, D) and isinstance(y, D):
            print("  %-18s %12s -> %-12s  %+s" % (k, x, y, y - x))
        else:
            print("  %-18s %12s -> %-12s" % (k, x, y))


if __name__ == "__main__":
    main()
