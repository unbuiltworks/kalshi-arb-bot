"""Test Grok's seven numbered predictions against the full corpus.

Grok produced falsifiable predictions with directions and thresholds. This
evaluates each one on all 760,817 fills / 17,046 windows, reporting the
point estimate, n, a capped percentile bootstrap CI, and a verdict against
the threshold Grok actually stated.

Two of the predictions cannot be tested as literally written, and this
says so rather than quietly testing something easier:

  P1 is CIRCULAR as stated. It conditions on `price_B`, the price of the
     opposite leg, but the only opposite-leg price in this data is the
     price of a fill that happened. So "did it switch to B, given B's
     price was attractive" can only be evaluated on cases where it DID
     switch to B. The condition selects on the outcome. We therefore
     report (a) the literal version, labelled as biased and not a test,
     and (b) non-circular versions that condition only on state known
     before the next fill.

  P4 is a distributional claim with no threshold. We report the mode sets
     and a quantitative overlap instead of inventing a cutoff.

Read-only. No network.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import json
import math
import os
import random
import statistics
import sys

BOOT = 2000
BOOT_CAP = 20000
SEED = 20261009
EARLY = {"2026-03", "2026-04", "2026-05"}
LATE = {"2026-08"}
PREFERRED = {5, 10, 20, 30, 50, 80}


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def boot_ci(vals, fn, rng, reps=BOOT):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None, None
    k, m = len(v), min(len(v), BOOT_CAP)
    out = []
    for _ in range(reps):
        out.append(fn([v[rng.randrange(k)] for _ in range(m)]))
    out.sort()
    return out[int(reps * 0.025)], out[int(reps * 0.975)]


def rate(v):
    return sum(v) / len(v) if v else float("nan")


def verdict(point, lo, hi, op, thresh):
    """Direction-aware verdict that refuses to call a CI spanning the
    threshold."""
    if point is None or lo is None:
        return "INDETERMINATE (insufficient n)"
    if op == ">":
        if lo > thresh:
            return "SUPPORTED"
        if hi <= thresh:
            return "NOT_SUPPORTED"
    else:
        if hi < thresh:
            return "SUPPORTED"
        if lo >= thresh:
            return "NOT_SUPPORTED"
    return "INDETERMINATE (CI spans the threshold)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)
    R = {}

    # ---------------- single pass over fills, grouped by window ----------
    gaps = []
    gap_zero = []
    switch_given_resid = []
    literal_p1 = []                       # Grok's circular version
    switch_by_resid_vwap = collections.defaultdict(list)
    size_by_regime = {"early": collections.Counter(),
                      "late": collections.Counter()}
    size_pref_share = {"early": [], "late": []}
    post_expiry = []
    pc_delta = {"pre": [], "post": []}
    resid_side_fills = collections.defaultdict(lambda: [0, 0])
    n_fills = 0

    cur = None
    prev = None
    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                n_fills += 1
                slug = r["event_slug"]
                if slug != cur:
                    cur, prev = slug, None

                month = r["datetime_utc"][:7]
                reg = "early" if month in EARLY else (
                    "late" if month in LATE else None)
                sz = f(r["size"])
                if reg and sz is not None:
                    size_by_regime[reg][sz] += 1
                    size_pref_share[reg].append(
                        1 if (sz in PREFERRED or
                              (sz == int(sz) and int(sz) in PREFERRED)) else 0)

                sts = f(r["seconds_to_settlement"])
                is_post = sts is not None and sts < 0
                post_expiry.append(1 if is_post else 0)

                pc = f(r["effective_pair_cost"])
                leg = r["leg"]

                # count fills on the window's FINAL residual side later;
                # for now tally per-leg counts
                resid_side_fills[slug][0 if leg == "UP" else 1] += 1

                if prev is not None:
                    g = f(r["seconds_since_prev_fill"])
                    if g is not None:
                        gaps.append(g)
                        gap_zero.append(1 if g == 0 else 0)
                    switched = int(r["leg_switched_from_prev"] or 0)
                    pr_q = f(prev["residual_qty"], 0.0)
                    pr_v = f(prev["residual_vwap"])
                    pr_s = (prev["residual_side"] or "").upper()
                    if pr_q and pr_q > 0 and pr_s in ("UP", "DOWN"):
                        switch_given_resid.append(switched)
                        if pr_v is not None:
                            b = min(int(pr_v * 10), 9)
                            switch_by_resid_vwap[f"[{b/10:.1f},{(b+1)/10:.1f})"]\
                                .append(switched)
                        px = f(r["price"])
                        # Grok's literal condition -- selects on the outcome
                        if pr_v is not None and px is not None \
                                and px + pr_v < 1.02:
                            literal_p1.append(switched)
                    ppc = f(prev["effective_pair_cost"])
                    if pc is not None and ppc is not None:
                        pc_delta["post" if is_post else "pre"].append(pc - ppc)
                prev = r

    # ---------------- windows ------------------------------------------
    wins = []
    with gzip.open(os.path.join(a.dataset, "l5_window_summary.csv.gz"),
                   "rt", newline="", encoding="utf-8") as fh:
        wins = list(csv.DictReader(fh))

    # ---------------- P1 ------------------------------------------------
    pt = rate(switch_given_resid)
    lo, hi = boot_ci(switch_given_resid, rate, rng)
    lpt = rate(literal_p1) if literal_p1 else None
    buckets = {k: {"n": len(v), "p_switch": round(rate(v), 4)}
               for k, v in sorted(switch_by_resid_vwap.items())
               if len(v) >= 200}
    R["P1"] = {
        "grok_statement": "Given residual>0 on side A, next fill is on B "
                          "with p>0.55 when price_B + residual_vwap_A < 1.02",
        "TESTABLE_AS_WRITTEN": False,
        "why_not": "The condition uses price_B, which exists in this data "
                   "only for fills that happened. Evaluating 'did it switch "
                   "to B' on rows selected by B's own fill price conditions "
                   "on the outcome. The literal number is reported for "
                   "completeness and is NOT evidence.",
        "literal_biased_value": None if lpt is None else round(lpt, 4),
        "literal_biased_n": len(literal_p1),
        "non_circular_unconditional": {
            "statement": "P(next fill switches leg | residual>0 now)",
            "n": len(switch_given_resid),
            "value": round(pt, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "VERDICT_vs_0.55": verdict(pt, lo, hi, ">", 0.55),
        },
        "non_circular_monotonicity": {
            "statement": "P(switch) by residual VWAP decile -- if switching "
                         "is driven by the pair-cost opportunity, P(switch) "
                         "should RISE with residual VWAP, because a dearer "
                         "residual needs a cheaper complement to stay under "
                         "1.0.",
            "by_residual_vwap": buckets,
        },
    }

    # ---------------- P2 ------------------------------------------------
    sub = [w for w in wins
           if w["final_pair_cost"] and f(w["final_residual_qty"], 0.0) > 0]
    pcs = [f(w["final_pair_cost"]) for w in sub]
    med = statistics.median(pcs)
    mlo, mhi = boot_ci(pcs, statistics.median, rng)
    s = sorted(pcs)
    p75 = s[int(len(s) * 0.75)]
    p75lo, p75hi = boot_ci(pcs, lambda v: sorted(v)[int(len(v) * 0.75)], rng)
    R["P2"] = {
        "grok_statement": "Median final effective_pair_cost <= 1.00 and "
                          "p75 <= 1.06, over windows forming >=1 pair, "
                          "excluding zero-residual windows",
        "n_windows": len(sub),
        "median": round(med, 5), "median_ci95": [round(mlo, 5), round(mhi, 5)],
        "VERDICT_median_le_1.00": verdict(med, mlo, mhi, "<", 1.00),
        "p75": round(p75, 5), "p75_ci95": [round(p75lo, 5), round(p75hi, 5)],
        "VERDICT_p75_le_1.06": verdict(p75, p75lo, p75hi, "<", 1.06),
    }

    # ---------------- P3 ------------------------------------------------
    allsw = [1 if int(w["n_leg_switches"] or 0) > 0 else 0 for w in wins]
    apt = rate(allsw)
    alo, ahi = boot_ci(allsw, rate, rng)
    e = [1 if int(w["n_leg_switches"] or 0) > 0 else 0 for w in wins
         if w["window_start_utc"][:7] in EARLY]
    l = [1 if int(w["n_leg_switches"] or 0) > 0 else 0 for w in wins
         if w["window_start_utc"][:7] in LATE]
    ept, lpt2 = rate(e), rate(l)
    elo, ehi = boot_ci(e, rate, rng)
    llo, lhi = boot_ci(l, rate, rng)
    R["P3"] = {
        "grok_statement": "Fraction of windows with >=1 leg switch is >0.70 "
                          "overall, and higher in August than March-May",
        "overall": {"n": len(allsw), "value": round(apt, 4),
                    "ci95": [round(alo, 4), round(ahi, 4)],
                    "VERDICT_gt_0.70": verdict(apt, alo, ahi, ">", 0.70)},
        "march_may": {"n": len(e), "value": round(ept, 4),
                      "ci95": [round(elo, 4), round(ehi, 4)]},
        "august": {"n": len(l), "value": round(lpt2, 4),
                   "ci95": [round(llo, 4), round(lhi, 4)]},
        "VERDICT_august_higher":
            "SUPPORTED" if llo > ehi else
            ("NOT_SUPPORTED" if lhi < elo else
             "INDETERMINATE (CIs overlap)"),
        "direction_observed": "august_higher" if lpt2 > ept
                              else "march_may_higher",
    }

    # ---------------- P4 ------------------------------------------------
    def topn(c, n=8):
        tot = sum(c.values())
        return [{"size": k, "share": round(v / tot, 4)}
                for k, v in c.most_common(n)]
    es, ls = size_pref_share["early"], size_pref_share["late"]
    esp, lsp = rate(es), rate(ls)
    eslo, eshi = boot_ci(es, rate, rng)
    lslo, lshi = boot_ci(ls, rate, rng)
    R["P4"] = {
        "grok_statement": "Size distribution remains peaked on the same "
                          "discrete set {5,10,20,30,50,80} in both regimes",
        "TESTABLE_AS_WRITTEN": False,
        "why_not": "No threshold was stated, so there is nothing to pass or "
                   "fail. Reported as mode sets plus the share of fills "
                   "landing exactly on Grok's set.",
        "early_top_sizes": topn(size_by_regime["early"]),
        "late_top_sizes": topn(size_by_regime["late"]),
        "share_on_grok_set": {
            "march_may": {"n": len(es), "value": round(esp, 4),
                          "ci95": [round(eslo, 4), round(eshi, 4)]},
            "august": {"n": len(ls), "value": round(lsp, 4),
                       "ci95": [round(lslo, 4), round(lshi, 4)]},
        },
        "mode_set_overlap": len(
            {d["size"] for d in topn(size_by_regime["early"], 6)} &
            {d["size"] for d in topn(size_by_regime["late"], 6)}),
    }

    # ---------------- P5 ------------------------------------------------
    gmed = statistics.median(gaps)
    glo, ghi = boot_ci(gaps, statistics.median, rng)
    zpt = rate(gap_zero)
    zlo, zhi = boot_ci(gap_zero, rate, rng)
    R["P5"] = {
        "grok_statement": "Median seconds_since_prev_fill <=3s, and the mass "
                          "at 0s is >30% of successive same-window fills",
        "n_gaps": len(gaps),
        "median_gap_s": round(gmed, 4),
        "median_ci95": [round(glo, 4), round(ghi, 4)],
        "VERDICT_median_le_3s": verdict(gmed, glo, ghi, "<", 3.0),
        "share_zero_second": round(zpt, 4),
        "share_zero_ci95": [round(zlo, 4), round(zhi, 4)],
        "VERDICT_zero_share_gt_0.30": verdict(zpt, zlo, zhi, ">", 0.30),
    }

    # ---------------- P6 ------------------------------------------------
    fr = []
    for w in wins:
        if f(w["final_residual_qty"], 0.0) <= 200:
            continue
        side = (w["final_residual_side"] or "").strip().lower()
        up, dn = resid_side_fills.get(w["event_slug"], [0, 0])
        tot = up + dn
        if not tot or side not in ("up", "down"):
            continue
        fr.append((up if side == "up" else dn) / tot)
    fpt = statistics.fmean(fr) if fr else None
    flo, fhi = boot_ci(fr, statistics.fmean, rng)
    above = rate([1 if x > 0.85 else 0 for x in fr]) if fr else None
    R["P6"] = {
        "grok_statement": "In windows ending with residual>200, the fraction "
                          "of fills on the residual side is >0.85 "
                          "(one-sided accumulation is intentional)",
        "n_windows": len(fr),
        "mean_fraction_on_residual_side": None if fpt is None else round(fpt, 4),
        "ci95": [None if flo is None else round(flo, 4),
                 None if fhi is None else round(fhi, 4)],
        "VERDICT_gt_0.85": verdict(fpt, flo, fhi, ">", 0.85),
        "share_of_such_windows_above_0.85":
            None if above is None else round(above, 4),
    }

    # ---------------- P7 ------------------------------------------------
    ppt = rate(post_expiry)
    plo, phi = boot_ci(post_expiry, rate, rng)
    pre, post = pc_delta["pre"], pc_delta["post"]
    pre_m = statistics.fmean(pre) if pre else None
    post_m = statistics.fmean(post) if post else None
    prelo, prehi = boot_ci(pre, statistics.fmean, rng)
    poslo, poshi = boot_ci(post, statistics.fmean, rng)
    R["P7"] = {
        "grok_statement": "Post-expiry fills are <5% of all fills and do not "
                          "improve pair cost more than pre-expiry fills",
        "n_fills": n_fills,
        "share_post_expiry": round(ppt, 5),
        "ci95": [round(plo, 5), round(phi, 5)],
        "VERDICT_lt_0.05": verdict(ppt, plo, phi, "<", 0.05),
        "mean_change_in_pair_cost": {
            "_note": "negative = pair cost improved on that fill",
            "pre_expiry": {"n": len(pre),
                           "value": None if pre_m is None else round(pre_m, 6),
                           "ci95": [None if prelo is None else round(prelo, 6),
                                    None if prehi is None else round(prehi, 6)]},
            "post_expiry": {"n": len(post),
                            "value": None if post_m is None else round(post_m, 6),
                            "ci95": [None if poslo is None else round(poslo, 6),
                                     None if poshi is None else round(poshi, 6)]},
        },
        "VERDICT_post_not_better":
            "INDETERMINATE (insufficient post-expiry n)" if len(post) < 30 else
            ("SUPPORTED" if (post_m is not None and pre_m is not None
                             and post_m >= pre_m) else "NOT_SUPPORTED"),
    }

    R["_meta"] = {
        "fills_tested": n_fills, "windows_tested": len(wins),
        "bootstrap": {"resamples": BOOT, "resample_size_cap": BOOT_CAP,
                      "seed": SEED, "ci": "95% percentile"},
        "multiple_comparisons":
            "7 predictions, several with two sub-tests (11 threshold tests "
            "in total). A naive 0.05 threshold expects ~0.55 false "
            "positives at this count; Bonferroni-adjusted is 0.0045. "
            "Verdicts here are CI-based, not p-value-based, and any CI "
            "spanning the stated threshold is reported INDETERMINATE.",
        "verdict_rule":
            "SUPPORTED only when the whole 95% CI clears the threshold Grok "
            "stated. NOT_SUPPORTED only when the whole CI is on the wrong "
            "side. Otherwise INDETERMINATE.",
    }

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    for k in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
        v = R[k]
        print(f"\n=== {k} ===")
        print(json.dumps(v, indent=1)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
