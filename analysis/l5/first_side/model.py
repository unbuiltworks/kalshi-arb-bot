"""P0: what determines L5's FIRST side? Sequential, no outcome leakage.

THE TARGET. At each market opening the decision is binary: buy Up, or buy
Down. y = 1 iff the first fill is on Up.

THE ONE MOVE THAT MAKES THIS NON-CIRCULAR. We observe only the price of the
side he CHOSE, so using "chosen-side price" as a feature would encode the
answer. In a binary market the two outcomes price to ~1.00, so the market's
UP price is recoverable either way:

    up_price = fill_price          if he bought Up
             = 1 - fill_price      if he bought Down

That is a property of the MARKET, identical under both choices, and it is
what every price feature here is built from. No feature is a function of the
label.

NO OUTCOME LEAKAGE. Which side actually WON the current window never enters a
feature. Only the PREVIOUS window's resolution is used, and only where it
resolved strictly before the current first fill.

UNAVAILABLE IS NOT ZERO. The public activity tape carries L5's own fills, not
the book. Spread, depth, order flow and intra-window trajectory are therefore
NOT AVAILABLE historically and are reported as UNAVAILABLE rather than
imputed as zero -- a zero would be a measurement claim we cannot make.
"""
from __future__ import annotations

import collections
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from analysis.l5.redemption import audit as A          # noqa: E402

# Features the historical public tape CANNOT supply. Named so their absence is
# explicit in the report instead of silently becoming a zero column.
UNAVAILABLE_FEATURES = (
    "spread", "depth_at_touch", "order_flow_imbalance",
    "intra_window_trajectory", "book_updates", "queue_position",
)

BASELINES = ("CHEAPER", "DEARER", "UP", "DOWN", "FAVORITE", "UNDERDOG",
             "MOMENTUM", "MEAN_REVERSION", "RANDOM")


def build_rows() -> List[dict]:
    """One row per market opening, in chronological order."""
    first: Dict[str, tuple] = {}
    resolved: Dict[str, tuple] = {}      # cid -> (end_ts, winning_outcome)
    for r in A.iter_activity():
        t = str(r.get("type") or "").upper()
        cid = str(r.get("conditionId") or "")
        w = A.window_of(r)
        if t == "TRADE" and str(r.get("side") or "").upper() == "BUY":
            ts = int(r.get("timestamp") or 0)
            px = float(r.get("price") or 0)
            if px <= 0 or not w:
                continue
            cur = first.get(cid)
            if cur is None or ts < cur[0]:
                first[cid] = (ts, px, str(r.get("outcome")), w[0], w[1], w[2])
        elif t == "REDEEM" and w:
            ts = int(r.get("timestamp") or 0)
            prev = resolved.get(cid)
            if prev is None or ts < prev[0]:
                resolved[cid] = (w[2], str(r.get("outcome")))

    rows = []
    for cid, (ts, px, outcome, asset, horizon, end) in first.items():
        up_price = px if outcome == "Up" else round(1.0 - px, 6)
        rows.append({
            "cid": cid, "t": ts, "asset": asset, "horizon": horizon,
            "window_end": end,
            "y_up": 1 if outcome == "Up" else 0,
            "up_price": up_price,
            "fill_price": px,
            "cohort": f"{asset.upper()}{horizon}",
        })
    rows.sort(key=lambda r: r["t"])

    # --- temporally valid history: the PREVIOUS window of the SAME series ---
    last_by_series: Dict[str, dict] = {}
    for r in rows:
        s = r["cohort"]
        prev = last_by_series.get(s)
        # prior window counts only if it RESOLVED before this first fill
        pv = None
        if prev is not None:
            res = resolved.get(prev["cid"])
            if res and res[0] <= r["t"]:
                pv = {"prev_up_price": prev["up_price"],
                      "prev_y_up": prev["y_up"],
                      "prev_winner_up": 1 if res[1] == "Up" else 0,
                      "prev_gap_s": r["t"] - res[0]}
        r.update(pv or {"prev_up_price": None, "prev_y_up": None,
                        "prev_winner_up": None, "prev_gap_s": None})
        last_by_series[s] = r

    for r in rows:
        p = r["up_price"]
        r["dist_from_50"] = round(abs(p - 0.5), 6)
        r["tte_s"] = r["window_end"] - r["t"]
        r["hour_utc"] = (r["t"] // 3600) % 24
        r["band"] = ("<0.20" if p < 0.20 else "0.20-0.35" if p < 0.35 else
                     "0.35-0.50" if p < 0.50 else "0.50-0.65" if p < 0.65 else
                     "0.65-0.80" if p < 0.80 else ">=0.80")
        # movement of the SERIES, from the previous window's price. This is the
        # only movement signal the tape supports; intra-window trajectory is
        # UNAVAILABLE.
        r["prev_move"] = (None if r["prev_up_price"] is None
                          else round(p - r["prev_up_price"], 6))
    return rows


# ------------------------------------------------------------- baselines

def baseline_predict(name: str, r: dict, rng: random.Random) -> Optional[float]:
    p = r["up_price"]
    if name == "CHEAPER":                     # our production rule
        return 1.0 if p < 0.5 else 0.0
    if name in ("DEARER", "FAVORITE"):
        return 1.0 if p > 0.5 else 0.0
    if name == "UNDERDOG":
        return 1.0 if p < 0.5 else 0.0
    if name == "UP":
        return 1.0
    if name == "DOWN":
        return 0.0
    if name == "RANDOM":
        return rng.random()
    if name == "MOMENTUM":                    # series moved up -> buy Up
        m = r["prev_move"]
        return None if m is None else (1.0 if m > 0 else 0.0)
    if name == "MEAN_REVERSION":
        m = r["prev_move"]
        return None if m is None else (0.0 if m > 0 else 1.0)
    raise ValueError(name)


def _clip(p: float, eps: float = 1e-6) -> float:
    return min(1 - eps, max(eps, p))


def score(rows: List[dict], preds: List[Optional[float]]) -> dict:
    ok = [(r, p) for r, p in zip(rows, preds) if p is not None]
    if not ok:
        return {"n": 0, "accuracy": None, "log_loss": None, "coverage": 0.0}
    acc = sum(1 for r, p in ok if (p >= 0.5) == bool(r["y_up"])) / len(ok)
    ll = -sum(r["y_up"] * math.log(_clip(p)) +
              (1 - r["y_up"]) * math.log(1 - _clip(p)) for r, p in ok) / len(ok)
    return {"n": len(ok), "accuracy": round(acc, 4), "log_loss": round(ll, 4),
            "coverage": round(len(ok) / len(rows), 4)}


# ------------------------------- smallest interpretable model: logistic ----

FEATURES = ("up_price", "dist_from_50", "tte_norm", "is_eth", "is_15m",
            "prev_move", "prev_y_up", "prev_winner_up")


def featurize(r: dict) -> Optional[List[float]]:
    if r["prev_move"] is None:
        return None                      # needs a temporally valid predecessor
    dur = 300.0 if r["horizon"] == "5m" else 900.0
    return [r["up_price"], r["dist_from_50"],
            max(0.0, min(1.0, r["tte_s"] / dur)),
            1.0 if r["asset"] == "eth" else 0.0,
            1.0 if r["horizon"] == "15m" else 0.0,
            r["prev_move"], float(r["prev_y_up"]), float(r["prev_winner_up"])]


def fit_logistic(X: List[List[float]], y: List[int], epochs: int = 260,
                 lr: float = 0.35, l2: float = 1e-4) -> List[float]:
    d = len(X[0])
    w = [0.0] * (d + 1)
    mu = [sum(c) / len(X) for c in zip(*X)]
    sd = [max(1e-9, (sum((v - m) ** 2 for v in c) / len(X)) ** 0.5)
          for c, m in zip(zip(*X), mu)]
    Z = [[(v - m) / s for v, m, s in zip(row, mu, sd)] for row in X]
    for _ in range(epochs):
        g = [0.0] * (d + 1)
        for row, t in zip(Z, y):
            z = w[0] + sum(wi * v for wi, v in zip(w[1:], row))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - t
            g[0] += e
            for i, v in enumerate(row):
                g[i + 1] += e * v
        n = len(Z)
        w[0] -= lr * g[0] / n
        for i in range(1, d + 1):
            w[i] -= lr * (g[i] / n + l2 * w[i])
    return w + mu + sd


def predict_logistic(model: List[float], x: List[float]) -> float:
    d = len(x)
    w, mu, sd = model[:d + 1], model[d + 1:2 * d + 1], model[2 * d + 1:]
    z = w[0] + sum(wi * ((v - m) / s)
                   for wi, v, m, s in zip(w[1:], x, mu, sd))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def calibration(rows: List[dict], preds: List[float], bins: int = 5) -> list:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(r, p) for r, p in zip(rows, preds) if lo <= p < hi or
               (b == bins - 1 and p == 1.0)]
        if not sel:
            continue
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel),
                    "mean_pred": round(sum(p for _, p in sel) / len(sel), 4),
                    "observed_up": round(sum(r["y_up"] for r, _ in sel)
                                         / len(sel), 4)})
    return out
