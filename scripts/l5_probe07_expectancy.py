"""Probe 07: where the -$0.77180 went, and what one lever would have changed.

Reads the authoritative venue order rows and nothing else. The chronology is
the venue's own lastTransactTime -- inventory changes when a fill happens,
not when an order is inserted.
"""
import json
from decimal import Decimal as D
import datetime as dt

from src.l5_expectancy_replay import (Fill, InventoryWalk, Native, ResidualCap,
                                      ResidualAge, ComplementRepair, replay,
                                      pooled_split, UP, DOWN)

SRC = "analysis/pmus/evidence/probe07_orders.json"
WINNER = UP            # settlementPx 1.0000 on the YES/UP token. Scoring only.


def load():
    doc = json.load(open(SRC))
    fills = []
    for o in doc["orders"]:
        wire = D(o["avgPx"])
        q = D(str(o["cumQuantity"]))
        # BUY_SHORT at wire p == buy DOWN(NO) at (1-p). Exact, not approximate.
        if o["intent"] == "ORDER_INTENT_BUY_SHORT":
            side, px = DOWN, D(1) - wire
        else:
            side, px = UP, wire
        t = dt.datetime.fromisoformat(
            o["lastTransactTime"].replace("Z", "+00:00")).timestamp()
        ins = dt.datetime.fromisoformat(
            o["insertTime"].replace("Z", "+00:00")).timestamp()
        fills.append(Fill(t, side, px, q,
                          rebate=-D(o["commission"]), ref=o["id"]))
        fills[-1].insert_t = ins           # type: ignore[attr-defined]
    return doc, sorted(fills, key=lambda f: (f.t, f.insert_t))


def money(x):
    return "n/a" if x is None else ("%+.6f" % x)


def main():
    doc, fills = load()
    print("PROBE 07 -- CHRONOLOGICAL DECOMPOSITION")
    print("market %s   settlementPx %s (%s wins)"
          % (doc["market"], doc["settlementPx"], WINNER))
    print()
    hdr = ("%-3s %-8s %-4s %6s %7s | %7s %10s %9s | %7s %9s"
           % ("#", "fill utc", "side", "px", "qty", "matched", "pair_cost",
              "locked", "resid", "resid_cost"))
    print(hdr)
    print("-" * len(hdr))
    walk = InventoryWalk(doc["market"])
    prev_m, prev_l = D(0), D(0)
    for i, f in enumerate(fills, 1):
        walk.accept(f)
        dm = walk.matched_pairs - prev_m
        dl = walk.matched_locked_pnl - prev_l
        pc = (walk.matched_cost - (prev_m and walk.matched_cost)) if False else None
        legs = [x for x in walk.legs if x["t"] == f.t]
        pcs = "/".join(x["pair_cost"] for x in legs[-3:]) if dm else "-"
        r = walk.residual()
        print("%-3d %-8s %-4s %6s %7s | %7s %10s %9s | %7s %9s"
              % (i, dt.datetime.utcfromtimestamp(f.t).strftime("%H:%M:%S"),
                 f.side, f.price, f.qty, dm, pcs, ("%+.4f" % dl),
                 ("%+.2f" % r), ("%.4f" % walk.residual_cost())))
        prev_m, prev_l = walk.matched_pairs, walk.matched_locked_pnl
    res = walk.settle(WINNER)
    print()
    print("FIFO SPLIT (preregistered convention)")
    for k in ("matched_qty", "matched_cost", "matched_redemption",
              "matched_pnl", "avg_pair_cost", "maker_rebates",
              "residual_qty", "residual_side", "residual_cost",
              "residual_pnl", "total"):
        v = res[k]
        print("   %-20s %s" % (k, v if isinstance(v, str) or v is None
                               else ("%s" % v)))
    print()
    pooled = pooled_split(fills, WINNER)
    print("POOLED (average-cost) SPLIT, to show the convention's weight")
    print("   matched_pnl %s   residual_pnl %s   total %s"
          % (pooled["matched_pnl"], pooled["residual_pnl"], pooled["total"]))
    print()
    print("RECONCILIATION")
    tgt = D("-0.771800")
    got = res["total"]
    print("   matched %s + residual %s + rebates %s = %s   target %s   %s"
          % (money(res["matched_pnl"]), money(res["residual_pnl"]),
             money(res["maker_rebates"]), money(got), money(tgt),
             "EXACT" if got == tgt else "MISMATCH %s" % (got - tgt)))
    print("   independent check: redemption %s - spend %s + rebates %s = %s"
          % (res["redemption"], res["spend"], res["maker_rebates"],
             money(res["redemption"] - res["spend"] + res["maker_rebates"])))
    print()

    print("TREATMENTS, one at a time, settlement-blind")
    clip = max(f.qty for f in fills)
    pols = [Native()]
    # k < 1 is degenerate: a cap below one clip cannot open any position
    for k in ("1", "1.5", "2", "3"):
        pols.append(ResidualCap(D(k)))
    for s in (60, 120, 300):
        pols.append(ResidualAge(s))
    pols.append(ComplementRepair())
    w_end = max(f.t for f in fills)
    rows = []
    for p in pols:
        r = replay(fills, p, winner=WINNER, market=doc["market"],
                   window_end=w_end)
        rows.append(r)
    h = ("%-22s %5s %5s %8s %9s %7s %9s %8s %9s %8s"
         % ("policy", "took", "supp", "matched", "match_pnl", "resid",
            "resid_pnl", "rebate", "TOTAL", "peak|r|"))
    print(h)
    print("-" * len(h))
    for r in rows:
        print("%-22s %5d %5d %8s %9s %7s %9s %8s %9s %8s"
              % (r["policy"], r["actions_taken"], r["actions_suppressed"],
                 r["matched_qty"], money(r["matched_pnl"]), r["residual_qty"],
                 money(r["residual_pnl"]), money(r["maker_rebates"]),
                 money(r["total"]), r["peak_abs_residual"]))
    print()
    print("running max clip at end: %s" % clip)
    json.dump([{k: (str(v) if isinstance(v, D) else v)
                for k, v in r.items() if k != "suppressed"} for r in rows],
              open("analysis/pmus/evidence/probe07_treatments.json", "w"),
              indent=1, default=str)
    return rows


if __name__ == "__main__":
    main()
