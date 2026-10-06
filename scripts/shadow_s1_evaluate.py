#!/usr/bin/env python3
"""Shadow S1 first-cohort evaluation. READ-ONLY over the committed tapes.

The cohort was PREREGISTERED at 25 COMPLETE independent markets. Run 4
produced 32 markets passing every measurement gate, of which 25 are
COMPLETE. The cohort is the FIRST 25 COMPLETE markets in chronological
order; later markets are held out, not pooled, so the evaluation set is
the one that was preregistered rather than the one that happens to look
best.

Nothing here can change S1: it imports no collector code and writes no
parameter. Uncertain fills are reported SEPARATELY and never enter
primary economics.

Markouts are computed from the mid of the SAME outcome's book at +1/+5/
+10/+30/+60s after the fill, signed as a buyer: a positive markout means
the mid rose after we bought. A horizon that runs past the market's last
observation is NOT extrapolated -- it is reported as missing coverage.
"""
from __future__ import annotations

import collections
import glob
import gzip
import json
import statistics
import sys

QTY = 23.61
HORIZONS = (1, 5, 10, 30, 60)


def rows():
    for p in sorted(glob.glob("data/l5/shadow/shadow_*.jsonl.gz")):
        with gzip.open(p, "rt", errors="ignore") as fh:
            for line in fh:
                try:
                    yield p, json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue


def load():
    snaps = collections.defaultdict(list)
    fills = collections.defaultdict(list)
    unc = collections.defaultdict(list)
    term = {}
    order = []
    for _p, r in rows():
        k = r.get("kind")
        mk = r.get("market")
        if k == "shadow_snapshot":
            snaps[mk].append(r)
        elif k == "shadow_fill":
            fills[mk].append(r)
        elif k == "shadow_uncertain":
            unc[mk].append(r)
        elif k == "market_terminal" and mk:
            # A market can appear in two segments; keep the LAST terminal.
            if mk not in term:
                order.append(mk)
            term[mk] = r
    return snaps, fills, unc, term, order


def mid_at(ser, oc, t):
    """Mid of `oc`'s book at the first observation at or after t."""
    lo, hi = 0, len(ser)
    while lo < hi:
        m = (lo + hi) // 2
        if ser[m]["t"] < t:
            lo = m + 1
        else:
            hi = m
    if lo >= len(ser):
        return None
    s = ser[lo]
    b, a = s.get(oc + "_bid"), s.get(oc + "_ask")
    if b is None or a is None:
        return None
    return (b + a) / 2.0


def main() -> int:
    snaps, fills, unc, term, order = load()
    # The preregistered cohort: first 25 COMPLETE markets, chronological.
    comp = [mk for mk in order
            if term[mk].get("status") == "COMPLETE"
            and len({f["outcome"] for f in fills.get(mk, [])}) >= 0]
    # Exclude markets where the policy never quoted (no bid on either
    # outcome, so no order could be placed) -- they measure the venue's
    # empty book, not S1's fill opportunity.
    quoted = []
    for mk in comp:
        ser = snaps.get(mk, [])
        ocs = sorted({k[:-4] for s in ser for k in s if k.endswith("_bid")})
        if len(ocs) < 2:
            continue
        if not any(all(s.get(o + "_bid") is not None for o in ocs)
                   for s in ser):
            continue
        quoted.append(mk)
    cohort = quoted[:25]
    per = []
    mo = {h: [] for h in HORIZONS}
    drift = {h: [] for h in HORIZONS}
    mo_missing = {h: 0 for h in HORIZONS}
    capture = []
    for mk in cohort:
        ser = sorted(snaps.get(mk, []), key=lambda s: s["t"])
        fl = sorted(fills.get(mk, []), key=lambda f: f["t"])
        ocs = sorted({k[:-4] for s in ser for k in s if k.endswith("_bid")})
        t_last = ser[-1]["t"] if ser else None
        by_oc = collections.Counter(f["outcome"] for f in fl)
        inv = {o: by_oc[o] * QTY for o in ocs}
        matched = min(inv.values()) if len(inv) == 2 else 0.0
        resid = abs(inv[ocs[0]] - inv[ocs[1]]) if len(ocs) == 2 else 0.0
        notional = sum(f["order"]["price"] * f["order"]["quantity"]
                       for f in fl)
        # Pair cost: the two sides' average fill prices summed. Only
        # defined when BOTH outcomes filled.
        px = {o: [f["order"]["price"] for f in fl if f["outcome"] == o]
              for o in ocs}
        pair = (statistics.mean(px[ocs[0]]) + statistics.mean(px[ocs[1]])
                if all(px[o] for o in ocs) else None)
        gaps = [b["t"] - a["t"] for a, b in zip(fl, fl[1:])]
        for f in fl:
            for h in HORIZONS:
                tgt = f["t"] + h
                if t_last is None or tgt > t_last:
                    mo_missing[h] += 1
                    continue
                m1 = mid_at(ser, f["outcome"], f["t"])
                m2 = mid_at(ser, f["outcome"], tgt)
                if m1 is None or m2 is None:
                    mo_missing[h] += 1
                    continue
                # DECOMPOSITION. A passive bid fill starts with the
                # half-spread in its favour, so a raw markout mixes
                # spread capture with adverse drift. Report both:
                #   markout = (mid_at_fill - price) + (mid_later - mid_at_fill)
                #           = spread capture   + adverse drift
                mo[h].append(m2 - f["order"]["price"])
                drift[h].append(m2 - m1)
                if h == HORIZONS[0]:
                    capture.append(m1 - f["order"]["price"])
        per.append({
            "market": mk,
            "observations": len(ser),
            "span_s": round(ser[-1]["t"] - ser[0]["t"], 1) if ser else None,
            "evidenced_fills": len(fl),
            "uncertain_fills_excluded": len(unc.get(mk, [])),
            "fills_by_outcome": dict(by_oc),
            "both_outcomes_filled": len(by_oc) == 2,
            "inventory_shares": inv,
            "matched_shares": round(matched, 2),
            "terminal_residual_shares": round(resid, 2),
            "matched_fraction": (round(2 * matched / sum(inv.values()), 4)
                                 if sum(inv.values()) else None),
            "pair_cost": round(pair, 4) if pair is not None else None,
            "notional_usd": round(notional, 2),
            "median_fill_gap_s": (round(statistics.median(gaps), 1)
                                  if gaps else None),
        })
    tot_ev = sum(p["evidenced_fills"] for p in per)
    tot_un = sum(p["uncertain_fills_excluded"] for p in per)
    both = sum(1 for p in per if p["both_outcomes_filled"])
    pairs = [p["pair_cost"] for p in per if p["pair_cost"] is not None]
    out = {
        "cohort": {
            "preregistered_n": 25,
            "markets": [p["market"] for p in per],
            "n": len(per),
            "held_out_not_pooled": len(quoted) - len(cohort),
        },
        "fill_opportunity": {
            "evidenced_fills_total": tot_ev,
            "evidenced_fills_per_market_mean": round(tot_ev / len(per), 2),
            "evidenced_fills_per_market_median": statistics.median(
                [p["evidenced_fills"] for p in per]),
            "markets_with_zero_evidenced_fills": sum(
                1 for p in per if p["evidenced_fills"] == 0),
            "uncertain_fills_total_EXCLUDED": tot_un,
            "uncertain_to_evidenced_ratio": (round(tot_un / tot_ev, 2)
                                             if tot_ev else None),
            "median_fill_gap_s": statistics.median(
                [p["median_fill_gap_s"] for p in per
                 if p["median_fill_gap_s"] is not None]),
        },
        "inventory_formation": {
            "both_outcome_fill_markets": both,
            "both_outcome_fill_fraction": round(both / len(per), 4),
            "matched_fraction_median": statistics.median(
                [p["matched_fraction"] for p in per
                 if p["matched_fraction"] is not None]),
            "terminal_residual_shares_median": statistics.median(
                [p["terminal_residual_shares"] for p in per]),
            "terminal_residual_shares_max": max(
                p["terminal_residual_shares"] for p in per),
            "residual_cap_shares": 80,
            "pair_cost_median": (round(statistics.median(pairs), 4)
                                 if pairs else None),
            "pair_cost_n": len(pairs),
            "pair_cost_below_1": sum(1 for p in pairs if p < 1.0),
        },
        "capital": {
            "hypothetical_peak_notional_usd_per_market_max": round(
                max(p["notional_usd"] for p in per), 2),
            "hypothetical_notional_usd_median": round(statistics.median(
                [p["notional_usd"] for p in per]), 2),
            "note": ("gross notional of evidenced hypothetical fills. NOT "
                     "capital deployed, NOT P&L, and NOT realized: no "
                     "order was ever submitted and no capital was at "
                     "risk."),
        },
        "markouts_signed_as_buyer": {
            "definition": ("markout = mid(t+h) - our fill price, signed as "
                           "a buyer: positive means the mid rose after we "
                           "bought. A horizon past the market's last "
                           "observation is reported as missing coverage, "
                           "never extrapolated."),
            "spread_capture_at_fill": {
                "n": len(capture),
                "mean": (round(statistics.mean(capture), 5)
                         if capture else None),
                "median": (round(statistics.median(capture), 5)
                           if capture else None),
                "note": ("mid at the fill minus our price. This is the "
                         "mechanical edge a passive bid starts with; it "
                         "is NOT profit and is not realized."),
            },
            "horizons": {
                ("plus_%ds" % h): {
                    "n": len(mo[h]),
                    "missing_coverage": mo_missing[h],
                    "markout_mean": (round(statistics.mean(mo[h]), 5)
                                     if mo[h] else None),
                    "markout_median": (round(statistics.median(mo[h]), 5)
                                       if mo[h] else None),
                    "adverse_drift_mean": (round(statistics.mean(drift[h]), 5)
                                           if drift[h] else None),
                    "adverse_drift_median": (
                        round(statistics.median(drift[h]), 5)
                        if drift[h] else None),
                } for h in HORIZONS
            },
        },
        "per_market": per,
        "discipline": {
            "candidate_fingerprint": (
                "441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333"
                "fe24b6fe"),
            "labels": ("SHADOW ONLY. Not submitted, not filled, not "
                       "settled, not realized. Uncertain fills are "
                       "reported separately and excluded from every "
                       "aggregate above."),
            "no_parameter_changed_after_seeing_results": True,
        },
    }
    with open("analysis/l5/shadow/S1_COHORT1_EVALUATION.json", "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    for k in ("fill_opportunity", "inventory_formation",
              "markouts_signed_as_buyer"):
        print(k.upper())
        print(json.dumps(out[k], indent=2)[:1600])
    return 0


if __name__ == "__main__":
    sys.exit(main())
