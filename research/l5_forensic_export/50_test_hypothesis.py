"""Stage E -- test a declared hypothesis against the FULL corpus.

Every hypothesis Grok or Perplexity returns should come back here before
anyone believes it. The point is that the test is declared as data, not
written as bespoke analysis code each time, so the same machinery (and the
same multiple-comparison bookkeeping) applies to all of them.

A hypothesis is a JSON file:

{
  "id": "H-001",
  "statement": "plain English claim, stated so it could be wrong",
  "unit": "fill" | "window",
  "filter":  [["price", ">=", 0.4], ["leg", "==", "UP"]],
  "group_by": "duration_minutes",
  "metric": {"column": "window_pnl", "stat": "mean"},
  "predicts": {"direction": "greater", "between": ["5", "15"]}
}

Operators: == != < <= > >= in not_in contains isnull notnull

Virtual columns usable in `group_by` and in `filter`, so the common
groupings need no new data file:
  month                     YYYY-MM of the row's own timestamp
  date                      YYYY-MM-DD
  hour                      UTC hour, "00".."23"
  bucket:<col>:<e1,e2,..>   numeric <col> bucketed by those edges
Stats:     mean median sum count rate_positive share_of_rows p25 p75 std

What it reports:

  * the metric per group, with n;
  * a bootstrap 95% CI per group (5,000 resamples, seeded, so reruns match);
  * for a two-group prediction, the observed difference, its bootstrap CI,
    and a two-sided permutation p-value;
  * VERDICT: SUPPORTED / NOT_SUPPORTED / INDETERMINATE (CI spans zero).

What it will NOT do: it will not report a p-value without also reporting
the effect size and n, and it will not call a prediction supported on a
CI that includes zero. It also records how many hypotheses have been run
against this corpus (`--ledger`), because the 20th test on the same data
is not the same evidence as the first.

Usage:
  python3 50_test_hypothesis.py --dataset DIR --hypothesis H.json \\
      [--ledger export/hypothesis_ledger.json] [--out results.json]
  python3 50_test_hypothesis.py --dataset DIR --columns      # list columns
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

BOOT = 5000
SEED = 20261009


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def virtual(name, row):
    """Virtual columns. Returns a string, or None if `name` is not virtual."""
    if name in ("month", "date", "hour"):
        ts = row.get("datetime_utc") or row.get("window_start_utc") or ""
        return {"month": ts[:7], "date": ts[:10], "hour": ts[11:13]}[name]
    if name.startswith("bucket:"):
        _, col, edges = name.split(":", 2)
        v = fnum(row.get(col))
        if v is None:
            return "NULL"
        es = [float(x) for x in edges.split(",")]
        if v < es[0]:
            return f"<{es[0]:g}"
        for lo, hi in zip(es, es[1:]):
            if lo <= v < hi:
                return f"[{lo:g},{hi:g})"
        return f">={es[-1]:g}"
    return None


def cell(name, row):
    v = virtual(name, row)
    return row[name] if v is None else v


OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: fnum(a) is not None and fnum(a) < float(b),
    "<=": lambda a, b: fnum(a) is not None and fnum(a) <= float(b),
    ">": lambda a, b: fnum(a) is not None and fnum(a) > float(b),
    ">=": lambda a, b: fnum(a) is not None and fnum(a) >= float(b),
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "contains": lambda a, b: str(b) in str(a),
    "isnull": lambda a, b: a in ("", None),
    "notnull": lambda a, b: a not in ("", None),
}


def rows_of(dataset, unit):
    if unit == "window":
        p = os.path.join(dataset, "l5_window_summary.csv.gz")
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            yield from csv.DictReader(fh)
    elif unit == "fill":
        for p in sorted(glob.glob(os.path.join(
                dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
            with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
                yield from csv.DictReader(fh)
    else:
        raise SystemExit(f"unit must be 'fill' or 'window', got {unit!r}")


def stat_of(vals, name, n_total):
    v = [x for x in vals if x is not None]
    if name == "count":
        return float(len(vals))
    if name == "share_of_rows":
        return len(vals) / n_total if n_total else float("nan")
    if not v:
        return float("nan")
    if name == "mean":
        return statistics.fmean(v)
    if name == "median":
        return statistics.median(v)
    if name == "sum":
        return sum(v)
    if name == "rate_positive":
        return sum(1 for x in v if x > 0) / len(v)
    if name == "std":
        return statistics.pstdev(v) if len(v) > 1 else 0.0
    if name == "p25":
        return sorted(v)[max(0, int(len(v) * 0.25) - 1)]
    if name == "p75":
        return sorted(v)[max(0, int(len(v) * 0.75) - 1)]
    raise SystemExit(f"unknown stat {name!r}")


def boot_ci(vals, name, n_total, rng, reps=BOOT):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None, None
    out = []
    k = len(v)
    for _ in range(reps):
        out.append(stat_of([v[rng.randrange(k)] for _ in range(k)],
                           name, n_total))
    out.sort()
    return out[int(reps * 0.025)], out[int(reps * 0.975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--hypothesis")
    ap.add_argument("--out")
    ap.add_argument("--ledger")
    ap.add_argument("--columns", action="store_true")
    a = ap.parse_args()

    if a.columns:
        for unit in ("fill", "window"):
            r = next(rows_of(a.dataset, unit))
            print(f"\n--- unit={unit} ---")
            for c in r:
                print(" ", c)
        print("\n--- virtual columns (any unit) ---")
        for c in ("month", "date", "hour", "bucket:<col>:<e1,e2,...>"):
            print(" ", c)
        return 0
    if not a.hypothesis:
        ap.error("--hypothesis is required unless --columns")

    with open(a.hypothesis, encoding="utf-8") as fh:
        h = json.load(fh)
    unit = h.get("unit", "fill")
    filt = h.get("filter") or []
    gb = h.get("group_by")
    metric = h["metric"]
    col, stat = metric["column"], metric.get("stat", "mean")

    groups = collections.defaultdict(list)
    n_seen = n_kept = 0
    for r in rows_of(a.dataset, unit):
        n_seen += 1
        ok = True
        for c, op, val in filt:
            if c not in r and virtual(c, r) is None:
                raise SystemExit(f"filter column {c!r} not in unit {unit!r}")
            if not OPS[op](cell(c, r), val):
                ok = False
                break
        if not ok:
            continue
        n_kept += 1
        g = cell(gb, r) if gb else "ALL"
        groups[g].append(fnum(r.get(col)) if stat != "count" else 1.0)

    rng = random.Random(SEED)
    res = {}
    for g, vals in sorted(groups.items()):
        pt = stat_of(vals, stat, n_kept)
        lo, hi = boot_ci(vals, stat, n_kept, rng)
        res[g] = {"n": len(vals), "value": None if pt != pt else round(pt, 6),
                  "ci95": [None if lo is None else round(lo, 6),
                           None if hi is None else round(hi, 6)]}

    out = {
        "hypothesis_id": h.get("id"),
        "statement": h.get("statement"),
        "unit": unit, "filter": filt, "group_by": gb,
        "metric": {"column": col, "stat": stat},
        "rows_scanned": n_seen, "rows_after_filter": n_kept,
        "groups": res,
        "bootstrap": {"resamples": BOOT, "seed": SEED, "ci": "95% percentile"},
    }

    pred = h.get("predicts") or {}
    pair = pred.get("between")
    if pair and len(pair) == 2 and all(p in groups for p in pair):
        A, B = groups[pair[0]], groups[pair[1]]
        a_v = stat_of(A, stat, n_kept)
        b_v = stat_of(B, stat, n_kept)
        diff = a_v - b_v
        dl = []
        for _ in range(BOOT):
            sa = [A[rng.randrange(len(A))] for _ in range(len(A))]
            sb = [B[rng.randrange(len(B))] for _ in range(len(B))]
            dl.append(stat_of(sa, stat, n_kept) - stat_of(sb, stat, n_kept))
        dl.sort()
        dlo, dhi = dl[int(BOOT * 0.025)], dl[int(BOOT * 0.975)]
        pool = [x for x in A + B if x is not None]
        na = len([x for x in A if x is not None])
        hits = 0
        for _ in range(BOOT):
            rng.shuffle(pool)
            d = stat_of(pool[:na], stat, n_kept) - \
                stat_of(pool[na:], stat, n_kept)
            if abs(d) >= abs(diff):
                hits += 1
        pval = (hits + 1) / (BOOT + 1)
        want = pred.get("direction", "greater")
        spans_zero = dlo <= 0 <= dhi
        if spans_zero:
            verdict = "INDETERMINATE"
        elif (want == "greater" and diff > 0) or \
             (want == "less" and diff < 0):
            verdict = "SUPPORTED"
        else:
            verdict = "NOT_SUPPORTED"
        out["comparison"] = {
            "between": pair, "a_minus_b": round(diff, 6),
            "a_minus_b_ci95": [round(dlo, 6), round(dhi, 6)],
            "permutation_p_two_sided": round(pval, 6),
            "predicted_direction": want,
            "ci_spans_zero": spans_zero,
            "VERDICT": verdict,
            "_note": "p-value is reported only alongside the effect size "
                     "and n. A CI spanning zero is INDETERMINATE, never "
                     "SUPPORTED, whatever the p-value.",
        }

    if a.ledger:
        led = {"corpus_fills": 760817, "tests": []}
        if os.path.exists(a.ledger):
            with open(a.ledger, encoding="utf-8") as fh:
                led = json.load(fh)
        led["tests"].append({"id": h.get("id"),
                             "statement": h.get("statement"),
                             "verdict": out.get("comparison", {})
                             .get("VERDICT", "DESCRIPTIVE_ONLY")})
        n = len(led["tests"])
        led["multiple_comparison_note"] = (
            f"{n} hypotheses have now been tested against this one corpus. "
            f"A naive 0.05 threshold expects ~{n * 0.05:.1f} false positives "
            f"at this count; a Bonferroni-adjusted threshold is "
            f"{0.05 / n:.5f}.")
        out["ledger"] = {"tests_run_against_this_corpus": n,
                         "note": led["multiple_comparison_note"]}
        with open(a.ledger, "w", encoding="utf-8") as fh:
            json.dump(led, fh, indent=1)

    txt = json.dumps(out, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
