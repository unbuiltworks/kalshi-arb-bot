#!/usr/bin/env python3
"""Chronological calibration of the BTC15m probability model. Research only.

Reads only committed corpora; places no orders and opens no socket.

  data/btc15m/coinbase_1m.jsonl.gz   the existing 1-minute BTC reference tape
  data/l5/raw/markets.jsonl          settlement truth + resolution source
  data/l5/raw/activity.jsonl         L5's executed prices = the market's own
                                     probability at the same instant

THE TEST THAT DECIDES ANYTHING. A probability model is only worth money if it
beats the price you would have to pay. So every statistic below is reported
three ways on the SAME observations: the model, the market-implied
probability from the most recent executed print at or before the decision,
and the constant 0.5. A model that beats 0.5 but not the market has found the
market's information, not an edge.

Observations are CLUSTERED BY WINDOW -- all 13 decision points in one window
share one outcome -- so every interval is a window-clustered interval and the
effective n is the window count, not the row count.

Run: PYTHONPATH=. python3 analysis/l5/btc15m_calibration_report.py
"""
from __future__ import annotations

import collections
import gzip
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.btc15m_asof import (SETTLE_AVERAGE_S, WINDOW_S, MinuteSeries,
                                     asof_snapshot, minutes_from_coinbase_rows,
                                     settled_value, strike)
from analysis.l5.btc15m_calibration import (WindowPrediction,
                                            chronological_gate)
from analysis.l5.btc15m_probability import estimate_up
from analysis.l5.favorite_accumulation import load_outcomes, load_trades

CANDLES = "data/btc15m/coinbase_1m.jsonl.gz"
MARKETS = "data/l5/raw/markets.jsonl"
PREFIX = "btc-updown-15m-"
#: Decision points: every minute from +60 s to the start of the terminal
#: average. +0 is excluded because the strike minute and the as-of minute
#: would be adjacent and the horizon carries no information yet.
OFFSETS = [60.0 * k for k in range(1, 14)]
TRAIN_FRACTION = 0.60


# ------------------------------------------------------------------ loading
def load_candles(path: str = CANDLES) -> MinuteSeries:
    rows = []
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return minutes_from_coinbase_rows(rows)


def load_sources(path: str = MARKETS) -> Dict[str, str]:
    """{slug: resolutionSource} for BTC15m windows."""
    out: Dict[str, str] = {}
    with open(path) as fh:
        for line in fh:
            try:
                env = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            rows = env.get("payload")
            if not isinstance(rows, list):
                continue
            for r in rows:
                if isinstance(r, dict) and str(r.get("slug") or "").startswith(PREFIX):
                    out[r["slug"]] = str(r.get("resolutionSource") or "")
    return out


# --------------------------------------------------------------- statistics
def _t_crit(n: int) -> float:
    if n < 2:
        return float("inf")
    return 1.96 + 2.4 / max(1.0, n - 1)


def clustered_mean(by_window: Dict[str, List[float]]) -> Tuple[float, float, int]:
    """Mean of per-window means, with its clustered standard error."""
    means = [sum(v) / len(v) for v in by_window.values() if v]
    n = len(means)
    if n == 0:
        return float("nan"), float("nan"), 0
    mu = sum(means) / n
    if n < 2:
        return mu, float("nan"), n
    var = sum((m - mu) ** 2 for m in means) / (n - 1)
    return mu, _t_crit(n) * math.sqrt(var / n), n


def brier(p: float, y: int) -> float:
    return (p - y) ** 2


def logloss(p: float, y: int) -> float:
    q = min(max(p, 1e-6), 1 - 1e-6)
    return -(math.log(q) if y else math.log(1 - q))


def auc(pairs: Sequence[Tuple[float, int]]) -> float:
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return float("nan")
    srt = sorted(pos + neg)
    ranks: Dict[float, float] = {}
    i = 0
    while i < len(srt):
        j = i
        while j + 1 < len(srt) and srt[j + 1] == srt[i]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        ranks[srt[i]] = r
        i = j + 1
    s = sum(ranks[p] for p in pos)
    return (s - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


# ------------------------------------------------------------ recalibration
def fit_logistic(obs: Sequence[Tuple[float, int]], iters: int = 400) -> Tuple[float, float]:
    """Platt scaling on the logit: y ~ sigmoid(a + b * logit(p)). Train only."""
    xs = [math.log(min(max(p, 1e-6), 1 - 1e-6) / (1 - min(max(p, 1e-6), 1 - 1e-6)))
          for p, _ in obs]
    ys = [float(y) for _, y in obs]
    a, b = 0.0, 1.0
    n = len(xs)
    if n < 20:
        return a, b
    lr = 0.05
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in zip(xs, ys):
            z = a + b * x
            s = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            ga += (s - y)
            gb += (s - y) * x
        a -= lr * ga / n
        b -= lr * gb / n
    return a, b


def apply_logistic(p: float, a: float, b: float) -> float:
    q = min(max(p, 1e-6), 1 - 1e-6)
    z = a + b * math.log(q / (1 - q))
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


# ------------------------------------------------------------------- build
def build_observations(series: MinuteSeries, outcomes: Dict[str, str],
                       trades: List[Dict[str, Any]],
                       sources: Dict[str, str]) -> Tuple[List[Dict], Dict[str, int]]:
    """One row per (window, decision offset) that every gate accepts."""
    prints: Dict[str, List[Tuple[float, float]]] = collections.defaultdict(list)
    for t in trades:
        p_up = t["px"] if t["outcome"] == "Up" else 1.0 - t["px"]
        prints[t["slug"]].append((t["ws"] + t["t_off"], p_up))
    for v in prints.values():
        v.sort()

    rows: List[Dict] = []
    refused: Dict[str, int] = collections.Counter()
    for slug, winner in sorted(outcomes.items(), key=lambda kv: kv[0]):
        try:
            ws = float(slug.rsplit("-", 1)[1])
        except Exception:                                      # noqa: BLE001
            refused["bad_slug"] += 1
            continue
        src = sources.get(slug, "")
        try:
            k = strike(series, ws, src)
            sv = settled_value(series, ws, src)
        except ValueError as exc:
            refused["reference_%s" % str(exc).split()[0]] += 1
            continue
        y = 1 if winner == "Up" else 0
        proxy_y = 1 if sv >= k else 0
        for off in OFFSETS:
            try:
                snap = asof_snapshot(series, ws, ws + off,
                                     resolution_source=src)
                p = estimate_up(snap)
            except ValueError as exc:
                refused["snapshot_%s" % str(exc).split()[0]] += 1
                continue
            # TWO price bases, and the difference between them is the whole
            # point. `mkt` is the most recent print AT OR BEFORE the decision
            # -- that is a price that has already happened, and an "edge"
            # measured against it is partly just the price having moved since
            # it printed. `mkt_next` is the first print AT OR AFTER the
            # decision: a price that was actually still to come, and the only
            # one a forward order could have met. Median staleness of `mkt` is
            # 6 s, p90 36 s, so the two are materially different.
            mkt = mkt_age = None
            mkt_next = mkt_next_age = None
            for ts, pu in prints.get(slug, ()):
                if ts <= ws + off:
                    mkt, mkt_age = pu, (ws + off) - ts
                else:
                    mkt_next, mkt_next_age = pu, ts - (ws + off)
                    break
            rows.append({"slug": slug, "ws": ws, "off": off, "y": y,
                         "proxy_y": proxy_y, "p": p, "mkt": mkt,
                         "mkt_age": mkt_age, "mkt_next": mkt_next,
                         "mkt_next_age": mkt_next_age,
                         "spot": snap.spot, "strike": k,
                         "sigma": snap.sigma_per_sqrt_second,
                         "source": src})
    return rows, dict(refused)


# ------------------------------------------------------------------ report
def score(rows: Sequence[Dict], key: str) -> Dict[str, Any]:
    use = [r for r in rows if r.get(key) is not None]
    if not use:
        return {"n": 0}
    bw: Dict[str, List[float]] = collections.defaultdict(list)
    lw: Dict[str, List[float]] = collections.defaultdict(list)
    hw: Dict[str, List[float]] = collections.defaultdict(list)
    for r in use:
        bw[r["slug"]].append(brier(r[key], r["y"]))
        lw[r["slug"]].append(logloss(r[key], r["y"]))
        side = 1 if r[key] >= 0.5 else 0
        hw[r["slug"]].append(1.0 if side == r["y"] else 0.0)
    b, bci, nw = clustered_mean(bw)
    l, lci, _ = clustered_mean(lw)
    h, hci, _ = clustered_mean(hw)
    return {"n": len(use), "windows": nw,
            "brier": b, "brier_ci": bci,
            "logloss": l, "logloss_ci": lci,
            "hit": h, "hit_ci": hci,
            "auc": auc([(r[key], r["y"]) for r in use])}


def executable_edge(rows: Sequence[Dict], *, price_key: str, age_key: str,
                    max_age: Optional[float], threshold: float,
                    margin: float) -> Dict[str, Any]:
    """Net per contract from buying the side the model favours.

    Payoff is the settled $1/$0, cost is the executed print, so this is a
    GROSS trading edge before fees, queue position and adverse selection. It
    is a replay number, never a confirmed fill.
    """
    per: Dict[str, List[float]] = collections.defaultdict(list)
    for r in rows:
        px_up = r.get(price_key)
        if px_up is None:
            continue
        if max_age is not None:
            age = r.get(age_key)
            if age is None or age > max_age:
                continue
        for side in ("Up", "Down"):
            p = r["p"] if side == "Up" else 1.0 - r["p"]
            px = px_up if side == "Up" else 1.0 - px_up
            if not 0.0 < px < 1.0:
                continue
            if max(0.0, p - margin) - px < threshold:
                continue
            won = (r["y"] == 1) if side == "Up" else (r["y"] == 0)
            per[r["slug"]].append((1.0 if won else 0.0) - px)
    mu, ci, n = clustered_mean(per)
    return {"rows": sum(len(v) for v in per.values()), "windows": n,
            "net_per_contract": mu, "ci": ci}


def reliability(rows: Sequence[Dict], key: str, edges: Sequence[float]) -> List[Dict]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [r for r in rows if r.get(key) is not None and lo <= r[key] < hi]
        if not sel:
            continue
        out.append({"lo": lo, "hi": hi, "n": len(sel),
                    "mean_p": sum(r[key] for r in sel) / len(sel),
                    "realized": sum(r["y"] for r in sel) / len(sel),
                    "windows": len({r["slug"] for r in sel})})
    return out


def main() -> int:
    series = load_candles()
    outcomes = load_outcomes()
    sources = load_sources()
    trades = load_trades(outcomes)
    rows, refused = build_observations(series, outcomes, trades, sources)
    if not rows:
        print("no observations survived the gates:", refused)
        return 1

    wins = sorted({(r["ws"], r["slug"]) for r in rows})
    cut = wins[int(len(wins) * TRAIN_FRACTION)][0] if len(wins) > 2 else wins[0][0]
    train = [r for r in rows if r["ws"] < cut]
    test = [r for r in rows if r["ws"] >= cut]

    a, b = fit_logistic([(r["p"], r["y"]) for r in train])
    for r in rows:
        r["cal"] = apply_logistic(r["p"], a, b)

    out: Dict[str, Any] = {
        "provenance": {
            "candles": CANDLES, "candles_minutes": len(series),
            "markets": MARKETS,
            "candle_source": "Coinbase 1m, PROXY -- settlement is Chainlink",
            "settlement_functional": "trailing-60s mean at both endpoints",
        },
        "observations": len(rows), "windows": len(wins),
        "refused": refused,
        "sources": dict(collections.Counter(r["source"].split("/")[-1] for r in rows)),
        "split": {"train_windows": len({r["slug"] for r in train}),
                  "test_windows": len({r["slug"] for r in test}),
                  "cut_ws": cut},
        "platt": {"a": a, "b": b},
        "proxy_fidelity": None,
        "scores": {},
        "reliability": {},
        "by_offset": {},
    }

    pf: Dict[str, List[float]] = collections.defaultdict(list)
    for r in rows:
        pf[r["slug"]].append(1.0 if r["proxy_y"] == r["y"] else 0.0)
    fid, fci, fn = clustered_mean(pf)
    out["proxy_fidelity"] = {"agreement": fid, "ci": fci, "windows": fn}

    for name, subset in (("all", rows), ("train", train), ("test", test)):
        out["scores"][name] = {
            "model": score(subset, "p"),
            "calibrated": score(subset, "cal"),
            "market": score(subset, "mkt"),
            "half": score([dict(r, half=0.5) for r in subset], "half"),
        }
    # Paired, window-clustered difference against the market. This, not the
    # absolute Brier, is what says whether the model knows anything the price
    # does not.
    out["paired_vs_market"] = {}
    for name, subset in (("train", train), ("test", test), ("all", rows)):
        m = [r for r in subset if r["mkt"] is not None]
        db: Dict[str, List[float]] = collections.defaultdict(list)
        dl: Dict[str, List[float]] = collections.defaultdict(list)
        for r in m:
            db[r["slug"]].append(brier(r["p"], r["y"]) - brier(r["mkt"], r["y"]))
            dl[r["slug"]].append(logloss(r["p"], r["y"]) - logloss(r["mkt"], r["y"]))
        # NOT `a`/`b`: those hold the Platt fit and shadowing them here
        # corrupted the reported calibration coefficients.
        db_mu, db_ci, nw = clustered_mean(db)
        dl_mu, dl_ci, _ = clustered_mean(dl)
        out["paired_vs_market"][name] = {"d_brier": db_mu, "d_brier_ci": db_ci,
                                         "d_logloss": dl_mu,
                                         "d_logloss_ci": dl_ci,
                                         "windows": nw}

    out["executable_edge"] = {}
    bases = (("stale_prev_print", "mkt", "mkt_age", None),
             ("prev_print_age_le_5s", "mkt", "mkt_age", 5.0),
             ("forward_next_print", "mkt_next", "mkt_next_age", None),
             ("forward_next_within_30s", "mkt_next", "mkt_next_age", 30.0))
    for name, subset in (("train", train), ("test", test)):
        out["executable_edge"][name] = {}
        for label, pk, ak, ma in bases:
            out["executable_edge"][name][label] = executable_edge(
                subset, price_key=pk, age_key=ak, max_age=ma,
                threshold=0.05, margin=0.0)

    ages = sorted(r["mkt_age"] for r in rows if r["mkt_age"] is not None)
    if ages:
        out["print_staleness_seconds"] = {
            "n": len(ages), "median": ages[len(ages) // 2],
            "p90": ages[int(0.9 * len(ages))], "max": ages[-1]}

    # INDEPENDENT CROSS-CHECK through the owner's gate in
    # analysis/l5/btc15m_calibration.py, which takes exactly ONE prediction
    # per window and freezes the split itself. Run once per decision offset,
    # with the MARKET PRICE as the baseline, so the question it answers is
    # "does the model beat the price out of sample" rather than "does it beat
    # a coin flip". Reported separately from my own window-clustered paired
    # statistic because the two are computed differently and agreeing is
    # evidence; the gate is strict about chronology and refuses rather than
    # reporting when a split is too thin.
    out["owner_gate_by_offset"] = {}
    for off in OFFSETS:
        sub = sorted((r for r in rows
                      if r["off"] == off and r["mkt"] is not None),
                     key=lambda r: r["ws"])
        cand = [WindowPrediction(window_start=int(r["ws"]),
                                 predicted_up=r["p"],
                                 settled_up=bool(r["y"])) for r in sub]
        base = [WindowPrediction(window_start=int(r["ws"]),
                                 predicted_up=r["mkt"],
                                 settled_up=bool(r["y"])) for r in sub]
        try:
            g = chronological_gate(cand, base)
        except ValueError as exc:
            out["owner_gate_by_offset"][int(off)] = {"refused": str(exc),
                                                     "windows": len(sub)}
            continue
        out["owner_gate_by_offset"][int(off)] = {
            "test_windows": g["test_windows"],
            "brier_improvement": g["brier_improvement"],
            "log_loss_improvement": g["log_loss_improvement"],
            "forecast_better_on_both_scores":
                g["forecast_better_on_both_scores"],
            "candidate_bias": g["candidate"]["mean_bias"],
            "baseline_bias": g["baseline"]["mean_bias"]}

    edges = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]
    out["reliability"]["model_test"] = reliability(test, "p", edges)
    out["reliability"]["calibrated_test"] = reliability(test, "cal", edges)
    out["reliability"]["market_test"] = reliability(test, "mkt", edges)
    for off in OFFSETS:
        sub = [r for r in test if r["off"] == off]
        if sub:
            out["by_offset"][int(off)] = {
                "model": score(sub, "p"), "calibrated": score(sub, "cal"),
                "market": score(sub, "mkt"), "n": len(sub)}

    os.makedirs("analysis/l5/btc15m_signal", exist_ok=True)
    with open("analysis/l5/btc15m_signal/calibration.json", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)

    def f(x: Any, nd: int = 4) -> str:
        return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else "%.*f" % (nd, x)

    print("observations %d over %d windows   sources %s" %
          (out["observations"], out["windows"], out["sources"]))
    print("refused: %s" % out["refused"])
    print("proxy reference fidelity (proxy sign vs Chainlink resolution): "
          "%s +/- %s over %d windows" % (f(fid), f(fci), fn))
    print("chronological split: train %d windows, test %d windows" %
          (out["split"]["train_windows"], out["split"]["test_windows"]))
    print("Platt on train: a=%s b=%s" % (f(a), f(b)))
    print()
    hdr = "%-12s %-10s %7s %7s %9s %9s %8s"
    print(hdr % ("subset", "predictor", "n", "windows", "brier", "logloss", "hit"))
    for name in ("train", "test"):
        for key in ("model", "calibrated", "market", "half"):
            s = out["scores"][name][key]
            if not s.get("n"):
                continue
            print(hdr % (name, key, s["n"], s["windows"], f(s["brier"]),
                         f(s["logloss"]), f(s["hit"], 3)))
    print()
    print("PAIRED vs the market price (negative = model better), window-clustered")
    for name in ("train", "test", "all"):
        d = out["paired_vs_market"][name]
        print("  %-6s dBrier %+.5f +/- %.5f   dLogloss %+.5f +/- %.5f   windows %d"
              % (name, d["d_brier"], d["d_brier_ci"], d["d_logloss"],
                 d["d_logloss_ci"], d["windows"]))
    st = out.get("print_staleness_seconds")
    if st:
        print()
        print("staleness of the 'current' print: median %.0fs p90 %.0fs max %.0fs"
              % (st["median"], st["p90"], st["max"]))
    print()
    print("GROSS executable edge, buy the model's side at edge>=0.05 (before fees)")
    eh = "  %-6s %-26s %6s %7s %13s %9s"
    print(eh % ("subset", "price basis", "rows", "windows", "net/contract", "+/- 95%"))
    for name in ("train", "test"):
        for label in ("stale_prev_print", "prev_print_age_le_5s",
                      "forward_next_print", "forward_next_within_30s"):
            e = out["executable_edge"][name][label]
            print(eh % (name, label, e["rows"], e["windows"],
                        f(e["net_per_contract"]), f(e["ci"])))
    print()
    print("OWNER GATE (btc15m_calibration.chronological_gate), model vs the")
    print("market price, one prediction per window, per decision offset:")
    gh = "  %-5s %6s %12s %12s %7s"
    print(gh % ("off", "testWin", "dBrier", "dLogLoss", "better"))
    better = 0
    ran = 0
    for off in sorted(out["owner_gate_by_offset"], key=int):
        g = out["owner_gate_by_offset"][off]
        if "refused" in g:
            print("  %-5s refused: %s (%d windows)"
                  % (off, g["refused"], g["windows"]))
            continue
        ran += 1
        better += bool(g["forecast_better_on_both_scores"])
        print(gh % (off, g["test_windows"], f(g["brier_improvement"], 5),
                    f(g["log_loss_improvement"], 5),
                    "yes" if g["forecast_better_on_both_scores"] else "no"))
    if ran:
        print("  model better on BOTH scores at %d of %d offsets" % (better, ran))
    print()
    print("HELD-OUT reliability, model vs market")
    print("%-12s %5s %5s %9s %9s" % ("bucket", "n", "wins", "mean_p", "realized"))
    for nm in ("model_test", "market_test"):
        print(" -- %s" % nm)
        for bkt in out["reliability"][nm]:
            print("%-12s %5d %5d %9s %9s" %
                  ("[%.1f,%.1f)" % (bkt["lo"], bkt["hi"]), bkt["n"],
                   bkt["windows"], f(bkt["mean_p"], 3), f(bkt["realized"], 3)))
    print()
    print("wrote analysis/l5/btc15m_signal/calibration.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
