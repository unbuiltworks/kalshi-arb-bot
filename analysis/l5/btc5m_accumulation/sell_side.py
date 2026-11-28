"""Sell the longshots instead of buying them. READ-ONLY.

THE QUESTION, owner 2026-11-14. Every sweep on this branch rests BUY
orders. Selling a longshot is a different trade and -- crucially -- a
different FILL. A resting buy at 0.08 fills when the bid FALLS to us, so
we end up long whatever just collapsed. A resting sell at 0.08 fills when
a buyer comes UP to us, so we end up short whatever is strengthening.
The adverse selection points the opposite way and nothing here has
measured it.

THE POSITION-LEVEL ARGUMENT, which is NOT the whole answer: in a binary
market selling UP at p is the same POSITION as buying DOWN at 1-p, and
buying at 0.92 measures -42c a leg. But that equivalence is about the
payoff, not about WHEN you get filled, so the sell side deserves its own
measurement.

FILL RULE, the adverse one, mirrored. A resting SELL at p fills when that
side's BID rises through p -- someone is willing to pay p. We receive p
and owe 1.00 if that side wins.

NOTE ON FEASIBILITY: BTC5m execution is BUY-only by policy and by code
(`l5_btc5m_execution` hardcodes action BUY). This measures whether the
question is worth reopening, not something we could run today.
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
    w = defaultdict(lambda: {"outcome": None, "max_up": None, "max_down": None,
                             "min_up": None, "min_down": None})
    with gzip.open(TAPE, "rt") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            mkt, rem = d.get("market"), d.get("seconds_remaining")
            if not mkt or rem is None or float(rem) < CANCEL_LEAD_S:
                continue
            rec = w[mkt]
            if d.get("settled_outcome"):
                rec["outcome"] = str(d["settled_outcome"])
            for side, mx, mn in (("up", "max_up", "min_up"),
                                 ("down", "max_down", "min_down")):
                b = (d.get(side) or {}).get("bid")
                if b is None:
                    continue
                b = float(b)
                if rec[mx] is None or b > rec[mx]:
                    rec[mx] = b
                if rec[mn] is None or b < rec[mn]:
                    rec[mn] = b
    return {m: r for m, r in w.items()
            if r["outcome"] in ("UP", "DOWN")
            and r["max_up"] is not None and r["max_down"] is not None}


def sell_leg(windows, price, side):
    """Rest a SELL at `price` on `side`; fills when that side's bid rises
    through it. Receive price, owe 1.00 if that side wins."""
    key = "max_up" if side == "UP" else "max_down"
    fills = wins = 0
    series = []
    for rec in windows.values():
        if rec[key] >= price - 1e-12:
            fills += 1
            won = (rec["outcome"] == side)
            if won:
                wins += 1
                series.append(price - 1.0)
            else:
                series.append(price)
        else:
            series.append(0.0)
    n = len(series)
    m = sum(series) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in series) / (n - 1))
    t = m / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    return {"fills": fills, "wins": wins,
            "wr": (wins / fills) if fills else float("nan"),
            "net": 100.0 * m, "t": t, "n": n}


def main() -> int:
    w = load()
    print("windows: %d\n" % len(w))
    print("SELLING a leg at price p (direction-neutral: UP and DOWN averaged)")
    print("%6s %8s %11s %12s %10s %8s"
          % ("price", "fill%", "wins|fill", "owe if wins", "EV c/leg", "t"))
    for c in (3, 5, 8, 10, 15, 20, 30, 40, 50):
        p = c / 100.0
        u = sell_leg(w, p, "UP")
        d = sell_leg(w, p, "DOWN")
        fills = u["fills"] + d["fills"]
        wins = u["wins"] + d["wins"]
        nn = u["n"] + d["n"]
        ev = (u["net"] + d["net"]) / 2.0
        tt = (u["t"] + d["t"]) / 2.0
        wr = wins / fills if fills else float("nan")
        mark = "   <- mirror of the straddle leg" if c == 8 else ""
        print("%6.2f %7.1f%% %10.1f%% %11.2f %+10.2f %+8.2f%s"
              % (p, 100.0 * fills / nn, 100 * wr, -(1 - p), ev, tt, mark))
    print()
    print("for reference, BUYING at 0.08 measures +1.87 c a leg.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
