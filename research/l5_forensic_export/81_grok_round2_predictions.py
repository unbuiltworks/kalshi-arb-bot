"""Test Grok's revised (round 2) predictions against the full corpus.

Same verdict discipline as round 1: SUPPORTED only when the whole 95%
bootstrap CI clears the stated threshold, NOT_SUPPORTED only when the whole
CI is on the wrong side, INDETERMINATE otherwise.

Two of these are flagged as NOT INDEPENDENT TESTS. R2-P2 and R2-P3 both
assert that same-leg runs lengthen from the early to the late regime. We
already measured in round 1 that the switch rate falls from 0.978 to
0.730. Longer runs and a lower switch rate are close to the same statement
-- mean run length is approximately 1/p_switch -- so these predictions are
largely entailed by a result Grok had already been shown. They are
measured anyway, because the magnitude is informative, but passing them is
weak evidence for the revised account.

R2-P6 is different and genuinely informative: it measures the pair-cost
consequence of switching rather than its frequency, so it can fail
independently.

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

BOOT = 2000
BOOT_CAP = 20000
SEED = 20261009
EARLY = {"2026-03", "2026-04", "2026-05"}
LATE = {"2026-08"}
BIG_MODES = {30.0, 50.0, 80.0}
GROK_SET = {5.0, 10.0, 20.0, 30.0, 50.0, 80.0}


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


def mean(v):
    return statistics.fmean(v) if v else float("nan")


def verdict(point, lo, hi, op, thresh):
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


def disjoint(alo, ahi, blo, bhi):
    if None in (alo, ahi, blo, bhi):
        return "INDETERMINATE (insufficient n)"
    if alo > bhi:
        return "SUPPORTED (a > b, CIs disjoint)"
    if ahi < blo:
        return "NOT_SUPPORTED (a < b, CIs disjoint)"
    return "INDETERMINATE (CIs overlap)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)
    R = {}

    same_by_month_dec = collections.defaultdict(list)
    same_range_month = collections.defaultdict(dict)
    runs = {"early": [], "late": []}
    longest_ge10 = {"early": [], "late": []}
    switch_by_prevsize = {"small": [], "large": []}
    pcd = {"switch": [], "same": []}
    size_mass = {"early": collections.Counter(), "late": collections.Counter()}
    big_share = {"early": [], "late": []}
    late_vs_mid = []
    MED_SIZE = 15.0                     # corpus median from round 1

    cur = None
    prev = None
    run_len = 0
    run_leg = None
    win_runs = []
    win_reg = None
    win_mid, win_last = [], []
    win_dur = None

    def close_window():
        nonlocal win_runs, win_reg, win_mid, win_last
        if win_reg in ("early", "late") and win_runs:
            runs[win_reg].extend(win_runs)
            longest_ge10[win_reg].append(1 if max(win_runs) >= 10 else 0)
        if win_mid and win_last:
            late_vs_mid.append(mean(win_last) - mean(win_mid))
        win_runs, win_mid, win_last = [], [], []

    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                slug = r["event_slug"]
                if slug != cur:
                    if cur is not None:
                        if run_len:
                            win_runs.append(run_len)
                        close_window()
                    cur, prev = slug, None
                    run_len, run_leg = 0, None
                    month = r["datetime_utc"][:7]
                    win_reg = "early" if month in EARLY else (
                        "late" if month in LATE else None)
                    win_dur = f(r["duration_minutes"])

                month = r["datetime_utc"][:7]
                leg = r["leg"]
                sz = f(r["size"])
                pc = f(r["effective_pair_cost"])

                # same-leg runs
                if leg == run_leg:
                    run_len += 1
                else:
                    if run_len:
                        win_runs.append(run_len)
                    run_leg, run_len = leg, 1

                # sizes by regime
                if win_reg and sz is not None:
                    size_mass[win_reg][sz] += 1
                    big_share[win_reg].append(1 if sz in BIG_MODES else 0)

                # residual in middle third vs final 60s
                sfs = f(r["seconds_from_window_start"])
                sts = f(r["seconds_to_settlement"])
                rq = f(r["residual_qty"], 0.0)
                if win_dur and sfs is not None:
                    W = win_dur * 60.0
                    if W / 3.0 <= sfs < 2.0 * W / 3.0:
                        win_mid.append(rq)
                if sts is not None and 0 <= sts <= 60:
                    win_last.append(rq)

                if prev is not None:
                    switched = int(r["leg_switched_from_prev"] or 0)
                    pr_q = f(prev["residual_qty"], 0.0)
                    pr_v = f(prev["residual_vwap"])
                    if pr_q and pr_q > 0 and pr_v is not None:
                        d = min(int(pr_v * 10), 9)
                        same_by_month_dec[(month, d)].append(1 - switched)
                    psz = f(prev["size"])
                    if psz is not None:
                        switch_by_prevsize[
                            "small" if psz < MED_SIZE else "large"
                        ].append(switched)
                    ppc = f(prev["effective_pair_cost"])
                    if pc is not None and ppc is not None:
                        pcd["switch" if switched else "same"].append(pc - ppc)
                prev = r
    if run_len:
        win_runs.append(run_len)
    close_window()

    # ---- R2-P1: per-month flatness of P(same leg) across residual deciles
    months = sorted({m for m, _ in same_by_month_dec})
    per_month = {}
    for m in months:
        vals = {d: rate(same_by_month_dec[(m, d)])
                for d in range(10)
                if len(same_by_month_dec.get((m, d), [])) >= 200}
        if len(vals) < 5:
            continue
        rng_ = max(vals.values()) - min(vals.values())
        per_month[m] = {
            "deciles_used": len(vals),
            "p_same_min": round(min(vals.values()), 4),
            "p_same_max": round(max(vals.values()), 4),
            "range": round(rng_, 4),
            "within_plus_minus_0.05": bool(rng_ <= 0.10),
            "by_decile": {f"[{d/10:.1f},{(d+1)/10:.1f})": round(v, 4)
                          for d, v in sorted(vals.items())},
        }
    ok = sum(1 for v in per_month.values() if v["within_plus_minus_0.05"])
    R["R2-P1"] = {
        "grok_statement": "Conditional on residual>0, P(next fill same leg) "
                          "is independent of residual-VWAP decile in EVERY "
                          "calendar month (flat within +/-0.05)",
        "months_tested": len(per_month),
        "months_flat_within_0.10_range": ok,
        "VERDICT": "SUPPORTED" if ok == len(per_month) else
                   f"NOT_SUPPORTED ({len(per_month) - ok} of "
                   f"{len(per_month)} months exceed the band)",
        "per_month": per_month,
    }

    # ---- R2-P2: mean run length late / early >= 1.5x
    em, lm = mean(runs["early"]), mean(runs["late"])
    elo, ehi = boot_ci(runs["early"], mean, rng)
    llo, lhi = boot_ci(runs["late"], mean, rng)
    ratio = lm / em if em else None
    R["R2-P2"] = {
        "grok_statement": "Mean maximal same-leg run length is >=1.5x longer "
                          "in August than in March-May",
        "INDEPENDENT_TEST": False,
        "why_not": "Mean run length is approximately 1/p_switch, and the "
                   "switch-rate decline (0.978 -> 0.730) was measured in "
                   "round 1 and shown to Grok. This is largely a "
                   "restatement of a result already in hand, so passing it "
                   "is weak evidence for the revised account.",
        "march_may": {"n_runs": len(runs["early"]), "mean": round(em, 4),
                      "ci95": [round(elo, 4), round(ehi, 4)]},
        "august": {"n_runs": len(runs["late"]), "mean": round(lm, 4),
                   "ci95": [round(llo, 4), round(lhi, 4)]},
        "ratio_august_over_march_may": None if ratio is None
        else round(ratio, 4),
        "VERDICT_ratio_ge_1.5":
            "SUPPORTED" if (ratio and llo / ehi >= 1.5) else
            ("NOT_SUPPORTED" if (ratio and lhi / elo < 1.5)
             else "INDETERMINATE (CI ratio spans 1.5)"),
    }

    # ---- R2-P3: share of windows with longest run >= 10
    ep, lp = rate(longest_ge10["early"]), rate(longest_ge10["late"])
    e3lo, e3hi = boot_ci(longest_ge10["early"], rate, rng)
    l3lo, l3hi = boot_ci(longest_ge10["late"], rate, rng)
    R["R2-P3"] = {
        "grok_statement": "Fraction of windows whose longest same-leg run "
                          ">=10 fills rises from early to late regime "
                          "(non-overlapping CIs)",
        "INDEPENDENT_TEST": False,
        "why_not": "Same objection as R2-P2 -- entailed by the already-known "
                   "switch-rate decline.",
        "march_may": {"n": len(longest_ge10["early"]), "value": round(ep, 4),
                      "ci95": [round(e3lo, 4), round(e3hi, 4)]},
        "august": {"n": len(longest_ge10["late"]), "value": round(lp, 4),
                   "ci95": [round(l3lo, 4), round(l3hi, 4)]},
        "VERDICT_late_higher": disjoint(l3lo, l3hi, e3lo, e3hi),
    }

    # ---- R2-P4: P(switch | prev size < median) > P(switch | >= median)
    sm, sl = rate(switch_by_prevsize["small"]), \
        rate(switch_by_prevsize["large"])
    smlo, smhi = boot_ci(switch_by_prevsize["small"], rate, rng)
    sllo, slhi = boot_ci(switch_by_prevsize["large"], rate, rng)
    R["R2-P4"] = {
        "grok_statement": "P(switch | just-filled size < median) > "
                          "P(switch | just-filled size >= median)",
        "median_size_used": MED_SIZE,
        "prev_size_below_median": {"n": len(switch_by_prevsize["small"]),
                                   "p_switch": round(sm, 4),
                                   "ci95": [round(smlo, 4), round(smhi, 4)]},
        "prev_size_at_or_above_median": {
            "n": len(switch_by_prevsize["large"]), "p_switch": round(sl, 4),
            "ci95": [round(sllo, 4), round(slhi, 4)]},
        "VERDICT_small_higher": disjoint(smlo, smhi, sllo, slhi),
    }

    # ---- R2-P5: residual in final 60s not smaller than middle third
    dm = mean(late_vs_mid)
    dlo, dhi = boot_ci(late_vs_mid, mean, rng)
    R["R2-P5"] = {
        "grok_statement": "Mean residual_qty in the final 60s is NOT smaller "
                          "than in the middle third of the same window "
                          "(i.e. no late closing)",
        "n_windows_with_both": len(late_vs_mid),
        "mean_paired_difference_last60s_minus_middlethird":
            None if dm != dm else round(dm, 4),
        "ci95": [None if dlo is None else round(dlo, 4),
                 None if dhi is None else round(dhi, 4)],
        "VERDICT_not_smaller":
            "INDETERMINATE (insufficient n)" if dlo is None else
            ("SUPPORTED" if dlo >= 0 else
             ("NOT_SUPPORTED (residual IS smaller late)" if dhi < 0
              else "INDETERMINATE (CI spans zero)")),
    }

    # ---- R2-P6: pair-cost change on switch vs same-leg fills
    swm, sam = mean(pcd["switch"]), mean(pcd["same"])
    swlo, swhi = boot_ci(pcd["switch"], mean, rng)
    salo, sahi = boot_ci(pcd["same"], mean, rng)
    overlap = not (swlo > sahi or swhi < salo)
    R["R2-P6"] = {
        "grok_statement": "Delta(effective_pair_cost) on a switch fill is "
                          "statistically indistinguishable from Delta on a "
                          "same-leg fill (switching is not "
                          "opportunity-driven)",
        "INDEPENDENT_TEST": True,
        "_note": "negative = pair cost improved on that fill",
        "switch_fills": {"n": len(pcd["switch"]), "mean_delta": round(swm, 6),
                         "ci95": [round(swlo, 6), round(swhi, 6)]},
        "same_leg_fills": {"n": len(pcd["same"]), "mean_delta": round(sam, 6),
                           "ci95": [round(salo, 6), round(sahi, 6)]},
        "VERDICT_indistinguishable":
            "SUPPORTED (CIs overlap)" if overlap else
            "NOT_SUPPORTED (CIs disjoint -- switching DOES change pair cost "
            "differently)",
    }

    # ---- R2-P7: mass on {30,50,80} higher in August; mode set unchanged
    bge, bgl = rate(big_share["early"]), rate(big_share["late"])
    blo, bhi = boot_ci(big_share["early"], rate, rng)
    bllo, blhi = boot_ci(big_share["late"], rate, rng)

    def top(c, n=6):
        tot = sum(c.values())
        return [{"size": k, "share": round(v / tot, 4)}
                for k, v in c.most_common(n)]
    et, lt = top(size_mass["early"]), top(size_mass["late"])
    eset, lset = {d["size"] for d in et}, {d["size"] for d in lt}
    R["R2-P7"] = {
        "grok_statement": "Mass on the larger discrete modes {30,50,80} is "
                          "higher in August than March-May, while the mode "
                          "set itself stays the same",
        "share_on_30_50_80": {
            "march_may": {"n": len(big_share["early"]),
                          "value": round(bge, 4),
                          "ci95": [round(blo, 4), round(bhi, 4)]},
            "august": {"n": len(big_share["late"]), "value": round(bgl, 4),
                       "ci95": [round(bllo, 4), round(blhi, 4)]},
        },
        "VERDICT_august_higher": disjoint(bllo, blhi, blo, bhi),
        "top6_march_may": et, "top6_august": lt,
        "mode_set_identical": bool(eset == lset),
        "mode_set_overlap_of_6": len(eset & lset),
        "VERDICT_mode_set_unchanged":
            "SUPPORTED" if eset == lset else
            f"NOT_SUPPORTED (only {len(eset & lset)} of 6 modes shared)",
    }

    R["_meta"] = {
        "round": 2,
        "bootstrap": {"resamples": BOOT, "resample_size_cap": BOOT_CAP,
                      "seed": SEED, "ci": "95% percentile"},
        "independence_caveat":
            "R2-P2 and R2-P3 are not independent tests of the revised "
            "account: both restate the switch-rate decline measured in "
            "round 1 and shown to Grok before it wrote them. R2-P1, R2-P4, "
            "R2-P5, R2-P6 and R2-P7 can fail independently.",
        "post_hoc_caveat":
            "Round 2 was written after Grok saw round-1 results on this same "
            "corpus, so it is no longer a blind prediction exercise. "
            "Cumulative threshold tests across both rounds now exceed 20; "
            "a Bonferroni-adjusted 0.05 is below 0.0025. Verdicts remain "
            "CI-based.",
    }

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    for k in [k for k in R if k != "_meta"]:
        print(f"\n=== {k} ===")
        print(json.dumps(R[k], indent=1)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
