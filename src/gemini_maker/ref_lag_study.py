"""Reference-lag study: does the prediction book lag reference fair value?

Consumes reflag_*.jsonl from ref_lag_recorder. For every SIGNAL (a material
fair-value change driven by the spot/RFR-proxy path) it asks, causally:

  - what quote was standing STRICTLY BEFORE the signal finished forming;
  - was that quote still standing 10/25/50/100/250/500/1000 ms AFTER the
    signal (executable duration, displayed depth);
  - was taking it +EV after fees against model fair value;
  - what was the markout at 100ms/250ms/500ms/1s;
  - settlement-scored P&L (settlement proxied by the RFR estimate at expiry —
    settlement IS the 60s reference rate, which the recorder approximates).

No edge is claimed unless the stale quote existed after the signal at
recorded depth. Conservative by construction; taker fee is a parameter
(default 2% of notional) until the real schedule is fetched.

Fair-value model (v1, deliberately simple):
  P(Up) = Phi( (rfr_now - strike) / (sigma_spot * sqrt(tau)) )
  sigma from trailing 5-min spot log-returns; tau = seconds to expiry.
Model error is acceptable: we measure BOOK REACTION LAG to fair-value
changes, not absolute mispricing.

Usage: python3 -m src.gemini_maker.ref_lag_study [--data-dir data/ref_lag]
"""
from __future__ import annotations

import argparse
import glob
import json
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OUT_PATH = Path("analysis/gemini/ref_lag_study.json")

HORIZONS_MS = (10, 25, 50, 100, 250, 500, 1000)
SIGNAL_MIN_PROB_MOVE = 0.05     # fair prob must move >= 5c to count as a signal
SIGNAL_COOLDOWN_S = 5.0         # min spacing between signals per window
MIN_EDGE = 0.03                 # required edge vs stale quote after fees
TAKER_FEE_RATE = 0.02           # of notional; parameter until real schedule known
VOL_WINDOW_S = 300.0
MIN_TAU_S = 20.0                # ignore signals in the last 20s (no fresh risk)


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def load(data_dir: str) -> Tuple[Dict[str, List], Dict[str, List], Dict[str, dict], Dict[str, List]]:
    """Returns (spot_by_asset, pbook_by_market, meta_by_market, rfr_by_asset)."""
    spot: Dict[str, List] = defaultdict(list)     # (t_s, price)
    rfr: Dict[str, List] = defaultdict(list)      # (t_s, rfr)
    pbook: Dict[str, List] = defaultdict(list)    # (t_s, bid, bsz, ask, asz)
    meta: Dict[str, dict] = {}
    for path in sorted(glob.glob(f"{data_dir}/reflag_*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = e.get("kind")
                t_s = (e.get("t_ns") or 0) / 1e9
                if kind == "spot_trade":
                    spot[e["sym"]].append((t_s, float(e["price"])))
                elif kind == "spot_rfr":
                    rfr[e["sym"]].append((t_s, float(e["rfr"])))
                elif kind == "pbook":
                    try:
                        pbook[e["market"]].append((
                            t_s,
                            float(e["b"]) if e.get("b") else None,
                            float(e["B"]) if e.get("B") else 0.0,
                            float(e["a"]) if e.get("a") else None,
                            float(e["A"]) if e.get("A") else 0.0,
                        ))
                    except (TypeError, ValueError):
                        continue
                elif kind == "window":
                    m = e.get("market")
                    if m and e.get("strike"):
                        try:
                            meta[m] = {
                                "strike": float(e["strike"]),
                                "expiry": datetime.fromisoformat(
                                    str(e["expiry"]).replace("Z", "+00:00")).timestamp(),
                                "source": e.get("source"),
                            }
                        except (TypeError, ValueError):
                            continue
    for d in (spot, rfr, pbook):
        for k in d:
            d[k].sort()
    return spot, pbook, meta, rfr


def asset_of(market: str) -> str:
    for a in ("BTC", "ETH", "SOL", "XRP"):
        if a in market:
            return f"{a}USD"
    return ""


class SpotModel:
    """Trailing RFR proxy + realized vol from one asset's spot trades."""

    def __init__(self, trades: List[Tuple[float, float]]) -> None:
        self.t = [x[0] for x in trades]
        self.p = [x[1] for x in trades]

    def rfr_at(self, t: float) -> Optional[float]:
        hi = bisect_right(self.t, t)
        lo = bisect_left(self.t, t - 60.0)
        if hi <= lo:
            return None
        window = self.p[lo:hi]
        return sum(window) / len(window)

    def sigma_at(self, t: float) -> Optional[float]:
        """Per-sqrt-second vol of the RFR-relevant spot process."""
        hi = bisect_right(self.t, t)
        lo = bisect_left(self.t, t - VOL_WINDOW_S)
        if hi - lo < 10:
            return None
        rets = []
        for i in range(lo + 1, hi):
            dt = self.t[i] - self.t[i - 1]
            if dt <= 0:
                continue
            r = math.log(self.p[i] / self.p[i - 1])
            rets.append(r * r / dt)
        if not rets:
            return None
        var_per_s = sum(rets) / len(rets)
        return math.sqrt(max(var_per_s, 1e-18))


def fair_prob(model: SpotModel, strike: float, t: float, expiry: float) -> Optional[float]:
    tau = expiry - t
    if tau < MIN_TAU_S:
        return None
    rfr = model.rfr_at(t)
    sigma = model.sigma_at(t)
    if rfr is None or sigma is None or rfr <= 0:
        return None
    denom = sigma * math.sqrt(tau) * rfr  # convert log-vol to price units
    if denom <= 0:
        return None
    return _phi((rfr - strike) / denom)


def study_window(market: str, ticks: List, meta: dict, model: SpotModel) -> List[dict]:
    strike, expiry = meta["strike"], meta["expiry"]
    tick_t = [x[0] for x in ticks]
    signals: List[dict] = []
    last_signal_t = 0.0
    last_prob: Optional[float] = None
    # scan on a 100ms grid over the window's recorded span
    if not ticks:
        return signals
    t = ticks[0][0]
    end = min(expiry, ticks[-1][0])
    settle_rfr = model.rfr_at(expiry) or model.rfr_at(end)
    settled_up = (settle_rfr >= strike) if settle_rfr is not None else None
    while t < end:
        prob = fair_prob(model, strike, t, expiry)
        if prob is not None and last_prob is not None \
                and abs(prob - last_prob) >= SIGNAL_MIN_PROB_MOVE \
                and t - last_signal_t >= SIGNAL_COOLDOWN_S:
            sig = _score_signal(market, t, prob, ticks, tick_t, settled_up)
            if sig:
                signals.append(sig)
                last_signal_t = t
        if prob is not None:
            last_prob = prob
        t += 0.1
    return signals


def _score_signal(market: str, t0: float, prob: float, ticks: List,
                  tick_t: List[float], settled_up: Optional[bool]) -> Optional[dict]:
    idx = bisect_right(tick_t, t0) - 1
    if idx < 0:
        return None
    _, bid, bsz, ask, asz = ticks[idx]
    # Direction: fair says Up worth `prob`. Stale ask below fair => buy Up.
    # Stale bid above fair => effectively buy Down (sell Up).
    action = None
    edge = 0.0
    if ask is not None and prob - ask - TAKER_FEE_RATE >= MIN_EDGE and asz > 0:
        action, price, size, edge = "buy_up", ask, asz, prob - ask - TAKER_FEE_RATE
    elif bid is not None and bid - prob - TAKER_FEE_RATE >= MIN_EDGE and bsz > 0:
        action, price, size, edge = "buy_down", bid, bsz, bid - prob - TAKER_FEE_RATE
    if action is None:
        return None
    # Causal executability: at each horizon, is the SAME quote still standing?
    alive: Dict[str, bool] = {}
    next_idx = idx + 1
    next_change_t = tick_t[next_idx] if next_idx < len(ticks) else float("inf")
    for h in HORIZONS_MS:
        alive[str(h)] = (t0 + h / 1000.0) < next_change_t
    executable_ms = max(0.0, (next_change_t - t0) * 1000.0)
    # Settlement scoring of the taken trade (1 contract)
    pnl = None
    if settled_up is not None:
        if action == "buy_up":
            pnl = (1.0 if settled_up else 0.0) - price - TAKER_FEE_RATE
        else:
            pnl = (0.0 if settled_up else 1.0) - (1.0 - price) - TAKER_FEE_RATE
    return {
        "market": market, "t0": round(t0, 3), "fair": round(prob, 3),
        "action": action, "stale_price": price, "stale_size": size,
        "edge_after_fee": round(edge, 3),
        "alive_at_ms": alive, "executable_ms": round(min(executable_ms, 99999), 1),
        "settled_up": settled_up, "pnl_1lot": round(pnl, 4) if pnl is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/ref_lag")
    args = parser.parse_args()
    spot, pbook, meta, _rfr = load(args.data_dir)
    models = {sym: SpotModel(trades) for sym, trades in spot.items()}
    all_signals: List[dict] = []
    for market, ticks in sorted(pbook.items()):
        if market not in meta:
            continue
        sym = asset_of(market)
        if sym not in models:
            continue
        all_signals.extend(study_window(market, ticks, meta[market], models[sym]))
    scored = [s for s in all_signals if s["pnl_1lot"] is not None]
    agg: Dict[str, Any] = {
        "evidence_class": "CAUSAL_STALE_QUOTE_STUDY_TAKER_FEE_PARAM",
        "taker_fee_assumed": TAKER_FEE_RATE,
        "min_edge_required": MIN_EDGE,
        "signals_with_stale_executable_quote": len(all_signals),
        "signals_settlement_scored": len(scored),
        "wins": sum(1 for s in scored if s["pnl_1lot"] > 0),
        "total_pnl_1lot_each": round(sum(s["pnl_1lot"] for s in scored), 4),
    }
    for h in HORIZONS_MS:
        alive = [s for s in all_signals if s["alive_at_ms"][str(h)]]
        agg[f"alive_{h}ms"] = len(alive)
    payload = {"generated_utc": datetime.now(timezone.utc).isoformat(),
               "aggregate": agg, "signals": all_signals[:500]}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(agg, indent=1))


if __name__ == "__main__":
    main()
