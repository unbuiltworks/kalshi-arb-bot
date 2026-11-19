"""Does the lone leg's ~2.5% win rate hold from 1c all the way to 49c?

THE QUESTION, asked by the owner 2026-11-13. WHY_IT_BUYS_THE_LOSER.md
measures the one-sided win rate at 0.02, 0.03, 0.05, 0.08, 0.15 and 0.30
and finds it FLAT at 2-3%. That table stops at 0.30. "Flat across 1-49"
was never measured, and the regime plainly changes as p -> 0.50: near the
money BOTH sides fill almost immediately, so the lone-fill case becomes
rare and the surviving cases are a different, more selected population.

METHOD. Same tape and same ADVERSE fill rule as the other replays: a
resting buy at p on a side fills when that side's BID falls through p.
Fills are only counted while the order would still be resting, i.e. before
the cancel lead. Per window and per price: which sides filled, and for the
one-sided windows, whether the side we were left holding actually won.

SELF-CHECK FIRST. At 0.08 this must reproduce the published decomposition
(BOTH 19, ONE 162, NONE 6 over 187 windows) or the method is wrong and
nothing below it means anything.

READ-ONLY. Reads the aligned tape, writes nothing, places nothing.
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
    """market -> {"outcome": str, "min_up": float, "min_down": float}"""
    w = defaultdict(lambda: {"outcome": None, "min_up": None,
                             "min_down": None, "rows": 0})
    with gzip.open(TAPE, "rt") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            mkt = d.get("market")
            if not mkt:
                continue
            # only while a resting order would still be live
            rem = d.get("seconds_remaining")
            if rem is None or float(rem) < CANCEL_LEAD_S:
                continue
            rec = w[mkt]
            rec["rows"] += 1
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


def sweep(windows, price):
    both = one = none = 0
    one_wins = 0
    net = 0.0
    for rec in windows.values():
        up_f = rec["min_up"] <= price + 1e-12
        dn_f = rec["min_down"] <= price + 1e-12
        if up_f and dn_f:
            both += 1
            net += 1.0 - 2.0 * price          # pair redeems 1.00
        elif up_f or dn_f:
            one += 1
            held = "UP" if up_f else "DOWN"
            if held == rec["outcome"]:
                one_wins += 1
                net += 1.0 - price
            else:
                net -= price
        else:
            none += 1
    n = len(windows)
    # per-window series, so the sweep can carry a t and not just a mean.
    # Choosing the best of 49 prices is exactly where a peak invents
    # itself, so the bar is sqrt(2 ln 49) = 2.79, not 2.
    series = []
    for rec in windows.values():
        up_f = rec["min_up"] <= price + 1e-12
        dn_f = rec["min_down"] <= price + 1e-12
        if up_f and dn_f:
            series.append(1.0 - 2.0 * price)
        elif up_f or dn_f:
            held = "UP" if up_f else "DOWN"
            series.append((1.0 - price) if held == rec["outcome"] else -price)
        else:
            series.append(0.0)
    mean = sum(series) / len(series) if series else 0.0
    if len(series) > 1:
        var = sum((x - mean) ** 2 for x in series) / (len(series) - 1)
        sd = math.sqrt(var)
        t = mean / (sd / math.sqrt(len(series))) if sd > 0 else float("nan")
    else:
        t = float("nan")
    return {"n": n, "both": both, "one": one, "none": none,
            "one_wins": one_wins,
            "one_rate": (one_wins / one) if one else float("nan"),
            "net_cw": 100.0 * net / n if n else float("nan"), "t": t}


def main() -> int:
    windows = load()
    print("windows with a settled outcome and quotes: %d" % len(windows))
    print()
    r8 = sweep(windows, 0.08)
    print("SELF-CHECK at 0.08  -> BOTH %d  ONE %d  NONE %d  (published: 19/162/6)"
          % (r8["both"], r8["one"], r8["none"]))
    ok = (r8["both"], r8["one"], r8["none"]) == (19, 162, 6)
    print("   reproduces published decomposition: %s" % ("YES" if ok else "NO"))
    if not ok:
        print("   -> method differs from the published run; treat the sweep")
        print("      below as indicative only, not as the same measurement.")
    print()
    hdr = ("price", "both%", "one n", "one wins", "one win%", "breakeven",
           "edge pts", "net c/w", "t")
    print("%6s %7s %7s %9s %9s %10s %9s %9s %7s" % hdr)
    for cents in list(range(1, 50)):
        p = cents / 100.0
        r = sweep(windows, p)
        wr = r["one_rate"]
        edge = (wr - p) * 100.0 if r["one"] else float("nan")
        print("%6.2f %6.1f%% %7d %9d %8.1f%% %9.1f%% %9.1f %9.2f %7.2f"
              % (p, 100.0 * r["both"] / r["n"], r["one"], r["one_wins"],
                 100.0 * wr if r["one"] else float("nan"),
                 100.0 * p, edge, r["net_cw"], r["t"]))
    print()
    print("multiple-comparisons bar for 49 arms: sqrt(2 ln 49) = %.2f"
          % math.sqrt(2 * math.log(49)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
