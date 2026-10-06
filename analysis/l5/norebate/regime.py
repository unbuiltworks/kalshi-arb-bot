"""Regime comparison around the introduction of maker rebates.

Frozen protocol: PREREGISTRATION_NOREBATE.md @ 3fb8fccb.

Deleting rebate payments from an incentive-era strategy is NOT equivalent
to observing that strategy in a world without incentives. This endpoint
decides whether Q1's E3 figure may be read as the no-incentive
counterfactual at all.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics as st
from collections import defaultdict
from typing import Any, Dict, List

from analysis.l5.microstructure.stats import cliffs_delta, qs
from analysis.l5.norebate.q1 import FIRST_REBATE, era, load

OUT = "analysis/l5/norebate/REGIME.json"


def main() -> int:
    rows = load("data/l5/raw/activity.jsonl")
    tr = [r for r in rows if r["type"] == "TRADE"]
    per_day: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "notional": 0.0, "prices": [], "sizes": [],
                 "shares": defaultdict(float), "cost": defaultdict(float),
                 "slugs": set(), "fees": 0.0})
    for r in tr:
        d = dt.datetime.utcfromtimestamp(r["timestamp"]).strftime("%Y-%m-%d")
        p = per_day[d]
        px, sz, u = float(r["price"]), float(r["size"]), float(r["usdcSize"])
        p["n"] += 1
        p["notional"] += u
        p["prices"].append(px)
        p["sizes"].append(sz)
        p["slugs"].add(r.get("eventSlug") or "")
        p["fees"] += max(0.0, u - px * sz)
        if r.get("side") == "BUY":
            p["shares"][str(r.get("outcome") or "").lower()] += sz
            p["cost"][str(r.get("outcome") or "").lower()] += px * sz

    days = []
    for d, p in sorted(per_day.items()):
        ks = sorted(p["shares"])
        tot = sum(p["shares"].values())
        matched = (min(p["shares"][ks[0]], p["shares"][ks[1]])
                   if len(ks) >= 2 else 0.0)
        pc = sum(p["cost"][k] / p["shares"][k] for k in ks[:2]
                 if p["shares"][k] > 0)
        ts = dt.datetime.strptime(d, "%Y-%m-%d").timestamp()
        days.append({
            "day": d,
            "era": ("E3_INCENTIVE_ERA"
                    if dt.datetime.strptime(d, "%Y-%m-%d") >= FIRST_REBATE
                    else "E1_PRE_REBATE"),
            "trades": p["n"],
            "notional_usd": round(p["notional"], 2),
            "markets": len(p["slugs"]),
            "median_trade_size": round(st.median(p["sizes"]), 4),
            "median_trade_price": round(st.median(p["prices"]), 4),
            "matched_shares": round(matched, 2),
            "matched_fraction": (round(2 * matched / tot, 4) if tot else None),
            "residual_shares": round(abs(tot - 2 * matched), 2),
            "pair_cost": (round(pc, 4) if pc else None),
            "fees_usd": round(p["fees"], 2),
        })

    pre = [x for x in days if x["era"] == "E1_PRE_REBATE"]
    post = [x for x in days if x["era"] == "E3_INCENTIVE_ERA"]

    out: Dict[str, Any] = {
        "protocol": "PREREGISTRATION_NOREBATE.md @ 3fb8fccb",
        "n_pre_days": len(pre), "n_post_days": len(post),
        "POWER_LIMIT": (
            "%d pre-rebate days vs %d incentive-era days. Cliff's delta on "
            "per-day distributions at this n is low-powered, and the two "
            "groups are separated by the May-July coverage HOLE, so any "
            "difference is confounded with a 4-month gap. Reported with "
            "that limit on every line." % (len(pre), len(post))),
        "per_day": days,
        "metrics": {},
    }
    for k in ("trades", "notional_usd", "markets", "median_trade_size",
              "median_trade_price", "matched_shares", "matched_fraction",
              "residual_shares", "pair_cost", "fees_usd"):
        a = [x[k] for x in post if x.get(k) is not None]
        b = [x[k] for x in pre if x.get(k) is not None]
        if min(len(a), len(b)) < 2:
            out["metrics"][k] = {"VERDICT": "INSUFFICIENT"}
            continue
        ma, mb = st.median(a), st.median(b)
        d = cliffs_delta(a, b)
        out["metrics"][k] = {
            "pre_median": round(mb, 4), "post_median": round(ma, 4),
            "ratio_post_over_pre": (round(ma / mb, 3) if mb else None),
            "cliffs_delta": d.get("delta"),
            "magnitude": d.get("magnitude"),
            "MATERIAL": bool(
                (d.get("delta") is not None and abs(d["delta"]) >= 0.33)
                or (mb and (ma / mb >= 2 or ma / mb <= 0.5))),
        }
    mat = [k for k, v in out["metrics"].items() if v.get("MATERIAL")]
    out["MATERIAL_CHANGE_METRICS"] = mat
    out["MATERIAL_REGIME_CHANGE"] = bool(mat)
    out["CONSEQUENCE"] = (
        "A material regime change means Q1's E3 rebates=$0 figure may NOT "
        "be read as the no-incentive counterfactual: the behaviour measured "
        "there was produced UNDER incentives."
        if mat else
        "No material regime change detected at this power; the E3 figure "
        "may be read as the incentive-era behaviour with credits removed, "
        "still subject to the coverage hole.")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, default=str)
    print("written REGIME.json: material=%s %s" % (bool(mat), mat))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
