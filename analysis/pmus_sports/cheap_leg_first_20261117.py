"""Cheap-leg-first vs dearer-leg-first vs simultaneous: LIVE evidence.

Input: episodes_20261117.json -- every one-sided episode rebuilt from the
bot's venue-confirmed FILL events, 2026-11-15 18:21Z .. 2026-11-17 03:29Z
(recovery controller on; maker only; 81 episodes). An episode starts when a
fill takes a flat market one-sided and ends when the position is balanced
again (paired) or the game ends (held). Held legs are priced at the venue's
PUBLIC settlementPx read 2026-11-17. Pairs settle to $1 each, so a pair's
settled P&L is 1 - pair cost (maker fills: no fee).

Under simultaneous two-sided quoting, WHICH leg fills first is what the
market decides; this splits the evidence by that. A cheap-first policy
removes the dearer-first episodes (it never rests the dearer leg alone).
"""
import json, os, statistics as st
from decimal import Decimal as D
HERE = os.path.dirname(os.path.abspath(__file__))
SETTLE = {"aec-nba-por-min-2026-11-16": D("0"), "aec-cs2-yl-bang-2026-11-15": D("0"),
          "aec-setkameua-vasivol-serrus-2026-11-15": D("0"),
          "aec-itfwo-vallos-katgar-2026-11-15": D("1")}   # YES settlement px
eps = json.load(open(os.path.join(HERE, "episodes_20261117.json")))
for name, cheap in (("cheap-first (lead < 0.50)", True), ("dearer-first (lead >= 0.50)", False)):
    g = [e for e in eps if (D(e["lead_px"]) < D("0.5")) == cheap]
    pnl = []
    for e in g:
        if e["end"] == "paired":
            pnl.append((D(1) - D(e["pair_cost"])) * min(D(e["lead_q"]), D(e["comp_q"])))
        else:
            px = SETTLE[e["slug"]]; val = px if e["lead"] == "YES" else D(1) - px
            pnl.append(D(e["lead_q"]) * val - D(e["lead_cost"]))
    held = [p for e, p in zip(g, pnl) if e["end"] != "paired"]
    print(f"{name}: episodes {len(g)}, held {len(held)} ({sum(held):+.2f}), "
          f"settled total {sum(pnl):+.2f}, per episode {sum(pnl)/len(g):+.4f}")
