"""Do UP legs and DOWN legs pay differently at the same price? READ-ONLY.

THE QUESTION, asked by the owner 2026-11-13. The price sweep in
WHY_IT_BUYS_THE_LOSER.md and lone_leg_full_range.py is SYMMETRIC: it
prices both sides the same. Settlement P&L is linear in legs, so each
leg's expectancy stands alone and the best symmetric price is only the
best asymmetric price IF the two sides behave alike. Nobody has checked.

If UP legs at 8c win materially more often than DOWN legs, the right
answer is not a different symmetric price -- it is two different prices,
or resting only on the better side. If they are the same within noise,
asymmetry is closed and 0.08/0.08 stands.

METHOD. Same tape, same adverse fill rule (a resting buy fills when that
side's bid falls through it), same cancel lead. Per SIDE and per price:
how often that leg fills, how often it wins when it does, and the
resulting per-leg expectancy (win rate minus price paid).
"""
from __future__ import annotations

import gzip
import json
import math
import os
from collections import defaultdict

TAPE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "btc5m_aligned_20261112.jsonl.gz")
CANCEL_LEAD_S = 30.0


def load():
    w = defaultdict(lambda: {"outcome": None, "min_up": None, "min_down": None})
    with gzip.open(TAPE, "rt") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            mkt = d.get("market")
            rem = d.get("seconds_remaining")
            if not mkt or rem is None or float(rem) < CANCEL_LEAD_S:
                continue
            rec = w[mkt]
            if d.get("settled_outcome"):
                rec["outcome"] = str(d["settled_outcome"])
            for side, key in (("up", "min_up"), ("down", "min_down")):
                b = (d.get(side) or {}).get("bid")
                if b is None:
                    continue
                b = float(b)
                if rec[key] is None or b < rec[key]:
                    rec[key] = b
    return {m: r for m, r in w.items()
            if r["outcome"] in ("UP", "DOWN")
            and r["min_up"] is not None and r["min_down"] is not None}


def per_side(windows, price, side):
    """One leg, in isolation. Fill when that side's bid falls through."""
    key = "min_up" if side == "UP" else "min_down"
    fills = wins = 0
    series = []
    for rec in windows.values():
        if rec[key] <= price + 1e-12:
            fills += 1
            won = (rec["outcome"] == side)
            wins += 1 if won else 0
            series.append((1.0 - price) if won else -price)
        else:
            series.append(0.0)
    mean = sum(series) / len(series) if series else 0.0
    if len(series) > 1:
        var = sum((x - mean) ** 2 for x in series) / (len(series) - 1)
        sd = math.sqrt(var)
        t = mean / (sd / math.sqrt(len(series))) if sd > 0 else float("nan")
    else:
        t = float("nan")
    return {"fills": fills, "wins": wins,
            "win_rate": (wins / fills) if fills else float("nan"),
            "net_cw": 100.0 * mean, "t": t}


def main() -> int:
    w = load()
    print("windows: %d" % len(w))
    print()
    print("%5s | %-28s | %-28s | %s"
          % ("price", "UP leg", "DOWN leg", "gap"))
    print("%5s | %6s %6s %7s %5s | %6s %6s %7s %5s | %7s"
          % ("", "fills", "win%", "c/w", "t", "fills", "win%", "c/w", "t", "c/w"))
    for cents in (3, 5, 8, 10, 15, 20, 30):
        p = cents / 100.0
        u = per_side(w, p, "UP")
        d = per_side(w, p, "DOWN")
        print("%5.2f | %6d %5.1f%% %+7.2f %+5.2f | %6d %5.1f%% %+7.2f %+5.2f | %+7.2f"
              % (p, u["fills"], 100 * u["win_rate"], u["net_cw"], u["t"],
                 d["fills"], 100 * d["win_rate"], d["net_cw"], d["t"],
                 u["net_cw"] - d["net_cw"]))
    print()
    print("A gap that is large AND carries a t the single-side column does not")
    print("would justify asymmetric pricing. Otherwise 0.08/0.08 stands and")
    print("asymmetry joins the closed list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
