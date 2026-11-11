"""Canonical L5 inventory cycle, reconstructed per market.

    FIRST BUY -> SAME-SIDE RUN -> FIRST SWITCH -> COMPLEMENT RUN
              -> SUBSEQUENT SWITCHES -> TERMINAL INVENTORY -> REDEMPTION

One pass over the deduped public activity builds every market's ordered fill
sequence; P0 (what distinguishes a favourite-open from an underdog-open), P1
(the cycle itself, segmented DEAR-first vs CHEAP-first) and P3 (which of L5's
own switches our pair-cost gate would refuse) all read from it.

NO OUTCOME LEAKAGE anywhere: the winning side is used only to compute
redemption and realized cash AFTER the fact, never as a feature.

UNAVAILABLE IS NOT ZERO. spread, depth, order flow, intra-window trajectory,
book updates and queue position are not in the public tape and are named as
unavailable rather than imputed.
"""
from __future__ import annotations

import collections
import math
import statistics
from typing import Any, Dict, List, Optional

from analysis.l5.redemption import audit as A

UNAVAILABLE_FEATURES = ("spread", "depth_at_touch", "order_flow_imbalance",
                        "intra_window_trajectory", "book_updates",
                        "queue_position")

# Our own economic policy, mirrored as VALUES for the P3 counterfactual.
# NOTHING HERE CHANGES A GATE -- these are read-only labels.
PAIR_GATES = (0.96, 0.97, 0.98, 1.00)
SPRING_PAIR_TARGET = 0.96


def build_cycles() -> List[dict]:
    """One record per market, in chronological order of its first fill."""
    buys: Dict[str, list] = collections.defaultdict(list)
    meta: Dict[str, tuple] = {}
    redeem: Dict[str, dict] = {}
    sells: Dict[str, float] = collections.defaultdict(float)
    for r in A.iter_activity():
        t = str(r.get("type") or "").upper()
        cid = str(r.get("conditionId") or "")
        w = A.window_of(r)
        if not w or not cid:
            continue
        meta.setdefault(cid, w)
        if t == "TRADE":
            side = str(r.get("side") or "").upper()
            px = float(r.get("price") or 0)
            q = float(r.get("size") or 0)
            u = float(r.get("usdcSize") or 0)
            if side == "BUY" and px > 0:
                buys[cid].append((int(r.get("timestamp") or 0), px,
                                  str(r.get("outcome")), q, u))
            elif side == "SELL":
                sells[cid] += u
        elif t == "REDEEM":
            d = redeem.setdefault(cid, {"qty": 0.0, "usd": 0.0,
                                        "outcome": None, "t": None})
            d["qty"] += float(r.get("size") or 0)
            d["usd"] += float(r.get("usdcSize") or 0)
            d["outcome"] = d["outcome"] or str(r.get("outcome"))
            ts = int(r.get("timestamp") or 0)
            d["t"] = ts if d["t"] is None else min(d["t"], ts)

    out: List[dict] = []
    for cid, s in buys.items():
        s.sort(key=lambda x: x[0])
        asset, horizon, end = meta[cid]
        t0, p0, o0, q0, u0 = s[0]
        up0 = p0 if o0 == "Up" else round(1.0 - p0, 6)

        # same-side run and the first switch
        k = next((i for i in range(1, len(s)) if s[i][2] != o0), None)
        run_q = sum(x[3] for x in s[:k]) if k else sum(x[3] for x in s)
        run_c = sum(x[1] * x[3] for x in s[:k]) if k else sum(x[1] * x[3] for x in s)
        wavg_first = (run_c / run_q) if run_q else p0
        sw = None
        if k is not None:
            tk, pk, ok, qk, uk = s[k]
            sw = {
                "i": k, "t": tk, "px": pk, "outcome": ok,
                "dt_s": tk - t0,
                "fills_before": k,
                "qty_before": round(run_q, 6),
                "wavg_before": round(wavg_first, 6),
                # L5's OWN attainable pair at his switch: what he already
                # paid on one side plus what he paid to start the other.
                "pair_cost": round(wavg_first + pk, 6),
                "gain_per_unit": round(pk - wavg_first, 6),
                "price_move_before": round(s[k - 1][1] - p0, 6),
            }

        # switches over the whole window
        switches = sum(1 for i in range(1, len(s)) if s[i][2] != s[i - 1][2])
        up = sum(x[3] for x in s if x[2] == "Up")
        dn = sum(x[3] for x in s if x[2] != "Up")
        tot = up + dn
        red = redeem.get(cid) or {}
        spend = sum(x[4] for x in s)
        out.append({
            "cid": cid, "t": t0, "asset": asset, "horizon": horizon,
            "cohort": f"{asset.upper()}{horizon}", "window_end": end,
            "first_px": p0, "first_outcome": o0, "up_price": up0,
            "first_is_dear": p0 > 0.5,
            "dist_from_50": round(abs(up0 - 0.5), 6),
            "tte_s": end - t0,
            "hour_utc": (t0 // 3600) % 24,
            "n_fills": len(s),
            "switches": switches,
            "first_switch": sw,
            "up_qty": round(up, 6), "dn_qty": round(dn, 6),
            "matched_qty": round(min(up, dn), 6),
            "residual_qty": round(abs(up - dn), 6),
            "matched_fraction": (round(2 * min(up, dn) / tot, 6) if tot else None),
            "residual_fraction": (round(abs(up - dn) / tot, 6) if tot else None),
            "buy_usd": round(spend, 6),
            "sell_usd": round(sells.get(cid, 0.0), 6),
            "redeem_usd": round(red.get("usd", 0.0), 6),
            "redeem_qty": round(red.get("qty", 0.0), 6),
            "redeem_outcome": red.get("outcome"),
            "redeem_delay_s": (None if not red.get("t") else red["t"] - end),
            "realized_trading_cash": round(
                red.get("usd", 0.0) + sells.get(cid, 0.0) - spend, 6),
        })
    out.sort(key=lambda r: r["t"])

    # temporally valid predecessor within the same series
    prev_by: Dict[str, dict] = {}
    for r in out:
        p = prev_by.get(r["cohort"])
        ok = p is not None and (redeem.get(p["cid"], {}).get("t") or 0) <= r["t"]
        r["prev_up_price"] = p["up_price"] if ok else None
        r["prev_is_dear"] = p["first_is_dear"] if ok else None
        r["prev_move"] = (round(r["up_price"] - p["up_price"], 6) if ok else None)
        r["prev_abs_move"] = (abs(r["prev_move"]) if ok else None)
        prev_by[r["cohort"]] = r

    # realized volatility of the series: rolling std of the last 10 up_prices
    hist: Dict[str, list] = collections.defaultdict(list)
    for r in out:
        h = hist[r["cohort"]]
        r["vol_10"] = (round(statistics.pstdev(h[-10:]), 6)
                       if len(h) >= 3 else None)
        h.append(r["up_price"])
    return out


def gate_label(pair_cost: Optional[float],
               gate: float = SPRING_PAIR_TARGET) -> str:
    """Would OUR economic policy admit the switch L5 actually made?

    READ-ONLY LABEL. No gate is changed anywhere by this function.
    """
    if pair_cost is None:
        return "NO_SWITCH"
    return "OUR_POLICY_WOULD_ALLOW" if pair_cost <= gate else "OUR_POLICY_WOULD_REJECT"


def q(xs: List[float], p: float) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
