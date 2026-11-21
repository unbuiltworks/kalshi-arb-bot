"""Score the three arms on INVENTORY, not on quote fill rate.

A generic maker fill rate is the wrong objective and chasing it is what
produced seven one-sided positions and -$102.54 of directional residual. What
matters is whether a window ends with MATCHED INVENTORY at a price worth
having, so every arm is scored on the same eight quantities:

    P(two-sided evidenced inventory)
    time to two-sided
    matched quantity
    effective pair cost after known economics
    residual quantity
    residual duration
    settled matched P&L
    settled residual P&L

and each is placed beside L5's own BTC15m record, which is the only benchmark
that means anything.

TWO GATES, NEVER ONE. Forming a pair and forming a good one are different
things: arm R's first live pairs were two-sided at a median cost of 1.43, a
guaranteed 43c-per-share loss. A two-sided rate is therefore never reported
without the pair cost beside it, and a pair above par is counted as
economically negative however it formed.

Only BTC15m rows are aggregated. BTC5m, ETH15m and ETH5m are a separate
predominantly-taker regime and are split out rather than averaged in.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from src.l5_shadow import cohorts as CO
from src.l5_shadow import ledger as LG
from src.l5_shadow import two_sided as TS

# L5's own BTC15m record, measured over 506 two-sided windows. The benchmark
# row, never a target to tune toward.
L5_BTC15M = {
    "two_sided_rate": 0.960,
    "median_time_to_two_sided_s": 26.0,
    "p10_time_to_two_sided_s": 3.0,
    "median_pair_cost_at_formation": "0.9900",
    "median_pair_cost_final": "0.9624",
    "median_fills_per_window": 121,
    "maker_rate": 0.955,
    "source": "analysis/l5/pair_formation/btc15m.py over the canonical history",
}


def _load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def windows(outcomes: Sequence[dict], *, arm: str) -> Dict[tuple, Dict[str, Any]]:
    """Group one arm's EVIDENCED fills into per (venue, market) inventory."""
    by: Dict[tuple, Dict[str, Any]] = {}
    for o in outcomes:
        if o.get("arm", "R") != arm or o.get("evidenced_fill") is not True:
            continue
        if not CO.is_maker_cohort(o.get("cohort")):
            continue
        k = (o.get("venue"), o.get("market_key"))
        g = by.setdefault(k, {"venue": k[0], "market_key": k[1],
                              "cohort": o.get("cohort"), "fills": []})
        g["fills"].append(o)
    for g in by.values():
        g["fills"].sort(key=lambda f: float(f.get("placed_at") or 0))
    return by


def score_window(g: Dict[str, Any],
                 resolution: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One window's inventory outcome, priced and settled."""
    up_q = up_c = dn_q = dn_c = D("0")
    t0 = float(g["fills"][0].get("placed_at") or 0) if g["fills"] else None
    t_two = None
    for f in g["fills"]:
        q = D(str(f.get("quote_qty") or "0"))
        p = D(str(f.get("quote_price") or "0"))
        if str(f.get("desired_outcome")) == "Up":
            up_q += q
            up_c += q * p
        else:
            dn_q += q
            dn_c += q * p
        if t_two is None and up_q > 0 and dn_q > 0:
            t_two = float(f.get("placed_at") or 0)
    matched = min(up_q, dn_q)
    pair = (((up_c / up_q) + (dn_c / dn_q)) if up_q > 0 and dn_q > 0 else None)
    pos = {"venue": g["venue"], "cohort": g["cohort"],
           "market_key": g["market_key"], "clock": "feed",
           "up_qty": up_q, "up_cost": up_c, "dn_qty": dn_q, "dn_cost": dn_c,
           "fees": D("0"), "n_fills": len(g["fills"]),
           "intent_ids": [f.get("intent_id") for f in g["fills"]],
           "first_t": t0, "last_t": t0}
    settled = LG.settle(pos, resolution)
    return {
        "venue": g["venue"], "market_key": g["market_key"],
        "cohort": g["cohort"],
        "two_sided": matched > 0,
        "time_to_two_sided_s": (round(t_two - t0, 1)
                                if t_two is not None and t0 else None),
        "matched_qty": str(matched),
        "effective_pair_cost": (str(pair.quantize(D("0.000001")))
                                if pair is not None else None),
        "residual_qty": str(abs(up_q - dn_q)),
        "residual_side": ("Up" if up_q > dn_q else
                          "Down" if dn_q > up_q else None),
        "settled_matched_pnl": settled.get("matched_pair_pnl"),
        "settled_residual_pnl": settled.get("residual_pnl"),
        "state": settled.get("state"),
        **({} if pair is None else TS.pair_economics(pair)),
    }


def score_arm(rows: Sequence[dict], *, arm: str) -> Dict[str, Any]:
    n = len(rows)
    two = [r for r in rows if r["two_sided"]]
    costs = sorted(D(r["effective_pair_cost"]) for r in two
                   if r["effective_pair_cost"])
    times = sorted(r["time_to_two_sided_s"] for r in two
                   if r["time_to_two_sided_s"] is not None)
    settled = [r for r in rows if r["state"] == "SETTLED"]
    return {
        "arm": arm, "windows_with_a_fill": n,
        "P_two_sided_evidenced": (round(len(two) / n, 4) if n else None),
        "time_to_two_sided_s": _dist(times),
        "matched_qty_total": str(sum((D(r["matched_qty"]) for r in rows),
                                     D("0"))),
        "effective_pair_cost": _dist_d(costs),
        "pairs_above_par": sum(1 for c in costs if c > D("1")),
        "pairs_within_ceiling": sum(
            1 for c in costs if c <= TS.ADAPTED_EXECUTABLE_PAIR_CEILING),
        "residual_qty_total": str(sum((D(r["residual_qty"]) for r in rows),
                                      D("0"))),
        "settled_windows": len(settled),
        "settled_matched_pnl": (
            str(sum((D(r["settled_matched_pnl"]) for r in settled
                     if r.get("settled_matched_pnl")), D("0")))
            if settled else None),
        "settled_residual_pnl": (
            str(sum((D(r["settled_residual_pnl"]) for r in settled
                     if r.get("settled_residual_pnl")), D("0")))
            if settled else None),
        "note": (None if two else
                 "no two-sided window in this arm: there is no pair cost to "
                 "report, and a two-sided rate of 0 is the finding"),
    }


def _dist(v: Sequence) -> Optional[Dict[str, Any]]:
    if not v:
        return None
    s = sorted(v)
    return {"n": len(s), "p10": s[int(len(s) * 0.1)], "median": s[len(s) // 2],
            "p90": s[int(len(s) * 0.9)]}


def _dist_d(v: Sequence[D]) -> Optional[Dict[str, Any]]:
    d = _dist(v)
    return ({k: (str(x) if isinstance(x, D) else x) for k, x in d.items()}
            if d else None)


def report(out_dir: str, *, resolver=None) -> Dict[str, Any]:
    outs = (_load(os.path.join(out_dir, "maker_outcomes.jsonl"))
            + _load(os.path.join(out_dir, "two_sided_outcomes.jsonl"))
            + _load(os.path.join(out_dir, "inventory_outcomes.jsonl")))
    if resolver is None:
        from src.l5_shadow import venues as V
        resolver = V.pm_settlement
    seen: Dict[str, Any] = {}
    arms: Dict[str, Any] = {}
    per_venue: Dict[str, Any] = {}
    for arm in ("R", "S", "D"):
        rows = []
        for g in windows(outs, arm=arm).values():
            mk = g["market_key"]
            if mk not in seen:
                try:
                    seen[mk] = resolver(mk)
                except Exception:                             # noqa: BLE001
                    seen[mk] = None
            rows.append(score_window(g, seen[mk]))
        arms[arm] = score_arm(rows, arm=arm)
        for v in sorted({r["venue"] for r in rows}):
            per_venue.setdefault(v, {})[arm] = score_arm(
                [r for r in rows if r["venue"] == v], arm=arm)
    return {
        "computed_at": time.time(), "cohort": "BTC15m",
        "regime": CO.regime("BTC15m"),
        "arms": arms, "by_venue": per_venue,
        "L5_BTC15m_benchmark": L5_BTC15M,
        "labels": (
            "scored on INVENTORY, not on quote fill rate. A two-sided rate is "
            "never reported without the pair cost beside it: forming a pair "
            "and forming a good one are different gates, and arm R's first "
            "live pairs were two-sided at a median 1.43. Only BTC15m is "
            "aggregated; the taker-regime cohorts are excluded, not deleted. "
            "Venues are scored separately and qualify independently."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-research/l5-shadow-state")
    a = ap.parse_args(argv)
    print(json.dumps(report(a.out_dir), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
