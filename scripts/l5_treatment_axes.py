"""Native beside each treatment on every axis the directive named. No selection."""
import json, collections
from decimal import Decimal as D

from src.l5_expectancy_replay import (Native, ResidualCap, ResidualAge,
                                      ComplementRepair)
from scripts.l5_treatment_oos import episodes, day_incentive, run_day, fmt

OOS = ("2026-09-24", "2026-09-25")


def main():
    doc = json.load(open("data/l5/raw/sep/l5_sep23_25_daily.json"))
    prep = {d: episodes(doc[d])[0] for d in OOS}
    inc = {d: day_incentive(doc[d]) for d in OOS}
    from src.l5_expectancy_replay import PairCostCap
    pols = [Native(), ResidualCap(D("1")), ResidualAge(120),
            ComplementRepair(), PairCostCap(D("1.00")), PairCostCap(D("0.98")),
            PairCostCap(D("0.96"))]
    out = []
    for p in pols:
        rs = [run_day(prep[d], p, inc[d]) for d in OOS]
        a = {k: sum((r[k] for r in rs), D(0)) for k in
             ("matched_qty", "matched_cost", "matched_pnl", "residual_qty",
              "residual_cost", "residual_pnl", "spend", "trading",
              "incentives", "total", "drawdown", "qty_offered", "qty_taken")}
        c = collections.Counter()
        for r in rs:
            c.update(r["counts"])
        a["counts"] = dict(c)
        a["policy"] = p.name
        a["peak_abs_residual"] = max(r["peak_abs_residual"] for r in rs)
        out.append(a)
    base = out[0]
    print("HELD OUT (Sep 24 + Sep 25) -- every axis")
    print()

    hdr = ("%-26s" + " %12s" * len(out)) % tuple(
        ["axis"] + [r["policy"].replace("RESIDUAL_", "").replace("COMPLEMENT_", "")
                    .replace("PAIR_COST_", "PC") for r in out])
    print(hdr); print("-" * len(hdr))

    def row(label, fn, n=1):
        vals = []
        for r in out:
            v = fn(r)
            vals.append(fmt(v, n) if isinstance(v, D) else ("n/a" if v is None else str(v)))
        print(("%-26s" + " %12s" * len(out)) % tuple([label] + vals))

    row("actions taken", lambda r: r["counts"]["taken"])
    row("actions suppressed", lambda r: r["counts"]["suppressed"])
    row("fills suppressed %", lambda r: D(100) * r["counts"]["suppressed"]
        / (r["counts"]["offered"] or 1))
    row("matched qty", lambda r: r["matched_qty"], 0)
    row("matched P&L", lambda r: r["matched_pnl"])
    row("avg pair cost", lambda r: (r["matched_cost"] / r["matched_qty"])
        if r["matched_qty"] else None, 5)
    row("residual qty", lambda r: r["residual_qty"], 0)
    row("residual P&L", lambda r: r["residual_pnl"])
    row("resid loss / share", lambda r: (r["residual_pnl"] / r["residual_qty"])
        if r["residual_qty"] else None, 4)
    row("max residual (1 mkt)", lambda r: r["peak_abs_residual"], 0)
    row("maker economics*", lambda r: r["incentives"])
    row("trading net", lambda r: r["trading"])
    row("TOTAL", lambda r: r["total"])
    row("vs NATIVE (trading)", lambda r: r["trading"] - base["trading"])
    row("capital deployed", lambda r: r["spend"], 0)
    row("return on capital bp", lambda r: D(10000) * r["trading"] / r["spend"]
        if r["spend"] else None)
    row("drawdown", lambda r: r["drawdown"])
    print()
    print("* maker economics is REAL for NATIVE (the venue's own daily rebate")
    print("  rows) and MODELLED for every treatment, scaled by the fraction of")
    print("  quantity still taken. The venue reports rebates as one daily lump")
    print("  with no conditionId, so no treatment's rebate is a realized number.")
    print("  'trading net' contains no rebate at all and is the honest line.")


if __name__ == "__main__":
    main()
