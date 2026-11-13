#!/usr/bin/env python3
"""BTC15m: how often does a maker pair priced at or below a target actually
complete, and what does it earn once lone legs are counted?

REPLAY on recorded PMUS BTC15m top-of-book (data/l5/recent/wide_20261111/
books_380.json: per window, rows of [t_offset_s, yes_bid, yes_ask] and the
settled winner). Hypothetical fills, never realized.

NO LOOKAHEAD. Every decision (open, price) reads only the row at hand.
Fills are decided by LATER rows. The winner is read only to settle a lone
leg at the end of the window. Nothing branches on the outcome.

POLICY (one pair at a time, the engine's shape):
  flat and t < ENTRY_CUT:  UP bid = yes_bid - d ticks, DOWN bid = (1 - yes_ask) - d
                           open BOTH only if UP + DOWN <= target T
  resting legs stay at their price; the first fill makes the other the
  completion; unfilled openings are cancelled at ENTRY_CUT, a completion
  rests until COMPLETE_CUT.
FILL RULES:
  through: UP fills when a later yes_ask <  UP price; DOWN when later yes_bid >  1-DOWN
  touch:   the same with <= / >=  (optimistic)
Per window: completed pairs earn 1 - (UP+DOWN); a lone leg settles at
winner value - price. Mean per window and a window-clustered 95% interval.

    python3 analysis/l5/btc15m_pair_gate_measure.py [--out OUT.json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
from decimal import Decimal as D

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOOKS = os.path.join(ROOT, "data/l5/recent/wide_20261111/books_380.json")
TICK = D("0.01")
ENTRY_CUT, COMPLETE_CUT = 600.0, 870.0


def sim(rows, winner, d, T, rule, chase=None):
    pnl, pairs, lones, opened = D(0), 0, 0, 0
    pair_costs = []
    state = None              # None | dict(up=px, dn=px, fu=bool, fd=bool)
    for t, b, a in rows:
        if b is None or a is None:
            continue
        if state is not None:
            if chase is not None and state["fu"] != state["fd"]:
                # CHASE: re-price the completion to its touch, never above the cap
                if state["fu"] and not state["fd"]:
                    state["dn"] = min(max(state["dn"], 1 - a), chase - state["up"])
                elif state["fd"] and not state["fu"]:
                    state["up"] = min(max(state["up"], b), chase - state["dn"])
            if not state["fu"]:
                state["fu"] = (a < state["up"]) if rule == "through" else (a <= state["up"])
            if not state["fd"]:
                lvl = 1 - state["dn"]
                state["fd"] = (b > lvl) if rule == "through" else (b >= lvl)
            if state["fu"] and state["fd"]:
                c = state["up"] + state["dn"]
                pnl += 1 - c; pairs += 1; pair_costs.append(c)
                state = None
                continue
            if not state["fu"] and not state["fd"] and t >= ENTRY_CUT:
                state = None              # unfilled opening cancelled
            elif t >= COMPLETE_CUT:
                break
            continue
        if t >= ENTRY_CUT:
            continue
        up = b - d * TICK
        dn = (1 - a) - d * TICK
        if up < TICK or dn < TICK or up + dn > T:
            continue
        state = {"up": up, "dn": dn, "fu": False, "fd": False}
        opened += 1
    if state is not None and (state["fu"] != state["fd"]):
        side = "UP" if state["fu"] else "DOWN"
        px = state["up"] if state["fu"] else state["dn"]
        pnl += (1 if winner == side else 0) - px
        lones += 1
    return pnl, pairs, lones, opened, pair_costs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    books = json.load(open(BOOKS))
    wins = []
    spreads = []
    for slug, w in sorted(books.items()):
        rows = [(float(r[0]), D(r[1]) if r[1] else None, D(r[2]) if r[2] else None)
                for r in w["rows"]]
        if w.get("winner") not in ("UP", "DOWN"):
            continue
        wins.append((slug, rows, w["winner"]))
        spreads += [float(r[2] - r[1]) for r in rows if r[1] is not None and r[2] is not None and r[0] < ENTRY_CUT]
    q = sorted(spreads)
    print("REPLAY, recorded PMUS BTC15m books; hypothetical fills, NOT realized")
    print("windows %d | spread (yes_ask - yes_bid) before %ds: median %.3f p75 %.3f p90 %.3f | share >= 0.05: %.3f"
          % (len(wins), ENTRY_CUT, q[len(q)//2], q[int(.75*len(q))], q[int(.9*len(q))],
             sum(1 for s in q if s >= 0.0499) / len(q)))
    out = {"label": "REPLAY, hypothetical fills, not realized", "windows": len(wins), "rows": []}
    print("%-7s %-4s %-5s %8s %8s %7s %7s %8s %8s %18s" % (
        "rule", "d", "T", "opens/w", "pairs/w", "lone/w", "compl%", "paircost", "pnl/w", "95% interval"))
    for rule, chase in (("through", None), ("touch", None), ("through", D("0.99")), ("through", D("1.00"))):
        print("-- completion %s" % ("STATIC" if chase is None else "CHASE to pair <= %s" % chase))
        for d in (0, 2, 3, 5):
            for T in (D("0.99"), D("0.97"), D("0.95"), D("0.93"), D("0.90")):
                per, P, L, O, C = [], 0, 0, 0, []
                for slug, rows, win in wins:
                    p, pr, lo, op, pc = sim(rows, win, d, T, rule, chase)
                    per.append(float(p)); P += pr; L += lo; O += op; C += pc
                n = len(per)
                m = st.mean(per); se = st.pstdev(per) / math.sqrt(n)
                comp = P / (P + L) if (P + L) else float("nan")
                row = {"rule": rule, "chase": (None if chase is None else str(chase)), "depth": d, "target": str(T), "opens_per_w": O / n,
                       "pairs_per_w": P / n, "lone_per_w": L / n, "completion": comp,
                       "pair_cost": (float(sum(C) / len(C)) if C else None),
                       "pnl_per_w": m, "ci95": [m - 1.96 * se, m + 1.96 * se]}
                out["rows"].append(row)
                print("%-7s %-4d %-5s %8.2f %8.2f %7.2f %7.1f %8s %+8.4f [%+.4f, %+.4f]" % (
                    rule, d, T, O / n, P / n, L / n, 100 * comp,
                    ("%.4f" % row["pair_cost"]) if C else "-", m, m - 1.96 * se, m + 1.96 * se))
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
