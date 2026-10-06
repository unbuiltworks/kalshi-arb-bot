"""Permanent audit of the crossed-book defect in the Zenodo historical L2.

A crossed book is best_ask < best_bid on ONE token within ONE second. That
is impossible in a real order book, so wherever it appears the archive's
ask side is wrong at that instant -- almost certainly the event-driven
delta capture dropping an ask-side update while the bid moved past it.

Discovered 2026-10-06 during the running pair-cost confirmation run
(PAIRCOST_FINDINGS.md @ 1498a293), where it produced an arithmetically
impossible ask_sum < bid_sum in two windows.

This module only MEASURES. It does not repair the archive and it does not
re-run or alter any frozen study.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.stats import qs

WINDOW = {"15m": 900, "5m": 300}


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def audit(parquet: str, events: str, clen: str) -> Dict[str, Any]:
    ev = jl(events)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    books, wins = load(parquet, contract_length=clen, slugs=set(byw))
    ws = WINDOW[clen]

    per_window = {}
    tot_sec = tot_cross = 0
    depths = []
    for slug in sorted(byw):
        start = int(slug.rsplit("-", 1)[1])
        toks = {str(o).lower(): t for t, o in wins[slug]["tokens"].items()}
        n = 0
        cross_sec = 0
        per_tok = {}
        worst = None
        for side, tk in toks.items():
            bk = books.get(tk)
            c = 0
            rows = 0
            for s in range(ws):
                sn = bk.at(float(start + s), max_age_s=MAX_CARRY_S) if bk \
                    else None
                if not sn:
                    continue
                bb, ba = sn.get("best_bid"), sn.get("best_ask")
                if bb is None or ba is None:
                    continue
                rows += 1
                if float(ba) < float(bb):
                    c += 1
                    d = float(bb) - float(ba)
                    depths.append(d)
                    if worst is None or d > worst["inversion"]:
                        worst = {"secs_after_open": s, "outcome": side,
                                 "best_bid": float(bb), "best_ask": float(ba),
                                 "inversion": round(d, 4)}
            per_tok[side] = {"seconds_with_both_quotes": rows,
                             "crossed_seconds": c,
                             "crossed_share": (round(c / rows, 4) if rows
                                               else None)}
        # a second is crossed if EITHER token is crossed
        for s in range(ws):
            ok = True
            bad = False
            for tk in toks.values():
                bk = books.get(tk)
                sn = bk.at(float(start + s), max_age_s=MAX_CARRY_S) if bk \
                    else None
                if not sn or sn.get("best_bid") is None \
                        or sn.get("best_ask") is None:
                    ok = False
                    break
                if float(sn["best_ask"]) < float(sn["best_bid"]):
                    bad = True
            if ok:
                n += 1
                cross_sec += bad
        tot_sec += n
        tot_cross += cross_sec
        per_window[slug] = {
            "seconds_with_full_quotes": n,
            "crossed_seconds_either_token": cross_sec,
            "crossed_share": round(cross_sec / n, 4) if n else None,
            "per_token": per_tok,
            "worst_inversion_example": worst,
        }
    return {
        "contract_length": clen,
        "n_windows": len(per_window),
        "windows_with_any_crossing": sum(
            1 for v in per_window.values()
            if (v["crossed_seconds_either_token"] or 0) > 0),
        "pooled_seconds_with_full_quotes": tot_sec,
        "pooled_crossed_seconds": tot_cross,
        "pooled_crossed_share": (round(tot_cross / tot_sec, 4) if tot_sec
                                 else None),
        "inversion_magnitude_bid_minus_ask": qs(depths),
        "per_window": per_window,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--parquet15", required=True)
    ap.add_argument("--events5")
    ap.add_argument("--parquet5")
    ap.add_argument("--outdir", default="analysis/l5/data_quality")
    a = ap.parse_args(argv)
    out = {
        "defect": "CROSSED_BOOK_best_ask_below_best_bid",
        "discovered": "2026-10-06, during PAIRCOST confirmation @ 1498a293",
        "cause_hypothesis": ("event-driven delta capture dropped an ask-side "
                             "update while the bid moved past it, leaving a "
                             "stale low ask"),
        "scope": "ASK side only; the bid side shows no inversion by construction",
        "this_module_only_measures": True,
        "BTC15m": audit(a.parquet15, a.events15, "15m"),
    }
    if a.events5 and a.parquet5:
        out["BTC5m"] = audit(a.parquet5, a.events5, "5m")
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "CROSSED_BOOKS.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written CROSSED_BOOKS.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
