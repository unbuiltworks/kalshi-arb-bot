"""The same three treatments, over L5's own Sep 23-25 fills. Out of sample.

Probe 07 is one window and nine fills. It can show a mechanism; it cannot
show an expectancy. L5's own three days are 22,512 real fills across 858
market-days with real settlements, and they are the only set large enough to
say whether suppressing same-side acquisition while imbalanced actually
helps.

PREREGISTERED, before any result was read:
  * thresholds come from inventory geometry, never from a sweep over
    settled outcomes -- the cap is k * the largest clip the market has
    ALREADY shown, the age limit is a fraction of the 900s window.
  * k < 1 is degenerate: a cap below one clip cannot open any position at
    all, so the grid starts at k = 1.
  * Sep 23 is the fit day. Sep 24 and Sep 25 are held out and are scored
    once, with whatever Sep 23 selected.
  * the counterfactual is fill-for-fill: an action taken fills as recorded,
    an action suppressed does not happen. Load-bearing, and stated.
  * incentives are MODELLED for the treatments (day rebate scaled by the
    fraction of quantity still taken) because the venue reports them as one
    daily lump with no conditionId. Native's figure is the real one. A
    modelled rebate is never called realized.
"""
import json, collections, sys
from decimal import Decimal as D

from src.l5_expectancy_replay import (Fill, Native, ResidualCap, ResidualAge,
                                      ComplementRepair, replay, drawdown,
                                      UP, DOWN)

SRC = "data/l5/raw/sep/l5_sep23_25_daily.json"
FIT_DAY = "2026-09-23"
OOS_DAYS = ("2026-09-24", "2026-09-25")
IDX = {0: UP, 1: DOWN}


def episodes(day_rows):
    """One episode per market: its fills in time order, and who won."""
    tr = collections.defaultdict(list)
    for t in day_rows["TRADE"]:
        tr[t["conditionId"]].append(t)
    win = {}
    for r in day_rows["REDEEM"]:
        win[r["conditionId"]] = IDX.get(r["outcomeIndex"])
    out = []
    unscorable = 0
    for cid, rows in tr.items():
        rows.sort(key=lambda x: x["timestamp"])
        fills = []
        for x in rows:
            q = D(str(x["size"]))
            if q <= 0:
                continue
            usd = D(str(x["usdcSize"]))
            fills.append(Fill(float(x["timestamp"]), IDX[x["outcomeIndex"]],
                              usd / q, q, ref=x["transactionHash"]))
        if not fills:
            continue
        w = win.get(cid)
        if w is None:
            sides = {f.side for f in fills}
            if len(sides) == 1:
                # he held one side and redeemed nothing: the other side won
                w = DOWN if sides == {UP} else UP
            else:
                unscorable += 1
                continue
        out.append((cid, fills, w, rows[-1]["timestamp"]))
    return out, unscorable


def day_incentive(day_rows):
    tot = D(0)
    for k in ("MAKER_REBATE", "TAKER_REBATE"):
        for x in day_rows.get(k) or []:
            tot += D(str(x["usdcSize"]))
    return tot


def run_day(eps, policy, incentive):
    agg = collections.Counter()
    acc = {k: D(0) for k in ("matched_qty", "matched_cost", "matched_pnl",
                             "residual_qty", "residual_cost", "residual_pnl",
                             "spend", "qty_offered", "qty_taken")}
    peak_r = D(0)
    series = []
    for cid, fills, w, t_end in sorted(eps, key=lambda e: e[3]):
        r = replay(fills, policy, winner=w, market=cid)
        for k in acc:
            v = r.get(k)
            if v is not None:
                acc[k] += v
        agg["offered"] += r["actions_offered"]
        agg["taken"] += r["actions_taken"]
        agg["suppressed"] += r["actions_suppressed"]
        agg["markets"] += 1
        if r["residual_qty"] > 0:
            agg["markets_with_residual"] += 1
        if r["peak_abs_residual"] > peak_r:
            peak_r = r["peak_abs_residual"]
        series.append(r["matched_pnl"] + (r["residual_pnl"] or D(0)))
    frac = (acc["qty_taken"] / acc["qty_offered"]) if acc["qty_offered"] else D(0)
    modelled = incentive * frac
    trading = acc["matched_pnl"] + acc["residual_pnl"]
    return {"policy": policy.name, "counts": dict(agg),
            "peak_abs_residual": peak_r, "drawdown": drawdown(series),
            "trading": trading, "incentives": modelled,
            "incentives_real": incentive,
            "total": trading + modelled, "qty_frac": frac, **acc}


def fmt(x, n=1):
    if x is None:
        return "n/a"
    return format(float(x), "+,.%df" % n)


def policies():
    p = [Native()]
    p += [ResidualCap(D(k)) for k in ("1", "1.5", "2", "3")]
    p += [ResidualAge(s) for s in (60, 120, 300)]
    p += [ComplementRepair()]
    return p


def table(title, rows, base):
    print()
    print(title)
    h = ("%-22s %7s %7s %10s %11s %10s %12s %11s %11s %9s"
         % ("policy", "took", "supp", "matched", "match_pnl", "resid_qty",
            "residual_pnl", "trading", "TOTAL", "vs NATIVE"))
    print(h); print("-" * len(h))
    for r in rows:
        print("%-22s %7d %7d %10s %11s %10s %12s %11s %11s %9s"
              % (r["policy"], r["counts"]["taken"], r["counts"]["suppressed"],
                 fmt(r["matched_qty"], 0), fmt(r["matched_pnl"]),
                 fmt(r["residual_qty"], 0), fmt(r["residual_pnl"]),
                 fmt(r["trading"]), fmt(r["total"]),
                 fmt(r["total"] - base["total"])))


def main():
    doc = json.load(open(SRC))
    allrows = {}
    for day in [FIT_DAY] + list(OOS_DAYS):
        eps, uns = episodes(doc[day])
        inc = day_incentive(doc[day])
        rows = [run_day(eps, p, inc) for p in policies()]
        allrows[day] = rows
        print("%s  markets scored %d  unscorable(two-sided, no redeem) %d  "
              "real incentives %s" % (day, len(eps), uns, fmt(inc)))
        table("  %s  %s" % (day, "FIT DAY" if day == FIT_DAY else "HELD OUT"),
              rows, rows[0])
    # pooled held-out
    print()
    pooled = []
    for i, p in enumerate(policies()):
        acc = collections.Counter()
        tot = {k: D(0) for k in ("matched_qty", "matched_pnl", "residual_qty",
                                 "residual_pnl", "trading", "total",
                                 "incentives")}
        dd = D(0)
        for day in OOS_DAYS:
            r = allrows[day][i]
            for k in tot:
                tot[k] += r[k]
            for k, v in r["counts"].items():
                acc[k] += v
            dd += r["drawdown"]
        pooled.append({"policy": p.name, "counts": dict(acc), "drawdown": dd,
                       **tot})
    table("HELD OUT, Sep 24 + Sep 25 pooled", pooled, pooled[0])
    json.dump({d: [{k: str(v) for k, v in r.items() if k != "counts"}
                   | {"counts": r["counts"]} for r in rs]
               for d, rs in allrows.items()},
              open("analysis/l5/treatment_oos.json", "w"), indent=1,
              default=str)


if __name__ == "__main__":
    main()
