"""Why L5's inventory oscillates between sides and ours gets stranded.

OWNER: "WHY DOES L5'S INVENTORY OSCILLATE BETWEEN SIDES WHILE OURS GETS
STRANDED?" Everything here is measured from the canonical per-fill
sequence -- L5's public fills, which is all that exists. His resting
orders, cancels and maker/taker status are NOT observable and are never
inferred; what IS observable is the PRICE at which his inventory actually
changed sides, and that is the quantity this module is built around.

A SIDE SWITCH is a BUY fill whose outcome differs from the previous BUY
fill's outcome. SELLs are excluded: L5's lifetime SELL volume is $2.18 and
a sell is not an inventory switch in the sense being measured.

THE PAIR PRICE AT A SWITCH is reported two ways, because they answer
different questions and only one of them is comparable to our engine:

  last_pair   = price of the LAST same-side fill + price of the FIRST
                opposite-side fill. What the market offered across the
                switch itself.
  avg_pair    = RUNNING AVERAGE cost of the held side + price of the first
                opposite-side fill. What the completed pair actually cost
                him, and the number our COMPLETE gate is the analogue of.

Neither says he crossed the spread. We are comparing ECONOMIC PRICE, not
execution type, and maker/taker stays UNKNOWN for L5 throughout.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Iterable, Optional

PAIR_BANDS = ((0.95, "<=.95"), (0.97, ".95-.97"), (0.98, ".97-.98"),
              (0.99, ".98-.99"), (1.00, ".99-1.00"), (1e9, ">1.00"))


def band(x: float) -> str:
    for hi, label in PAIR_BANDS:
        if x <= hi:
            return label
    return PAIR_BANDS[-1][1]


def _f(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def switches(rows: list) -> list:
    """Every side switch in one settlement window, with the state around it.

    `rows` is one window's fills in time order. Running average cost is
    accumulated as we go, so `avg_pair` is the cost of the pair AT THE
    MOMENT it was completed and not a hindsight average over the whole
    window.
    """
    buys = [r for r in rows if r.get("side") == "BUY"]
    out = []
    qty = defaultdict(float)
    cost = defaultdict(float)
    run = 0
    prev: Optional[dict] = None
    run_start_t: Optional[float] = None
    run_start_px: Optional[float] = None
    for r in buys:
        o, px, q = r["outcome"], _f(r["price"]), _f(r["quantity"])
        t = _f(r["timestamp"])
        if prev is not None and o != prev["outcome"]:
            held = prev["outcome"]
            held_q = qty[held]
            avg = (cost[held] / held_q) if held_q > 0 else None
            out.append({
                "t": t,
                "tte_s": _f(r["seconds_to_settlement"]),
                "run_length_before_switch": run,
                "run_seconds": (t - run_start_t
                                ) if run_start_t is not None else None,
                "from_side": held, "to_side": o,
                "last_same_side_price": _f(prev["price"]),
                "first_opposite_price": px,
                # ACROSS-SWITCH is a CROSS-SIDE gap, not a temporal move:
                # in a binary market the two sides sum to ~1, so it is
                # determined by where in 0-1 the market sits. The temporal
                # move is the drift of the HELD side over its own run, and
                # that is the number that can speak to "did he switch after
                # the market moved".
                "price_change_across_switch": px - _f(prev["price"]),
                "held_side_drift_during_run": (
                    _f(prev["price"]) - run_start_px
                    if run_start_px is not None else None),
                "last_pair": _f(prev["price"]) + px,
                "avg_pair": (avg + px) if avg is not None else None,
                "held_avg_cost": avg,
                "held_qty_before": held_q,
                "other_qty_before": qty[o],
                "matched_before": min(qty["Up"], qty["Down"]),
                "residual_before": qty["Up"] - qty["Down"],
                "switch_qty": q,
            })
            run, run_start_t, run_start_px = 0, t, px
        elif prev is None:
            # a new run begins. `run_start_t = run_start_t or t` was wrong
            # here: a timestamp of 0 is falsy, so the first run's start was
            # silently overwritten by every later fill and run_seconds read
            # 0 for the whole window.
            run_start_t, run_start_px = t, px
        run += 1
        qty[o] += q
        cost[o] += px * q
        prev = r
    return out


def window_summary(rows: list) -> dict:
    """Switch rate, run lengths and the terminal matched fraction for one
    window -- the trajectory numbers, not the action labels."""
    buys = [r for r in rows if r.get("side") == "BUY"]
    if not buys:
        return {}
    qty = defaultdict(float)
    runs, run = [], 0
    prev = None
    for r in buys:
        if prev is not None and r["outcome"] != prev["outcome"]:
            runs.append(run)
            run = 0
        run += 1
        qty[r["outcome"]] += _f(r["quantity"])
        prev = r
    runs.append(run)
    up, dn = qty["Up"], qty["Down"]
    tot = up + dn
    span = _f(buys[-1]["timestamp"]) - _f(buys[0]["timestamp"])
    return {
        "fills": len(buys),
        "switches": len(runs) - 1,
        "switch_rate_per_fill": (len(runs) - 1) / len(buys),
        "switches_per_minute": ((len(runs) - 1) / (span / 60.0)
                                if span > 0 else None),
        "mean_run_length": statistics.fmean(runs),
        "median_run_length": statistics.median(runs),
        "max_run_length": max(runs),
        "matched_fraction": (2 * min(up, dn) / tot) if tot else 0.0,
        "terminal_residual": abs(up - dn),
        "span_s": span,
    }


def distribution(xs: Iterable[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    s = sorted(xs)

    def q(p):
        return round(s[min(int(p * len(s)), len(s) - 1)], 4)
    return {"n": len(s), "p10": q(0.10), "p25": q(0.25), "p50": q(0.50),
            "p75": q(0.75), "p90": q(0.90), "mean": round(sum(s) / len(s), 4),
            "min": round(s[0], 4), "max": round(s[-1], 4)}


def band_histogram(xs: Iterable[float]) -> dict:
    c: dict[str, int] = {label: 0 for _, label in PAIR_BANDS}
    n = 0
    for x in xs:
        if x is None:
            continue
        c[band(x)] += 1
        n += 1
    return {"n": n, "counts": c,
            "shares": {k: (round(v / n, 4) if n else None)
                       for k, v in c.items()},
            "share_at_or_below_1_00": (
                round(sum(v for k, v in c.items() if k != ">1.00") / n, 4)
                if n else None)}
