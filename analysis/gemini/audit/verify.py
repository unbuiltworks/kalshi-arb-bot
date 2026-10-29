"""INDEPENDENT recomputation. Imports NOTHING from the primary implementation.

It may not import `report`, `metric`, `p10h_gate`, `p10b_decode`,
`p10e_audit_decode` or `p11a_blocked_census`. It re-reads the same raw
artifacts and recomputes the load-bearing totals its own way, so that
"verified" means two implementations agreed rather than one implementation ran
twice. A test enforces the import ban.

Where a different ALGORITHM is available it uses one:

* the gate's Clopper-Pearson upper bound is computed here as the 0.95 quantile
  of Beta(k+1, n-k) via a continued-fraction incomplete beta, NOT by summing
  binomial terms and bisecting. Those two routes share no arithmetic.
* the decoder classification is redone from the raw capture rows with its own
  bracketing and its own tolerance handling.
"""
from __future__ import annotations

import glob
import json
import math
import os
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
CAPDIR = os.path.join(os.path.dirname(HERE), "p10_capture")
TOL = F(1, 100)


# ---------------------------------------------------------------- the bound

def _betacf(a, b, x, itmax=300, eps=3e-16):
    """Continued fraction for the incomplete beta (Lentz). Standard form."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def clopper_pearson_upper(k, n, alpha=0.05):
    """Upper bound as the (1-alpha) quantile of Beta(k+1, n-k).

    Found by bisecting the BETA CDF -- a different function from the binomial
    sum the primary implementation bisects. For k == n the bound is 1.
    """
    if n == 0:
        return 1.0
    if k >= n:
        return 1.0
    a, b = k + 1.0, float(n - k)
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if betainc(a, b, mid) < 1.0 - alpha:
            lo = mid
        else:
            hi = mid
    return hi


# ------------------------------------------------------- the gate population

def _captures():
    return sorted(glob.glob(os.path.join(CAPDIR, "*.jsonl")) +
                  glob.glob(os.path.join(CAPDIR, "*.jsonl.partial")))


def _fits(p, bid, ask):
    lo, hi = min(bid, ask) - TOL, max(bid, ask) + TOL
    return lo <= p <= hi


def gate_population(family="BTC", max_stale_s=5.0):
    """Re-derive the decoder population from raw capture rows.

    Own bracketing: for each trade, collect every book row for that symbol
    within `max_stale_s` on either side, and separately note whether any book
    at all preceded it. Deliberately not the primary's prev/next structure.
    """
    books = defaultdict(list)
    trades = []
    for path in _captures():
        with open(path) as fh:
            for ln in fh:
                try:
                    d = json.loads(ln)
                except Exception:                              # noqa: BLE001
                    continue
                k = d.get("kind")
                if k == "book":
                    if d.get("bid") in (None, "") or d.get("ask") in (None, ""):
                        continue
                    books[d["symbol"]].append(
                        (float(d["t_recv"]), F(str(d["bid"])), F(str(d["ask"]))))
                elif k == "trade":
                    trades.append(d)
    for v in books.values():
        v.sort()

    best = {}
    for t in trades:
        sym = t.get("symbol") or ""
        tid = t.get("tid")
        if tid is None:
            continue
        core = sym.split("-")[1] if "-" in sym else sym
        if family and not core.startswith(family):
            continue
        rows = books.get(sym) or []
        ts = (t.get("tms") or 0) / 1000.0
        if not rows:
            lab = "NO_BOOK"
        elif ts < rows[0][0]:
            lab = "PRE_CAPTURE"
        else:
            near = [r for r in rows if abs(r[0] - ts) <= max_stale_s]
            if not near:
                lab = "STALE"
            else:
                p = F(str(t["price"]))
                q = F(1) - p
                h1 = any(_fits(p, b, a) for _t, b, a in near)
                h2 = any(_fits(q, b, a) for _t, b, a in near)
                lab = ("H1" if (h1 and not h2) else
                       "H2" if (h2 and not h1) else
                       "AMBIGUOUS_BOTH" if (h1 and h2) else
                       "AMBIGUOUS_NEITHER")
        key = (sym, tid)
        rank = {"H1": 0, "H2": 0, "AMBIGUOUS_BOTH": 1, "AMBIGUOUS_NEITHER": 1,
                "STALE": 2, "PRE_CAPTURE": 3, "NO_BOOK": 4}
        if key not in best or rank[lab] < rank[best[key]]:
            best[key] = lab

    out = defaultdict(int)
    for lab in best.values():
        out[lab] += 1
    out["_unique_keys"] = len(best)
    out["_decoded"] = out["H1"] + out["H2"]
    out["_ambiguous"] = out["AMBIGUOUS_BOTH"] + out["AMBIGUOUS_NEITHER"]
    out["_observed"] = out["_decoded"] + out["_ambiguous"] + out["STALE"]
    return dict(out)


def duplicate_keys():
    """(symbol, tid) pairs seen in more than one capture file.

    Duplication across files is EXPECTED -- two recorders can watch one symbol
    -- and is why the population is taken unique. This reports the count so a
    report can state it rather than assume it.
    """
    seen = defaultdict(set)
    for path in _captures():
        with open(path) as fh:
            for ln in fh:
                try:
                    d = json.loads(ln)
                except Exception:                              # noqa: BLE001
                    continue
                if d.get("kind") == "trade" and d.get("tid") is not None:
                    seen[(d["symbol"], d["tid"])].add(path)
    return {"keys": len(seen),
            "in_multiple_files": sum(1 for v in seen.values() if len(v) > 1)}


# ------------------------------------------------------ the action census

def census_totals(path=None):
    """Re-parse the L5F_ACTIONS census with its own parser and sum it.

    Sums per-column rather than per-row, so a row the primary mis-splits
    would not produce the same totals here.
    """
    path = path or os.path.join(HERE, "l5f_actions_20260922.tsv")
    cols = defaultdict(lambda: defaultdict(int))
    windows = defaultdict(int)
    blocked = defaultdict(lambda: defaultdict(int))
    fills_hist = defaultdict(int)
    for ln in open(path):
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.rstrip("\n").split("\t")
        if len(f) < 9:
            continue
        fam = f[1]
        windows[fam] += 1
        for i, k in ((2, "reqOPEN"), (3, "reqEXTEND"), (4, "reqCOMPLETE"),
                     (5, "filOPEN"), (6, "filEXTEND"), (7, "filCOMPLETE")):
            cols[fam][k] += int(f[i])
        fills_hist[int(f[5]) + int(f[6]) + int(f[7])] += 1
        if f[8] != "-":
            for part in f[8].split(";"):
                kind, by, n = part.split(":")
                blocked[by][kind] += int(n)
    return {"windows": dict(windows),
            "cols": {k: dict(v) for k, v in cols.items()},
            "blocked": {k: dict(v) for k, v in blocked.items()},
            "fills_per_window_hist": dict(fills_hist)}


if __name__ == "__main__":
    import pprint
    print("gate population (BTC):")
    pprint.pprint(gate_population("BTC"))
    print("\nduplicates:", duplicate_keys())
    print("\ncensus:")
    pprint.pprint(census_totals())
    print("\nbound k=2 n=118 ->", clopper_pearson_upper(2, 118))
