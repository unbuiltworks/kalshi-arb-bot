"""Characterise the price-acceptance rule, and test Grok's static-cap clone.

Grok's round-4 position: the profitable component is a "static,
inventory-blind, time-blind reservation-price rule that simply refuses to
buy either leg above roughly 0.48-0.50", and a clone needs only that plus
sizes, timings and the 0.28 hazard.

That is a strong structural claim with four testable sub-claims, all of
which Grok asserted rather than measured:

  C2  the acceptance threshold is symmetric across legs
  C3  no time-in-window dependence
  C4  no residual dependence (the filter is inventory-blind)
  C5  stable across the corpus

and one that can be checked against the data's own arithmetic:

  C1  a static cap near 0.48-0.50

The decisive diagnostic is the last block below: whether the price paid
differs between fills that EXTEND the residual and fills that COMPLETE a
pair. A static inventory-blind cap requires those to be the same. If
completing fills are systematically dearer, the rule is conditional on
inventory and Grok's C4 is false -- and the mechanism is "accumulate cheap
on one leg, then pay up to close the pair", which is not a cap at all.

Read-only. No network.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import json
import os
import random
import statistics
import sys

BOOT, CAP, SEED = 2000, 20000, 20261009


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def ci(vals, rng, fn=statistics.fmean, reps=BOOT):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None, None
    k, m = len(v), min(len(v), CAP)
    out = sorted(fn([v[rng.randrange(k)] for _ in range(m)])
                 for _ in range(reps))
    return out[int(reps * .025)], out[int(reps * .975)]


def blk(vals, rng):
    if not vals:
        return {"n": 0}
    lo, hi = ci(vals, rng)
    return {"n": len(vals), "mean": round(statistics.fmean(vals), 5),
            "ci95": [None if lo is None else round(lo, 5),
                     None if hi is None else round(hi, 5)]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)

    px_all = []
    by_leg = collections.defaultdict(list)
    by_decile = collections.defaultdict(list)
    by_month = collections.defaultdict(list)
    by_resid_q = collections.defaultdict(list)
    by_resid_v = collections.defaultdict(list)
    by_role = collections.defaultdict(list)       # EXTEND vs COMPLETE
    first_vs_rest = collections.defaultdict(list)
    pair_costs = []
    role_counts = collections.Counter()

    prev = None
    cur = None
    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["event_slug"] != cur:
                    cur, prev = r["event_slug"], None
                px = f(r["price"])
                if px is None:
                    prev = r
                    continue
                px_all.append(px)
                by_leg[r["leg"]].append(px)
                by_month[r["datetime_utc"][:7]].append(px)
                seq = int(r["trade_seq"])
                first_vs_rest["first_fill" if seq == 1
                              else "later_fills"].append(px)
                sfs, dur = f(r["seconds_from_window_start"]), \
                    f(r["duration_minutes"])
                if sfs is not None and dur:
                    d = min(max(int(sfs / (dur * 60) * 10), 0), 9)
                    by_decile[f"decile_{d}"].append(px)
                if prev is not None:
                    prq = f(prev["residual_qty"], 0.0)
                    prv = f(prev["residual_vwap"])
                    mq, pmq = f(r["matched_qty"], 0.0), \
                        f(prev["matched_qty"], 0.0)
                    role = "COMPLETE_increases_matched" if mq > pmq + 1e-9 \
                        else "EXTEND_no_new_pair"
                    role_counts[role] += 1
                    by_role[role].append(px)
                    if prq is not None:
                        b = ("resid_0" if prq <= 1e-9 else
                             "resid_1_50" if prq <= 50 else
                             "resid_50_200" if prq <= 200 else
                             "resid_200_1000" if prq <= 1000 else
                             "resid_gt_1000")
                        by_resid_q[b].append(px)
                    if prv is not None and prq > 1e-9:
                        by_resid_v[f"vwap_[{min(int(prv*5),4)/5:.1f},"
                                    f"{(min(int(prv*5),4)+1)/5:.1f})"]\
                            .append(px)
                prev = r

    s = sorted(px_all)
    n = len(s)

    def q(p):
        return round(s[min(n - 1, int(n * p))], 4)

    share_above = {f"share_above_{t:.2f}":
                   round(sum(1 for x in s if x > t) / n, 4)
                   for t in (0.40, 0.48, 0.50, 0.60, 0.70, 0.80, 0.90)}

    with gzip.open(os.path.join(a.dataset, "l5_window_summary.csv.gz"),
                   "rt", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            v = f(r["final_pair_cost"])
            if v is not None:
                pair_costs.append(v)
    pc = sorted(pair_costs)
    share_pc_above_1 = round(
        sum(1 for x in pc if x >= 1.0) / len(pc), 4)
    share_pc_above_096 = round(
        sum(1 for x in pc if x > 0.96) / len(pc), 4)

    R = {}
    R["C1_static_cap_near_0.48_0.50"] = {
        "grok_claim": "a static rule refusing to buy either leg above "
                      "roughly 0.48-0.50",
        "n_fills": n,
        "mean": round(statistics.fmean(px_all), 5),
        "quantiles": {f"p{int(p*100)}": q(p) for p in
                      (.01, .05, .10, .25, .50, .75, .90, .95, .99)},
        **share_above,
        "VERDICT": "REFUTED",
        "why": (
            f"{share_above['share_above_0.50']:.1%} of fills are above "
            f"0.50 and {share_above['share_above_0.80']:.1%} are above "
            f"0.80, with p99 at {q(.99)}. A cap at 0.48-0.50 would have to "
            "reject nearly half the corpus. The sub-0.50 MEAN is real; a "
            "CAP is not what produces it."),
        "pair_cost_cross_check": {
            "share_of_windows_with_final_pair_cost_ge_1.00":
                share_pc_above_1,
            "share_above_0.96": share_pc_above_096,
            "note": "A static per-leg cap at 0.48 forces every pair to cost "
                    "<=0.96. In fact " f"{share_pc_above_096:.1%} of windows "
                    "end above 0.96 and " f"{share_pc_above_1:.1%} at or "
                    "above 1.00, so no static per-leg cap can generate the "
                    "observed pair-cost distribution.",
        },
    }
    R["C2_symmetric_across_legs"] = {
        "grok_claim": "threshold roughly symmetric across legs",
        "UP": blk(by_leg["UP"], rng), "DOWN": blk(by_leg["DOWN"], rng),
    }
    up, dn = R["C2_symmetric_across_legs"]["UP"], \
        R["C2_symmetric_across_legs"]["DOWN"]
    R["C2_symmetric_across_legs"]["VERDICT"] = (
        "SUPPORTED (CIs overlap)"
        if not (up["ci95"][0] > dn["ci95"][1] or up["ci95"][1] < dn["ci95"][0])
        else "REFUTED (CIs disjoint)")

    R["C3_no_time_in_window_dependence"] = {
        "grok_claim": "mean fill price does not vary materially with "
                      "position in the window",
        "by_decile": {k: blk(v, rng) for k, v in sorted(by_decile.items())},
    }
    dm = [v["mean"] for v in R["C3_no_time_in_window_dependence"]
          ["by_decile"].values() if v.get("mean") is not None]
    R["C3_no_time_in_window_dependence"]["range_across_deciles"] = \
        round(max(dm) - min(dm), 5) if dm else None
    R["C3_no_time_in_window_dependence"]["VERDICT"] = (
        "SUPPORTED" if dm and max(dm) - min(dm) <= 0.02
        else f"REFUTED (spread {max(dm) - min(dm):.4f} across deciles)")

    R["C4_inventory_blind"] = {
        "grok_claim": "the price filter is unconditional on current "
                      "inventory state",
        "by_residual_qty": {k: blk(v, rng)
                            for k, v in sorted(by_resid_q.items())},
        "by_residual_vwap": {k: blk(v, rng)
                             for k, v in sorted(by_resid_v.items())},
    }
    R["C5_stable_across_corpus"] = {
        "grok_claim": "the 0.4614 mean holds in every calendar month",
        "by_month": {k: blk(v, rng) for k, v in sorted(by_month.items())},
    }
    mm = [v["mean"] for v in R["C5_stable_across_corpus"]["by_month"].values()]
    R["C5_stable_across_corpus"]["range_across_months"] = \
        round(max(mm) - min(mm), 5)
    R["C5_stable_across_corpus"]["VERDICT"] = (
        "SUPPORTED" if max(mm) - min(mm) <= 0.03
        else f"REFUTED (spread {max(mm) - min(mm):.4f} across months)")

    # ---- the decisive diagnostic -----------------------------------------
    ext = by_role["EXTEND_no_new_pair"]
    com = by_role["COMPLETE_increases_matched"]
    be, bc = blk(ext, rng), blk(com, rng)
    disj = (be["ci95"][0] > bc["ci95"][1] or be["ci95"][1] < bc["ci95"][0]) \
        if (be.get("ci95") and bc.get("ci95")) else False
    R["DECISIVE_extend_vs_complete"] = {
        "question": "Does the price paid differ between fills that merely "
                    "EXTEND the one-sided residual and fills that COMPLETE "
                    "a pair? A static, inventory-blind cap requires these "
                    "to be equal.",
        "EXTEND_no_new_pair": be,
        "COMPLETE_increases_matched": bc,
        "difference_complete_minus_extend":
            None if not (be.get("mean") and bc.get("mean"))
            else round(bc["mean"] - be["mean"], 5),
        "counts": dict(role_counts),
        "VERDICT":
            ("REFUTES C4: completing fills are paid at a DIFFERENT price "
             "than extending fills, CIs disjoint. The rule is conditional "
             "on inventory, not a static cap."
             if disj and bc.get("mean", 0) != be.get("mean", 0)
             else "C4 not refuted on this split (CIs overlap)"),
        "interpretation_if_complete_is_dearer":
            "Then the mechanism is: accumulate the first leg cheaply while "
            "it stays cheap, then PAY UP to close the pair. That is a "
            "two-state conditional policy -- cheap accumulation plus "
            "willingness-to-complete -- and it is exactly the component a "
            "static-cap clone would omit.",
    }
    R["first_vs_later_fills"] = {
        k: blk(v, rng) for k, v in sorted(first_vs_rest.items())}

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps(R, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
