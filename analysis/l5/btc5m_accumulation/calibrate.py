"""Calibrate candidate BTC5m probabilities. READ-ONLY, offline, no fitting
on anything a decision could not have seen.

THE QUESTION, in the order it has to be asked:

  1. Is the venue-index model CALIBRATED? When it says 0.70, does the side
     win 70% of the time?
  2. Is it INFORMATIVE BEYOND THE BOOK? Log loss and Brier against the book
     mid on the same rows. A model that only restates the price is worth
     nothing to a trader who can already read the price.
  3. Do STRIKE DISTANCE, VOLATILITY, TIME REMAINING and SHORT-TERM MOMENTUM
     add anything, fitted on TRAIN only and scored once on OOS?
  4. Does any of it survive as CONDITIONAL EV at the fills the accumulation
     policy would actually get?

SPLIT. Chronological by window, never shuffled: the first 60% of windows
are TRAIN, the next 20% VALIDATION (where a threshold may be looked at),
the last 20% OOS (scored once). A window is wholly in one block, because
rows inside a window share an outcome and splitting them would leak it.

CLUSTERING. Decision rows inside one window share one settlement, so they
are not independent observations. Every standard error here is clustered
BY WINDOW; treating 27,000 rows as 27,000 observations would overstate
significance by roughly the square root of the rows per window.

GROK'S COEFFICIENTS ARE NOT USED. No coefficient from any external
suggestion is taken as a default. Momentum enters only as a feature whose
weight is fitted here and reported with its uncertainty.

NOTHING HERE IS WIRED TO EXECUTION. This file fits and scores; it does not
change the controller, and no threshold it finds is a default anywhere.

Usage: python3 -I analysis/l5/btc5m_accumulation/calibrate.py
"""
from __future__ import annotations

import collections
import gzip
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ALIGNED = os.path.join(HERE, "btc5m_aligned_20261112.jsonl.gz")
INDEX = os.path.join(HERE, "btc_index_1s_20261112.jsonl.gz")

BAND_LO, BAND_HI = 0.51, 0.90
ENTRY_DEADLINE_S = 270.0


def load_index():
    t, a = [], []
    with gzip.open(INDEX, "rt") as fh:
        for line in fh:
            d = json.loads(line)
            t.append(int(d["t"]))
            a.append(float(d["a"]))
    return np.array(t), np.array(a)


def load_rows():
    by = collections.defaultdict(list)
    with gzip.open(ALIGNED, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            by[r["market"]].append(r)
    for v in by.values():
        v.sort(key=lambda r: r["t_in_window_s"])
    return by


def build():
    """One record per usable decision row, with causal features only."""
    it, ia = load_index()
    pos = {int(x): i for i, x in enumerate(it)}
    by = load_rows()
    recs = []
    for market in sorted(by):
        rows = by[market]
        won = rows[0].get("settled_outcome")
        if won is None:
            continue
        for r in rows:
            t = r["t_in_window_s"]
            if t > ENTRY_DEADLINE_S:
                continue
            p = r.get("p_up") or {}
            if not p.get("ok") or p.get("value") is None:
                continue
            bid, ask = r["up"]["bid"], r["up"]["ask"]
            if bid is None or ask is None or ask <= bid:
                continue
            b = int((r.get("index") or {}).get("bucket_t") or -1)
            i = pos.get(b)
            if i is None or i < 61:
                continue
            # SHORT-TERM MOMENTUM of the published average, in dollars.
            # A is a 60 s mean, so these are genuinely different horizons.
            mom10 = float(ia[i] - ia[i - 10])
            mom30 = float(ia[i] - ia[i - 30])
            mom60 = float(ia[i] - ia[i - 60])
            sd = float(p["sd"]) if p.get("sd") else None
            if not sd or sd <= 0:
                continue
            recs.append({
                "market": market, "t": t, "won_up": 1.0 if won == "UP" else 0.0,
                "p_model": float(p["value"]),
                "p_book": (bid + ask) / 2.0,
                "up_bid": bid, "up_ask": ask,
                "spread": ask - bid,
                "z": float(p["z"]),
                "dist_sd": float(p["distance_to_strike"]) / sd,
                "tau": float(p["tau_s"]),
                "sigma": float(p["sigma_per_second"] or 0.0),
                "sd": sd,
                "mom10_sd": mom10 / sd, "mom30_sd": mom30 / sd,
                "mom60_sd": mom60 / sd,
            })
    return recs


def split(recs):
    markets = sorted({r["market"] for r in recs})
    n = len(markets)
    tr = set(markets[: int(n * 0.6)])
    va = set(markets[int(n * 0.6): int(n * 0.8)])
    te = set(markets[int(n * 0.8):])
    g = lambda s: [r for r in recs if r["market"] in s]      # noqa: E731
    return g(tr), g(va), g(te), (len(tr), len(va), len(te))


# ----------------------------------------------------------------- scoring

def clip(p, eps=1e-6):
    return np.clip(p, eps, 1 - eps)


def logloss(y, p):
    p = clip(np.asarray(p, float))
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y, p):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def cluster_se(values, groups):
    """SE of a mean, clustered by group. Rows in one window share an
    outcome, so the window is the independent unit."""
    agg = collections.defaultdict(list)
    for v, g in zip(values, groups):
        agg[g].append(v)
    means = np.array([np.mean(v) for v in agg.values()])
    k = len(means)
    if k < 2:
        return float("nan"), k
    return float(np.std(means, ddof=1) / math.sqrt(k)), k


def reliability(y, p, bins=8, lo=0.0, hi=1.0):
    out = []
    edges = np.linspace(lo, hi, bins + 1)
    y, p = np.asarray(y, float), np.asarray(p, float)
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & (p < b)
        if m.sum() < 30:
            continue
        out.append((a, b, int(m.sum()), float(p[m].mean()), float(y[m].mean())))
    return out


# -------------------------------------------------------------- logistic

def fit_logistic(X, y, l2=1.0, iters=400):
    """Plain Newton-ish gradient descent. Few features, standardised."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    mu, sg = X.mean(0), X.std(0)
    sg[sg == 0] = 1.0
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sg])
    w = np.zeros(Z.shape[1])
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-Z @ w))
        g = Z.T @ (p - y) / len(y)
        g[1:] += l2 * w[1:] / len(y)
        H = (Z * (p * (1 - p))[:, None]).T @ Z / len(y)
        H[np.diag_indices_from(H)] += l2 / len(y) + 1e-6
        try:
            w -= np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            w -= 0.1 * g
    return {"w": w, "mu": mu, "sg": sg}


def apply_logistic(m, X):
    X = np.asarray(X, float)
    Z = np.hstack([np.ones((len(X), 1)), (X - m["mu"]) / m["sg"]])
    return 1.0 / (1.0 + np.exp(-Z @ w_of(m)))


def w_of(m):
    return m["w"]


def cols(recs, names):
    return np.array([[r[n] for n in names] for r in recs], float)


def y_of(recs):
    return np.array([r["won_up"] for r in recs], float)


# ------------------------------------------------------------------ main

def main():
    recs = build()
    tr, va, te, counts = split(recs)
    print("ROWS %d over %d windows   TRAIN %d / VAL %d / OOS %d windows"
          % (len(recs), len({r["market"] for r in recs}), *counts))
    print("rows: train %d  val %d  oos %d" % (len(tr), len(va), len(te)))
    print()

    # ---- 1/2. the two probabilities we already have
    print("=" * 78)
    print("1-2. IS THE INDEX MODEL CALIBRATED, AND BETTER THAN THE BOOK?")
    print("=" * 78)
    print("%-14s %10s %10s %10s %10s" % ("block", "logloss_M", "logloss_B",
                                         "brier_M", "brier_B"))
    for name, blk in (("train", tr), ("val", va), ("OOS", te)):
        y = y_of(blk)
        pm = [r["p_model"] for r in blk]
        pb = [r["p_book"] for r in blk]
        print("%-14s %10.4f %10.4f %10.4f %10.4f"
              % (name, logloss(y, pm), logloss(y, pb),
                 brier(y, pm), brier(y, pb)))
    print()
    print("reliability on OOS -- model says / side actually won")
    print("%-14s %7s %9s %9s %9s" % ("p bucket", "rows", "mean_p", "realised",
                                     "gap"))
    for a, b, n, mp, my in reliability(y_of(te), [r["p_model"] for r in te]):
        print("%-14s %7d %9.3f %9.3f %+9.3f"
              % ("%.2f-%.2f" % (a, b), n, mp, my, my - mp))
    print()
    print("same rows, the BOOK's own mid")
    for a, b, n, mp, my in reliability(y_of(te), [r["p_book"] for r in te]):
        print("%-14s %7d %9.3f %9.3f %+9.3f"
              % ("%.2f-%.2f" % (a, b), n, mp, my, my - mp))

    # ---- 3. do extra features add anything, fitted on train only?
    print()
    print("=" * 78)
    print("3. FITTED CANDIDATES -- train only, scored once on OOS")
    print("=" * 78)
    cands = {
        "book only": ["p_book"],
        "model only": ["p_model"],
        "model+book": ["p_model", "p_book"],
        "model+book+mom": ["p_model", "p_book", "mom10_sd", "mom30_sd",
                           "mom60_sd"],
        "model+book+mom+tau+sig": ["p_model", "p_book", "mom10_sd",
                                   "mom30_sd", "mom60_sd", "tau", "sigma",
                                   "spread"],
        "momentum only": ["mom10_sd", "mom30_sd", "mom60_sd"],
    }
    print("%-26s %10s %10s %10s" % ("features", "LL train", "LL val",
                                    "LL OOS"))
    fitted = {}
    for name, names in cands.items():
        m = fit_logistic(cols(tr, names), y_of(tr))
        fitted[name] = (m, names)
        print("%-26s %10.4f %10.4f %10.4f"
              % (name, logloss(y_of(tr), apply_logistic(m, cols(tr, names))),
                 logloss(y_of(va), apply_logistic(m, cols(va, names))),
                 logloss(y_of(te), apply_logistic(m, cols(te, names)))))
    print()
    m, names = fitted["model+book+mom"]
    print("fitted weights, model+book+mom (standardised units):")
    for n, wv in zip(["intercept"] + names, m["w"]):
        print("   %-12s %+8.4f" % (n, wv))
    print("   (a momentum weight near zero means momentum adds nothing"
          " once the model and the book are in)")

    # ---- 4. the disagreement signal, on the rows the policy would trade
    print()
    print("=" * 78)
    print("4. THE DISAGREEMENT SIGNAL AT TRADEABLE ROWS")
    print("=" * 78)
    print("favorite = the side the BOOK favours; price = that side's bid;")
    print("edge = model's probability for that side MINUS the price paid.")
    print()
    print("%-16s %8s %8s %10s %10s %9s %7s"
          % ("edge bucket", "rows", "wins", "win_rate", "avg_price",
             "net/share", "se"))
    for name, blk in (("VAL", va), ("OOS", te)):
        print("-- %s --" % name)
        buckets = collections.defaultdict(list)
        for r in blk:
            fav_up = r["p_book"] >= 0.5
            # the favorite's OWN bid: the price accumulation would pay
            price = r["up_bid"] if fav_up else 1.0 - r["up_ask"]
            if not (BAND_LO <= price <= BAND_HI):
                continue
            pm = r["p_model"] if fav_up else 1.0 - r["p_model"]
            won = r["won_up"] if fav_up else 1.0 - r["won_up"]
            edge = pm - price
            k = min(4, max(-2, int(math.floor(edge * 20))))
            buckets[k].append((won, price, won - price, r["market"]))
        for k in sorted(buckets):
            v = buckets[k]
            nets = [x[2] for x in v]
            se, nw = cluster_se(nets, [x[3] for x in v])
            print("%-16s %8d %8d %10.3f %10.3f %+9.4f %7.4f"
                  % ("%+.2f..%+.2f" % (k / 20.0, (k + 1) / 20.0), len(v),
                     int(sum(x[0] for x in v)),
                     float(np.mean([x[0] for x in v])),
                     float(np.mean([x[1] for x in v])),
                     float(np.mean(nets)), se))
    print()
    print("net/share here is settlement minus price at a NOTIONAL fill on")
    print("every eligible row -- it is the EV of the state, not of a fill.")
    print("se is clustered by window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
