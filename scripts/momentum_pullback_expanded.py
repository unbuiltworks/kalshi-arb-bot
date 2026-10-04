"""EXPANDED OUT-OF-SAMPLE: three preregistered candidates, 805 windows.

WHAT CHANGED FROM THE 33-WINDOW RUN: the SAMPLE, and nothing else. The
strategy is imported from `momentum_pullback_backtest` and run unmodified --
same ask-ladder entry, same bid-ladder exit, same pullback-on-the-bid rule,
same exit-before-extend ordering. NO THRESHOLD IS SELECTED HERE. The three
candidates were fixed in advance:

    C1  momentum, NO base, 3c pullback
    C2  momentum, NO base, 5c pullback
    C3  liquidate-all 5c          (a SEPARATE strategy: the pullback sells
                                   the base as well, which is the other
                                   reading of "cash out once it dips")

Two-sided base is NOT mandatory: the base contributed essentially nothing in
the 33-window run (pair cost 1.0112), so C1 and C2 carry none. C3 keeps one
only because there has to be a base for "liquidate all" to mean anything.

THE SAMPLE. Zenodo 22084977 ships three contract lengths; the 33-window
result used BTC 15m alone, which is ONE coin of eight:

    updown_5m.parquet    471 windows, 8 coins
    updown_15m.parquet   265 windows, 8 coins   (33 of them the original BTC)
    updown_4h.parquet     69 windows, 8 coins

The honest reading of "out of sample" differs per slice, so they are never
pooled into one headline:
  * BTC 15m        -- the ORIGINAL sample. In-sample. Reported for continuity.
  * non-BTC 15m    -- same duration, unseen coins. The cleanest OOS test.
  * 5m and 4h      -- unseen duration AND unseen coins. A harder test, and a
                      cadence question too: 60s is the tested cadence and it
                      is NOT retuned per duration, so a 5m window gets ~5
                      decision points and a 4h window ~240.

THE CAPACITY CURVE is the commercial question, so it is dense where the
answer lives: $2 / $5 / $7.50 / $10 / $12.50 / $15 / $20 / $25 per extension.
The objective is REAL EXECUTABLE DOLLARS PER WINDOW, not percentage return,
and nothing is extrapolated past the largest size the recorded ladder
actually absorbed.

Accounting label: historical replay on third-party public data. No order was
sent. Nothing here is realized profit.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.momentum_pullback_backtest import (  # noqa: E402
    FEE_PER_DOLLAR_SENSITIVITY, load, run_window, summarise)

LABEL = "MOMENTUM_PULLBACK_EXPANDED_V1"
CADENCE_S = 60.0
SIZES = (2.0, 5.0, 7.5, 10.0, 12.5, 15.0, 20.0, 25.0)

#: FIXED BEFORE THE RUN. No fourth candidate is added and no threshold is
#: searched; adding either after seeing 805 windows is the hindsight the
#: 33-window design already refused.
CANDIDATES = {
    "C1_momentum_no_base_pb3c": dict(base_dollars=0.0, pullback=0.03,
                                     use_base=False),
    "C2_momentum_no_base_pb5c": dict(base_dollars=0.0, pullback=0.05,
                                     use_base=False),
    "C3_liquidate_all_pb5c": dict(base_dollars=1.0, pullback=0.05,
                                  use_base=True, liquidate_base_too=True),
}

FILES = {
    "5m": ("updown_5m.parquet",
           "166822b28caa160bb8321c548a8d2ef02b15ae0f06975dd05fced7f4ae118d11"),
    "15m": ("updown_15m.parquet",
            "d86191506e416026f460338b1b238c628b3fcee37f468e646b3a2256bd0be959"
            ),
    "4h": ("updown_4h.parquet",
           "b31a73e7e738b7db0d5a650a8795cd17bc79fcf8aeed74e25b4760eadc50e342"),
}

#: windows per day, for the $/day column. 15m -> 96, 5m -> 288, 4h -> 6.
PER_DAY = {"5m": 288, "15m": 96, "4h": 6}


def slice_rows(wins: Dict[str, Dict[str, Any]], slugs: List[str],
               **kw) -> List[Dict[str, Any]]:
    return [run_window(wins[s], cadence=CADENCE_S, **kw) for s in slugs]


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="directory of the parquets")
    ap.add_argument("--out", default=None)
    ap.add_argument("--durations", default="5m,15m,4h")
    a = ap.parse_args(argv)

    report: Dict[str, Any] = {
        "label": LABEL, "cadence_s": CADENCE_S,
        "sizes_usd": list(SIZES),
        "candidates_preregistered": sorted(CANDIDATES),
        "no_threshold_optimization": (
            "the three candidates were fixed before this run; no threshold "
            "is searched and no arm is added after seeing the sample"),
        "strategy_provenance": (
            "run_window imported UNMODIFIED from "
            "scripts/momentum_pullback_backtest.py; only the sample changed"),
        "execution": ("BUYS walk the ask ladder upward; SELLS walk the BID "
                      "ladder downward; the pullback high is measured on the "
                      "BID, which is what could actually be sold into"),
        "accounting_label": "historical replay on third-party public data",
        "durations": {},
    }

    for dur in a.durations.split(","):
        dur = dur.strip()
        if dur not in FILES:
            continue
        path = os.path.join(a.dir, FILES[dur][0])
        if not os.path.exists(path):
            report["durations"][dur] = {"readable": False, "path": path}
            continue
        wins = load(path, coin=None)
        # the coin is not carried on the window record, so recover it from
        # the slug, whose first segment IS the coin ("btc-updown-15m-...").
        btc = sorted(s for s in wins if s.split("-")[0] == "btc")
        other = sorted(set(wins) - set(btc))
        slices = {"btc_original_in_sample": btc,
                  "non_btc_out_of_sample": other,
                  "all_coins": sorted(wins)}
        out: Dict[str, Any] = {"readable": True,
                               "parquet_sha256": FILES[dur][1],
                               "windows_total": len(wins),
                               "windows_per_day": PER_DAY[dur],
                               "slice_sizes": {k: len(v)
                                               for k, v in slices.items()},
                               "by_candidate": {}}
        for cname, ckw in CANDIDATES.items():
            per_size: Dict[str, Any] = {}
            for size in SIZES:
                kw = dict(ckw, ext_dollars=size)
                entry: Dict[str, Any] = {}
                for sname, slugs in slices.items():
                    if not slugs:
                        continue
                    rows = slice_rows(wins, slugs, **kw)
                    sm = summarise(rows, "%s @ $%g" % (cname, size))
                    npw = sm["net_per_window"]
                    sm["net_per_day_estimate"] = (
                        None if npw is None
                        else round(npw * PER_DAY[dur], 2))
                    fnpw = sm["fees"]["net_per_window_with_sensitivity"]
                    sm["net_per_day_with_fee_sensitivity"] = (
                        None if fnpw is None
                        else round(fnpw * PER_DAY[dur], 2))
                    entry[sname] = sm
                per_size["$%g" % size] = entry
            out["by_candidate"][cname] = per_size
        report["durations"][dur] = out

    # ---- the capacity answer, pulled out so it is readable without
    # digging: for each duration/candidate/slice, the size with the best
    # EXECUTABLE DOLLARS per window, and whether fees survive it.
    cap: Dict[str, Any] = {}
    for dur, d in report["durations"].items():
        if not d.get("readable"):
            continue
        for cname, per_size in d["by_candidate"].items():
            for sname in ("non_btc_out_of_sample", "all_coins"):
                pts = []
                for sz in SIZES:
                    sm = per_size.get("$%g" % sz, {}).get(sname)
                    if not sm:
                        continue
                    pts.append({
                        "size_usd": sz,
                        "net_per_window": sm["net_per_window"],
                        "net_per_window_with_fee_sensitivity":
                            sm["fees"]["net_per_window_with_sensitivity"],
                        "peak_capital_median":
                            sm["peak_capital"]["median"],
                        "exit_slippage_pct_of_touch":
                            sm["exit_slippage_pct_of_touch"],
                        "exits_nothing_sellable":
                            sm["exits_nothing_sellable"],
                        "exits_partial": sm["exits_partial"],
                        "unsold_shares_total":
                            sm["unsold_shares_at_exit"]["total"],
                    })
                if not pts:
                    continue
                best = max(pts, key=lambda p: (p["net_per_window"] or -9e9))
                bestf = max(pts, key=lambda p: (
                    p["net_per_window_with_fee_sensitivity"] or -9e9))
                cap["%s/%s/%s" % (dur, cname, sname)] = {
                    "curve": pts,
                    "argmax_executable_dollars_per_window": best,
                    "argmax_after_2pct_fee_sensitivity": bestf,
                    "largest_size_with_measured_depth": max(
                        (p["size_usd"] for p in pts
                         if not p["unsold_shares_total"]), default=None),
                    "note": ("the objective is executable DOLLARS per "
                             "window, not percentage return; nothing is "
                             "extrapolated past the largest size the "
                             "recorded ladder absorbed"),
                }
    report["capacity"] = cap

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, indent=2, sort_keys=True,
                                default=str) + "\n")
    tot = sum(d.get("windows_total", 0)
              for d in report["durations"].values())
    print("%s  durations=%s  windows=%d  sizes=%d  candidates=%d"
          % (LABEL, ",".join(report["durations"]), tot, len(SIZES),
             len(CANDIDATES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
