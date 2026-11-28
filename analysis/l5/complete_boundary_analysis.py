"""What L5 actually does at COMPLETE, and what it would do in OUR state.

READ-ONLY. Reads the canonical sequence and writes one report. It opens no
order path, holds no credential and changes no controller.

THE QUESTION. Our corrected controller, holding NO 0.01 at average cost 0.14
and seeing YES executable at 0.91, classifies the YES rung COMPLETE -- a 1.05
pair. There is no pair-cost gate in the live path. Before adding one, this asks
what L5 itself did at comparable states.

DEFINITIONS, matched to analysis/l5/l5_completion_ceiling.json so the two can
be compared rather than merely coexist:

  relation    from the canonical residual BEFORE the fill: flat -> open,
              adding to the surplus side -> extend, adding to the deficit
              side -> complete.
  engine_avg  completing price + average cost (cum spend / cum qty) of the
              SURPLUS side before the fill. This is the definition our live
              controller's own state produces, which is why it is the one
              that decides whether our 1.05 is normal.

THE LIMIT THAT BOUNDS EVERY CLAIM HERE. This is a feed of EXECUTED TRADES. A
fill is not a quote. We observe what L5 DID, never what it declined to do, so
"L5 waits" can only ever be inferred from the absence of a fill -- which is
also what a missed quote looks like. Stated here rather than discovered later.
"""
from __future__ import annotations

import csv
import gzip
import json
import statistics
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

SRC = "analysis/l5/l5_canonical_sequence.csv.gz"
OUT = "analysis/l5/complete_boundary_report.json"

#: OUR state, in L5 terms
OUR_HELD_AVG = 0.14
OUR_COMPLETE_PRICE = 0.91
OUR_PAIR = 1.05

#: how wide a band counts as "a state like ours"
HELD_BAND = (0.08, 0.22)
COMPLETE_BAND = (0.82, 0.97)


def pct(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 4)


def summarize(xs: List[float]) -> Dict[str, Any]:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 4),
            "p10": pct(xs, 10), "p25": pct(xs, 25), "p50": pct(xs, 50),
            "p75": pct(xs, 75), "p90": pct(xs, 90), "p95": pct(xs, 95),
            "p99": pct(xs, 99), "max": round(max(xs), 4)}


def buckets(xs: List[float]) -> Dict[str, Any]:
    n = len(xs)
    if not n:
        return {}
    def f(lo, hi):
        c = sum(1 for x in xs if (lo is None or x > lo) and x <= hi)
        return {"n": c, "pct": round(100.0 * c / n, 2)}
    over = sum(1 for x in xs if x > 1.05)
    return {"<=0.96": f(None, 0.96), "<=0.98": f(None, 0.98),
            "<=1.00": f(None, 1.00),
            "1.00-1.02": f(1.00, 1.02), "1.02-1.05": f(1.02, 1.05),
            ">1.05": {"n": over, "pct": round(100.0 * over / n, 2)}}


def load() -> List[Dict[str, Any]]:
    with gzip.open(SRC, "rt", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    rows = load()
    by_window: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_window[r["window_id"]].append(r)

    completes: List[Dict[str, Any]] = []
    for wid, rs in by_window.items():
        rs.sort(key=lambda r: int(r["trade_seq"]))
        for i, r in enumerate(rs):
            if r["side"].upper() != "BUY":
                continue
            # state BEFORE this fill
            prev = rs[i - 1] if i else None
            up_q = float(prev["cum_up_qty"]) if prev else 0.0
            up_s = float(prev["cum_up_spend"]) if prev else 0.0
            dn_q = float(prev["cum_down_qty"]) if prev else 0.0
            dn_s = float(prev["cum_down_spend"]) if prev else 0.0
            res = up_q - dn_q
            out = r["outcome"].strip().lower()
            is_up = out in ("up", "yes")
            if abs(res) < 1.0:
                rel = "open"
            elif (res > 0) == is_up:
                rel = "extend"
            else:
                rel = "complete"
            if rel != "complete":
                continue
            # the SURPLUS side is the one we already hold
            sq, ss = (up_q, up_s) if res > 0 else (dn_q, dn_s)
            if sq <= 0:
                continue
            held_avg = ss / sq
            price = float(r["price"])
            completes.append({
                "window_id": wid, "asset": r["asset"],
                "dur": r["duration_minutes"],
                "lane": "%s-%sm" % (r["asset"], r["duration_minutes"]),
                "month": r["datetime_utc"][:7],
                "dt": r["datetime_utc"],
                "price": price, "held_avg": held_avg,
                "pair": price + held_avg,
                "qty": float(r["quantity"]),
                "residual_before": res,
                "tts": float(r["seconds_to_settlement"] or 0.0),
                "seq": int(r["trade_seq"]),
            })

    allp = [c["pair"] for c in completes]
    rep: Dict[str, Any] = {
        "label": "L5_COMPLETE_BOUNDARY_V1",
        "source": SRC,
        "rows_read": len(rows),
        "completes": len(completes),
        "span_utc": [min(c["dt"] for c in completes),
                     max(c["dt"] for c in completes)] if completes else None,
        "definition": ("engine_avg = completing price + average cost of the "
                       "surplus side before the fill; matches "
                       "l5_completion_ceiling.json"),
        "limit": ("EXECUTED TRADES ONLY. A fill is not a quote: we observe "
                  "what L5 DID, never what it declined to do."),
        "overall": summarize(allp),
        "overall_buckets": buckets(allp),
    }

    # ---- by lane
    rep["by_lane"] = {}
    for lane in sorted({c["lane"] for c in completes}):
        xs = [c["pair"] for c in completes if c["lane"] == lane]
        rep["by_lane"][lane] = {**summarize(xs), "buckets": buckets(xs)}

    # ---- chronological, month by month (OOS shape, not full-sample only)
    rep["by_month"] = {}
    for m in sorted({c["month"] for c in completes}):
        xs = [c["pair"] for c in completes if c["month"] == m]
        rep["by_month"][m] = {**summarize(xs), "buckets": buckets(xs)}

    # ---- time to settlement
    def tts_bucket(t: float) -> str:
        if t < 30:
            return "tts<30s"
        if t < 120:
            return "tts30-120s"
        if t < 300:
            return "tts2-5m"
        return "tts>5m"
    rep["by_tts"] = {}
    for b in ("tts<30s", "tts30-120s", "tts2-5m", "tts>5m"):
        xs = [c["pair"] for c in completes if tts_bucket(c["tts"]) == b]
        rep["by_tts"][b] = {**summarize(xs), "buckets": buckets(xs)}

    # ---- residual magnitude
    def res_bucket(r: float) -> str:
        a = abs(r)
        if a < 10:
            return "res<10"
        if a < 50:
            return "res10-50"
        if a < 200:
            return "res50-200"
        return "res>=200"
    rep["by_residual"] = {}
    for b in ("res<10", "res10-50", "res50-200", "res>=200"):
        xs = [c["pair"] for c in completes if res_bucket(c["residual_before"]) == b]
        rep["by_residual"][b] = {**summarize(xs), "buckets": buckets(xs)}

    # ---- held-side average cost band: does L5 pay up to complete a CHEAP leg?
    rep["by_held_avg_band"] = {}
    for lo, hi, name in ((0.0, 0.10, "held<0.10"), (0.10, 0.25, "held0.10-0.25"),
                         (0.25, 0.50, "held0.25-0.50"),
                         (0.50, 0.75, "held0.50-0.75"),
                         (0.75, 1.01, "held>=0.75")):
        xs = [c["pair"] for c in completes if lo <= c["held_avg"] < hi]
        px = [c["price"] for c in completes if lo <= c["held_avg"] < hi]
        rep["by_held_avg_band"][name] = {
            **summarize(xs), "buckets": buckets(xs),
            "completing_price_p50": pct(px, 50)}

    # ---- THE DECISION BOUNDARY: states shaped like ours
    like = [c for c in completes
            if HELD_BAND[0] <= c["held_avg"] <= HELD_BAND[1]
            and COMPLETE_BAND[0] <= c["price"] <= COMPLETE_BAND[1]]
    rep["states_like_ours"] = {
        "held_avg_band": list(HELD_BAND),
        "completing_price_band": list(COMPLETE_BAND),
        "n": len(like),
        "pct_of_all_completes": round(100.0 * len(like) / len(completes), 3)
        if completes else None,
        "pair": summarize([c["pair"] for c in like]),
        "buckets": buckets([c["pair"] for c in like]),
        "by_lane": {ln: len([c for c in like if c["lane"] == ln])
                    for ln in sorted({c["lane"] for c in like})},
        "by_month": {m: len([c for c in like if c["month"] == m])
                     for m in sorted({c["month"] for c in like})},
    }

    # ---- what happened AFTER an expensive complete, within the same window
    worked_down: Dict[str, Any] = {"expensive_completes": 0,
                                   "followed_by_more_fills": 0,
                                   "next_relation": defaultdict(int),
                                   "window_final_pair_better": 0,
                                   "window_final_pair_worse_or_equal": 0}
    final_pair: Dict[str, float] = {}
    for wid, rs in by_window.items():
        rs.sort(key=lambda r: int(r["trade_seq"]))
        last = rs[-1]
        try:
            fp = float(last["matched_pair_unit_cost"] or 0) or None
        except ValueError:
            fp = None
        if fp:
            final_pair[wid] = fp
    for c in completes:
        if c["pair"] <= 1.05:
            continue
        worked_down["expensive_completes"] += 1
        rs = by_window[c["window_id"]]
        later = [r for r in rs if int(r["trade_seq"]) > c["seq"]]
        if later:
            worked_down["followed_by_more_fills"] += 1
        fp = final_pair.get(c["window_id"])
        if fp is not None:
            if fp < c["pair"]:
                worked_down["window_final_pair_better"] += 1
            else:
                worked_down["window_final_pair_worse_or_equal"] += 1
    worked_down["next_relation"] = dict(worked_down["next_relation"])
    n = worked_down["expensive_completes"] or 1
    worked_down["followed_by_more_fills_pct"] = round(
        100.0 * worked_down["followed_by_more_fills"] / n, 2)
    worked_down["window_final_pair_better_pct"] = round(
        100.0 * worked_down["window_final_pair_better"] / n, 2)
    rep["after_expensive_complete"] = worked_down

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print(json.dumps({k: rep[k] for k in
                      ("completes", "span_utc", "overall", "overall_buckets")},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
