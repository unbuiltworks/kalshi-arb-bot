"""Executable capacity per BTC15m window at $20 / $50 / $100 / $200.

Depth is READ, not assumed: 290 authenticated L2 snapshots recorded by the
live engine on 2026-10-25 (`data/l5_strand_replay/residual_books_20261025.jsonl`),
49-50 levels a side. Nothing here is a fill: depth bounds what COULD rest,
and queue position bounds what would fill. Both are reported separately.
"""
import json
import statistics as st
from decimal import Decimal as D

ROWS = [json.loads(l) for l in
        open("data/l5_strand_replay/residual_books_20261025.jsonl")]
T = D("0.01")

# ---------------------------------------------------------------- 1. depth
bid_px, bid_sh, ask_px, ask_sh, spreads = [], [], [], [], []
for r in ROWS:
    b, a = r.get("bids") or [], r.get("asks") or []
    if not b or not a:
        continue
    bp, bs = D(b[0][0]), D(b[0][1])
    ap, asz = D(a[0][0]), D(a[0][1])
    bid_px.append(bp); bid_sh.append(bs)
    ask_px.append(ap); ask_sh.append(asz)
    spreads.append(ap - bp)

def pct(xs, p):
    xs = sorted(float(x) for x in xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]

print("=" * 74)
print("1. RECORDED TOUCH DEPTH  (n=%d snapshots, %d windows)" % (
    len(spreads), len({r["slug"] for r in ROWS})))
print("=" * 74)
print("%-22s %10s %10s %10s" % ("", "p10", "p50", "p90"))
for name, xs in (("bid touch shares", bid_sh), ("ask touch shares", ask_sh)):
    print("%-22s %10.1f %10.1f %10.1f" % (
        name, pct(xs, 10), pct(xs, 50), pct(xs, 90)))
bid_usd = [p * s for p, s in zip(bid_px, bid_sh)]
ask_usd = [p * s for p, s in zip(ask_px, ask_sh)]
for name, xs in (("bid touch $", bid_usd), ("ask touch $", ask_usd)):
    print("%-22s %10.2f %10.2f %10.2f" % (
        name, pct(xs, 10), pct(xs, 50), pct(xs, 90)))
print("%-22s %10.4f %10.4f %10.4f" % (
    "spread (ticks)", pct(spreads, 10) * 100, pct(spreads, 50) * 100,
    pct(spreads, 90) * 100))
sp50 = D(str(round(pct(spreads, 50), 4)))

# ------------------------------------------- 2. two-sided pair cost by spread
print()
print("=" * 74)
print("2. PRE-POSITIONED PAIR COST = 1 - spread - 2*depth*tick")
print("=" * 74)
print("   (the identity from one_vs_two_resting.py; depth = ticks behind")
print("    each touch, so depth 0 is AT the touch)")
print("%-14s %12s %12s %12s" % ("spread", "depth 0", "depth 1", "depth 2"))
for sp_t in (1, 2, 3):
    sp = D(sp_t) * T
    row = [D(1) - sp - 2 * d * T for d in (0, 1, 2)]
    print("%-14s %12s %12s %12s" % (
        "%d tick" % sp_t, *["%.4f" % r for r in row]))

# ------------------------------------------------------- 3. capacity ladder
print()
print("=" * 74)
print("3. CAPACITY LADDER  (per window, both legs, maker-only, fees $0)")
print("=" * 74)
print("A matched pair redeems exactly $1.00, so profit = (1 - pair) * qty.")
print("Capital out per pair = pair cost. Contracts = budget / pair.")
print()
med_bid_sh = D(str(pct(bid_sh, 50)))
med_ask_sh = D(str(pct(ask_sh, 50)))
WINDOWS_PER_DAY = {"L5 observed 33.5": D("33.5"), "all 96": D("96")}

for pair in (D("0.95"), D("0.97"), D("0.99")):
    edge = D(1) - pair
    print("-- pair cost %.2f  (edge $%.2f/contract) %s" % (
        pair, edge, "<- 1-tick book at depth 2" if pair == D("0.95") else ""))
    print("%8s %10s %10s %12s %12s %10s" % (
        "budget", "contracts", "per leg", "profit/win", "$/day @33.5",
        "% of touch"))
    for b in (D("20"), D("50"), D("100"), D("200")):
        qty = b / pair
        prof = edge * qty
        share = qty / min(med_bid_sh, med_ask_sh) * 100
        print("%8s %10.1f %10.1f %12.4f %12.2f %9.2f%%" % (
            "$" + str(b), qty, qty, prof, prof * D("33.5"), share))
    print()

# ----------------------------------------------- 4. what the target requires
print("=" * 74)
print("4. WHAT $480/DAY REQUIRES")
print("=" * 74)
for pair in (D("0.95"), D("0.97"), D("0.99")):
    edge = D(1) - pair
    need_contracts = D("480") / edge
    for label, wpd in WINDOWS_PER_DAY.items():
        per_win = need_contracts / wpd
        cap = per_win * pair
        print("pair %.2f  edge $%.2f  ->  %7.0f matched contracts/day"
              "  = %6.1f/window over %-18s capital $%8.2f/window"
              % (pair, edge, need_contracts, per_win, label, cap))
    print()

# -------------------------------------------------- 5. observed, for scale
print("=" * 74)
print("5. WHAT WE ACTUALLY DID  (Stage B2, settled)")
print("=" * 74)
obs = [("1315z", D("6.03")), ("1330z", D("0")), ("1345z", D("1.97")),
       ("1415z", D("3.79")), ("1430z", D("0")), ("1445z", D("0"))]
tot = sum(q for _, q in obs)
print("matched contracts, recorded windows: %s = %.2f" % (
    " + ".join("%s %.2f" % (w, q) for w, q in obs), tot))
print("(1400z matched 2 pairs; its qty is on the volume tape, not local)")
print()
print("best single window: 6.03 matched contracts")
print("to reach $480/day at pair 0.95 we need ~287 matched contracts/window")
print("over 33.5 windows -- a factor of %.0fx on our best window."
      % (D("287") / D("6.03")))
