"""Reference->prediction lag study (read-only, evidence for V3 promotion).

Consumes data/ref_lag/reflag_*.jsonl and answers, with causal discipline:
when model fair value moves materially, does the prediction book leave stale,
EXECUTABLE quotes behind — for how long, at what depth, and does taking them
win at settlement?

Causality rules:
- Fair value at time t uses only spot trades with t_ns <= t.
- A "signal" fires when fair p moves >= SIGNAL_DELTA_P from the last signal
  baseline within one window.
- For each horizon h (10ms..1s), the book state used is the last bookTicker
  update at or before t_signal + h: i.e. the quote that would still be resting
  when an order sent at t_signal arrives h later. Depth recorded from that
  same update. No future information is used at decision time.
- Settlement is scored from the spot tape itself: the 60s-RFR proxy at expiry
  (mean of spot trades over the final 60s) vs strike. This mirrors the actual
  settlement construction; flagged as proxy since the exact Kaiko feed is not
  public.

Simulated executions are IOC takes of stale quotes (edge >= TAKE_EDGE_P at
h = 250ms), sized at min(displayed depth, Q_CAP). Zero fees modeled (the
Sept 2 maker waiver does not cover takers — flagged; edge threshold should
absorb realistic taker fees).

Usage: python3 -m src.gemini_maker.lag_study
"""
from __future__ import annotations

import glob
import json
import math
import statistics
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.gemini_maker.fair_value import fair_value, realized_vol_per_sqrt_s

OUT_PATH = Path("analysis/gemini/ref_lag_study.json")

HORIZONS_MS = (10, 25, 50, 100, 250, 500, 1000)
SIGNAL_DELTA_P = 0.05
STALE_EDGE_P = 0.03        # quote counted stale if fair-vs-quote gap >= this
TAKE_EDGE_P = 0.05         # simulated take threshold at the 250ms horizon
TAKE_HORIZON_MS = 250
Q_CAP = 10.0
VOL_TRAIL_S = 300.0
MARKET_RE_ASSET = {"BTC": "BTCUSD", "ETH": "ETHUSD", "SOL": "SOLUSD",
                   "XRP": "XRPUSD"}


def _load(tape_dir: str) -> Dict[str, Any]:
    spot: Dict[str, List[Tuple[float, float]]] = defaultdict(list)  # (t_s, px)
    books: Dict[str, List[Tuple[int, dict]]] = defaultdict(list)
    windows: Dict[str, dict] = {}
    for path in sorted(glob.glob(f"{tape_dir}/reflag_*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = e.get("kind")
                if kind == "spot_trade":
                    spot[e["sym"]].append((e["t_ns"] / 1e9, float(e["price"])))
                elif kind == "pbook":
                    try:
                        row = {"b": float(e["b"]), "B": float(e["B"]),
                               "a": float(e["a"]), "A": float(e["A"])}
                    except (TypeError, ValueError):
                        continue
                    books[e["market"]].append((e["t_ns"], row))
                elif kind == "window":
                    windows[e["market"]] = e
    for sym in spot:
        spot[sym].sort()
    for m in books:
        books[m].sort(key=lambda r: r[0])
    return {"spot": spot, "books": books, "windows": windows}


def _book_at(rows: List[Tuple[int, dict]], keys: List[int],
             t_ns: int) -> Optional[dict]:
    i = bisect_right(keys, t_ns) - 1
    return rows[i][1] if i >= 0 else None


def _asset(market: str) -> Optional[str]:
    for k, v in MARKET_RE_ASSET.items():
        if market.startswith(f"GEMI-{k}"):
            return v
    return None


def _iso_ts(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _fair_at(spot_rows: List[Tuple[float, float]], t_s: float, strike: float,
             expiry_s: float) -> Optional[Any]:
    i = bisect_right(spot_rows, (t_s, float("inf"))) - 1
    if i < 5:
        return None
    spot_now = spot_rows[i][1]
    trail = [r for r in spot_rows[max(0, i - 400):i + 1] if r[0] >= t_s - VOL_TRAIL_S]
    sigma = realized_vol_per_sqrt_s(trail)
    if sigma is None:
        return None
    tau = expiry_s - t_s
    realized_mean = None
    coverage = 0.0
    if tau < 60.0:
        win = [p for (tt, p) in trail if expiry_s - 60.0 <= tt <= t_s]
        if win:
            realized_mean = sum(win) / len(win)
            coverage = min(t_s - (expiry_s - 60.0), 60.0 - tau)
    return fair_value(strike=strike, spot_now=spot_now, seconds_to_expiry=tau,
                      sigma=sigma, realized_mean=realized_mean,
                      realized_coverage_s=coverage)


def _settle_proxy(spot_rows: List[Tuple[float, float]], expiry_s: float,
                  strike: float) -> Optional[str]:
    win = [p for (t, p) in spot_rows if expiry_s - 60.0 <= t <= expiry_s]
    if len(win) < 3:
        return None
    return "yes" if (sum(win) / len(win)) >= strike else "no"


def run(tape_dir: str = "data/ref_lag") -> Dict[str, Any]:
    data = _load(tape_dir)
    signals: List[dict] = []
    takes: List[dict] = []
    calib: List[dict] = []
    windows_seen = 0
    for market, meta in sorted(data["windows"].items()):
        asset = _asset(market)
        if asset is None or asset not in data["spot"]:
            continue
        try:
            strike = float(meta.get("strike"))
        except (TypeError, ValueError):
            continue
        expiry_s = _iso_ts(meta.get("expiry"))
        effective_s = _iso_ts(meta.get("effective")) or (expiry_s - 900)
        if expiry_s is None:
            continue
        brows = data["books"].get(market, [])
        if not brows:
            continue
        bkeys = [t for t, _ in brows]
        spot_rows = data["spot"][asset]
        windows_seen += 1
        settle = _settle_proxy(spot_rows, expiry_s, strike)

        baseline_p: Optional[float] = None
        last_take_t = 0.0
        i0 = bisect_right(spot_rows, (effective_s, float("inf")))
        i1 = bisect_right(spot_rows, (expiry_s, float("inf")))
        for (t_s, _px) in spot_rows[i0:i1]:
            fv = _fair_at(spot_rows, t_s, strike, expiry_s)
            if fv is None:
                continue
            p = fv.p_yes
            if baseline_p is None:
                baseline_p = p
                continue
            if abs(p - baseline_p) < SIGNAL_DELTA_P:
                continue
            baseline_p = p
            t_sig_ns = int(t_s * 1e9)
            sig = {"market": market, "t": t_s, "p": round(p, 4),
                   "tte": round(expiry_s - t_s, 1), "stale": {}}
            for h in HORIZONS_MS:
                bk = _book_at(brows, bkeys, t_sig_ns + h * 1_000_000)
                if bk is None:
                    continue
                buy_edge = p - bk["a"]     # fair above ask: buy YES cheap
                sell_edge = bk["b"] - p    # bid above fair: sell YES rich
                if buy_edge >= STALE_EDGE_P:
                    sig["stale"][h] = {"side": "buy", "edge": round(buy_edge, 4),
                                       "px": bk["a"], "depth": bk["A"]}
                elif sell_edge >= STALE_EDGE_P:
                    sig["stale"][h] = {"side": "sell", "edge": round(sell_edge, 4),
                                       "px": bk["b"], "depth": bk["B"]}
            signals.append(sig)
            if settle is not None:
                calib.append({"p": round(p, 3), "tte": round(expiry_s - t_s, 1),
                              "outcome": 1 if settle == "yes" else 0})
            # Simulated strict IOC take at the 250ms horizon, one per episode
            # (30s cooldown per market so a burst can't be triple-counted).
            st = sig["stale"].get(TAKE_HORIZON_MS)
            if (st and st["edge"] >= TAKE_EDGE_P and settle is not None
                    and t_s - last_take_t >= 30.0):
                last_take_t = t_s
                qty = min(st["depth"], Q_CAP)
                if st["side"] == "buy":
                    pnl = qty * ((1.0 if settle == "yes" else 0.0) - st["px"])
                else:
                    pnl = qty * (st["px"] - (1.0 if settle == "yes" else 0.0))
                takes.append({"market": market, "t": t_s, "side": st["side"],
                              "px": st["px"], "qty": qty,
                              "edge": st["edge"], "settle": settle,
                              "pnl": round(pnl, 4)})
    stale_by_h = {h: sum(1 for s in signals if h in s["stale"])
                  for h in HORIZONS_MS}
    take_pnl = sum(t["pnl"] for t in takes)
    take_notional = sum(t["qty"] * (t["px"] if t["side"] == "buy" else 1 - t["px"])
                        for t in takes)
    agg = {
        "evidence_class": "READ_ONLY_LAG_STUDY_SETTLE_PROXY",
        "windows_analyzed": windows_seen,
        "signals": len(signals),
        "stale_executable_by_horizon_ms": stale_by_h,
        "stale_share_at_250ms": round(stale_by_h.get(250, 0) / len(signals), 3)
        if signals else None,
        "simulated_takes": len(takes),
        "take_pnl": round(take_pnl, 2),
        "take_notional": round(take_notional, 2),
        "take_pnl_per_dollar": round(take_pnl / take_notional, 4)
        if take_notional else None,
        "median_take_edge": round(statistics.median(t["edge"] for t in takes), 4)
        if takes else None,
    }
    # Model calibration: within each predicted-p bucket, how often did YES
    # actually settle? A well-calibrated model matches bucket midpoints; an
    # overconfident one shows outcome rates pulled toward 0.5.
    buckets: Dict[str, List[int]] = defaultdict(list)
    for c in calib:
        buckets[f"{int(c['p'] * 10) / 10:.1f}"].append(c["outcome"])
    agg["calibration"] = {
        k: {"n": len(v), "outcome_rate": round(sum(v) / len(v), 3)}
        for k, v in sorted(buckets.items())
    }
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "params": {"signal_delta_p": SIGNAL_DELTA_P, "stale_edge_p": STALE_EDGE_P,
                   "take_edge_p": TAKE_EDGE_P, "take_horizon_ms": TAKE_HORIZON_MS,
                   "q_cap": Q_CAP, "fees_modeled": "none (flagged)"},
        "aggregate": agg,
        "takes": takes[:200],
        "signals_sample": signals[:100],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(agg, indent=1))
    return payload


if __name__ == "__main__":
    run()
