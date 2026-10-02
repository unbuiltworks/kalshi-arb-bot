#!/usr/bin/env python3
"""Descriptive characterization of 1730z vs 1745z. NOT a regime filter.

Two windows cannot support a regime rule, and none is built here: this computes
coarse, mechanistically motivated market-state descriptors that already exist in
the data, so that when further pristine windows arrive there is a vocabulary to
describe them in. Every number is a description of what happened, not a
threshold, and nothing here is fitted to the cancellation result.

Descriptors, all from data already collected:
  realized BTC volatility        stdev of 1 s log returns on the reference feed
  BTC displacement / range       net move and high-low, in bps
  PMUS price path / range        mid at open/close, high-low, total absolute path
  directional trade-flow persistence
                                 share of consecutive same-direction taker
                                 trades, and longest same-direction run
  sweeps                         count and median size of prints through the touch
  spread / depth                 median spread and median touch depth per side
  time distribution of fills     quartile shares of first-leg fills across the window
  settlement direction / strike  final mid and distance from 0.50, plus the
                                 strike where the tape records one
  inventory / residual trajectory peak and closing residual under the frozen
                                 baseline, and forced-exit share

No threshold search, no filter, no rule.
"""
from __future__ import annotations
import bisect, glob, json, os, statistics
from decimal import Decimal as D

OUT = os.environ.get("CHAR_OUT") or "analysis/l5/pmus_rewards/WINDOW_CHARACTER_1730Z_1745Z.json"
import sys
# INPUT SELECTION ONLY: which windows are described, never how. Defaults to the
# original pair so earlier output is reproducible.
WINDOWS = tuple(a for a in sys.argv[1:] if a.startswith("cpc-")) or (
    "cpc-btc-updown-15m-2026-10-02-1730z",
    "cpc-btc-updown-15m-2026-10-02-1745z")

import importlib.util
_a = importlib.util.spec_from_file_location("aud", "scripts/pmus_imbalance_mechanism_audit.py")
aud = importlib.util.module_from_spec(_a); _a.loader.exec_module(aud)


def ref_series():
    pts = []
    for p in sorted(glob.glob("data/btc_ref/btcref_*.jsonl")) + \
            sorted(glob.glob("data/btc_ref/btcref_harvested.jsonl")):
        for line in open(p):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("kind") != "ref_bbo" or not r.get("bid") or not r.get("ask"):
                continue
            pts.append((r["t_ns"] / 1e9, (float(r["bid"]) + float(r["ask"])) / 2))
    pts.sort()
    return [p[0] for p in pts], [p[1] for p in pts]


def btc_stats(ts, mids, lo, hi):
    i = bisect.bisect_left(ts, lo); j = bisect.bisect_right(ts, hi)
    seg = mids[i:j]
    if len(seg) < 10:
        return {"status": "INSUFFICIENT"}
    # one-second sampling for realized volatility
    sec = {}
    for t, m in zip(ts[i:j], seg):
        sec[int(t)] = m
    sv = [sec[k] for k in sorted(sec)]
    rets = [(b - a) / a for a, b in zip(sv, sv[1:]) if a]
    return {
        "n_ref_rows": len(seg),
        "open": round(seg[0], 2), "close": round(seg[-1], 2),
        "high": round(max(seg), 2), "low": round(min(seg), 2),
        "displacement_bps": round((seg[-1] - seg[0]) / seg[0] * 1e4, 2),
        "range_bps": round((max(seg) - min(seg)) / seg[0] * 1e4, 2),
        "realized_vol_1s_bps": round(statistics.stdev(rets) * 1e4, 3) if len(rets) > 2 else None,
        "n_1s_samples": len(sv),
    }


def main():
    rts, rmids = ref_series()
    tr, bk = aud.pou.load()
    sc, _ = aud.pou.scorable(tr, bk)
    res = {"note": ("DESCRIPTIVE ONLY. Two windows cannot support a regime filter "
                    "and none is constructed. No threshold is searched or fitted."),
           "windows": {}}
    for w in WINDOWS:
        rows = sc.get(w) or []
        if not rows:
            res["windows"][w] = {"status": "NO_SCORABLE_ROWS"}; continue
        lo, hi = rows[0]["t"], rows[-1]["t"]
        mids = [(float(x["bid_at_trade"]) + float(x["ask_at_trade"])) / 2 for x in rows]
        spreads = [float(x["ask_at_trade"]) - float(x["bid_at_trade"]) for x in rows]
        bq = [float(x["book"]["bid_qty"] or 0) for x in rows]
        aq = [float(x["book"]["ask_qty"] or 0) for x in rows]
        dirs = [x["dir"] for x in rows]
        same = sum(1 for a, b in zip(dirs, dirs[1:]) if a == b)
        run = best = 1
        for a, b in zip(dirs, dirs[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        sweeps = [float(x["qty"]) for x in rows
                  if float(x["px"]) < float(x["bid_at_trade"])
                  or float(x["px"]) > float(x["ask_at_trade"])]
        dead = aud.pou.window_end(w) - aud.DEADLINE_S
        lots = aud.lots_with_economics(rows, dead, D("0.97"))
        # inventory trajectory: open lots over time under the frozen baseline
        events = sorted([(l["t"], 1) for l in lots] +
                        [(l["exit_t"], -1) for l in lots if l["exit_t"]])
        cur = peak = 0
        for _t, dd in events:
            cur += dd; peak = max(peak, cur)
        qs = [lo + (hi - lo) * k / 4 for k in range(5)]
        fill_q = [sum(1 for l in lots if qs[k] <= l["t"] < qs[k + 1]) for k in range(4)]
        res["windows"][w] = {
            "span_s": round(hi - lo, 1), "scorable_trades": len(rows),
            "btc": btc_stats(rts, rmids, lo, hi),
            "pmus_price_path": {
                "mid_open": round(mids[0], 4), "mid_close": round(mids[-1], 4),
                "mid_high": round(max(mids), 4), "mid_low": round(min(mids), 4),
                "range": round(max(mids) - min(mids), 4),
                "total_abs_path": round(sum(abs(b - a) for a, b in zip(mids, mids[1:])), 4)},
            "flow_persistence": {
                "same_direction_share": round(same / max(1, len(dirs) - 1), 4),
                "longest_same_direction_run": best,
                "buy_share": round(sum(1 for d in dirs if d == "buy") / len(dirs), 4)},
            "sweeps": {"count": len(sweeps),
                       "share_of_trades": round(len(sweeps) / len(rows), 4),
                       "median_size": round(statistics.median(sweeps), 2) if sweeps else None,
                       "total_size": round(sum(sweeps), 2)},
            "spread_depth": {
                "median_spread": round(statistics.median(spreads), 4),
                "median_bid_depth": round(statistics.median(bq), 1),
                "median_ask_depth": round(statistics.median(aq), 1)},
            "fill_time_distribution_quartiles": fill_q,
            "settlement": {
                "final_mid": round(mids[-1], 4),
                "distance_from_0.50": round(abs(mids[-1] - 0.5), 4),
                "direction": "UP" if mids[-1] > 0.5 else "DOWN",
                "strike": "NOT_IN_TAPE -- the authenticated rows carry no strike field"},
            "inventory_trajectory_c97": {
                "first_legs": len(lots),
                "peak_concurrent_open_lots": peak,
                "closing_open_lots": cur,
                "forced_exit_share": round(sum(1 for l in lots if l["exit_kind"] in (None, "FORCED")) / len(lots), 4) if lots else None,
                "baseline_net": str(sum(l["net"] for l in lots))},
        }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=2, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
