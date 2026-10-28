#!/usr/bin/env python3
"""Campaign 7 settled reconciliation, tied to the authoritative cash series.

OWN PRICE IS THE ONLY BASIS USED. The venue wire price is YES/UP, so a DOWN
leg filled at wire p cost `1 - p` of our own money. The tape records both;
`own_px` on the submit is the one that is money.

PAIRS ARE FIFO WITHIN A WINDOW, and a matched pair redeems exactly $1.00 a
share on fill -- not at settlement -- which the cash series confirms. Pair
economics therefore split at the $1.00 boundary: below it is pair edge,
above it is a DEFENSIVE LOSS and is never folded into edge.

RESIDUAL IS SETTLED, NOT MARKED. An unmatched leg pays its quantity if its
side wins and zero otherwise. Nothing here infers a direction: the residual
outcome is taken from the CASH series, because a win credits exactly the
residual quantity and a loss credits nothing.
"""
import json, pathlib
from collections import defaultdict
from decimal import Decimal as D

HERE = pathlib.Path("/home/user/kalshi-arb-bot/analysis/l5/campaign7")


def load(n):
    return [json.loads(l) for l in (HERE / n).read_text().splitlines() if l.strip()]


sub = {s["coid"]: s for s in load("tape_submits.jsonl")}
fills = load("tape_fills.jsonl")
cash = load("tape_cash_moves.jsonl")

byw = defaultdict(list)
for f in sorted(fills, key=lambda r: r["utc"]):
    s = sub.get(f["coid"])
    if not s:
        print("UNJOINED FILL", f["coid"]); continue
    byw[s["window"]].append({
        "utc": f["utc"], "side": s["side"], "qty": D(str(f["qty"])),
        "own": D(str(s["own_px"])), "coid": f["coid"],
        "queue_ahead": s.get("queue_ahead"), "depth": s.get("level_depth")})

print("=" * 76)
print("CAMPAIGN 7 -- SETTLED RECONCILIATION")
print("=" * 76)

tot_edge = tot_def = D(0)
tot_pairs = 0
tot_matched = D(0)
resid_rows = []
spent = D(0)

for w in sorted(byw):
    legs = byw[w]
    inv = {"UP": [], "DOWN": []}          # FIFO queues of [qty, own]
    pairs = []
    for lg in legs:
        spent += lg["qty"] * lg["own"]
        side, other = lg["side"], ("DOWN" if lg["side"] == "UP" else "UP")
        q = lg["qty"]
        # match against opposite inventory first, FIFO
        while q > 0 and inv[other]:
            oq, oown = inv[other][0]
            m = min(q, oq)
            pairs.append((m, lg["own"] + oown))
            q -= m
            if oq - m == 0:
                inv[other].pop(0)
            else:
                inv[other][0][0] = oq - m
        if q > 0:
            inv[side].append([q, lg["own"]])
    edge = sum((m * (D(1) - c) for m, c in pairs if c <= 1), D(0))
    dfn = sum((m * (c - D(1)) for m, c in pairs if c > 1), D(0))
    tot_edge += edge; tot_def += dfn; tot_pairs += len(pairs)
    tot_matched += sum((m for m, _ in pairs), D(0))
    res = {s: sum((q for q, _ in inv[s]), D(0)) for s in ("UP", "DOWN")}
    rcost = sum((q * o for s in inv for q, o in inv[s]), D(0))
    if rcost:
        resid_rows.append((w, res, rcost))
    print("\n%s  fills=%d  pairs=%d" % (w, len(legs), len(pairs)))
    for lg in legs:
        print("   %s %-4s %5s @ own %s" % (lg["utc"][11:19], lg["side"],
                                           lg["qty"], lg["own"]))
    if pairs:
        print("   pair costs: %s" % ", ".join(str(c) for _, c in pairs))
    print("   pair edge %+0.4f  defensive %+0.4f  residual UP %s DOWN %s "
          "(cost %0.4f)" % (edge, -dfn, res["UP"], res["DOWN"], rcost))

print("\n" + "-" * 76)
print("pairs completed            %d  (%s matched contracts)" % (tot_pairs, tot_matched))
print("pair edge                  %+0.4f" % tot_edge)
print("defensive losses           %+0.4f" % -tot_def)
print("pair economics subtotal    %+0.4f" % (tot_edge - tot_def))
print("\nunmatched residual at window end:")
for w, res, c in resid_rows:
    print("  %s  UP %-5s DOWN %-5s  cost basis %0.4f" % (w, res["UP"], res["DOWN"], c))
print("  total residual cost basis %0.4f" % sum(c for _, _, c in resid_rows))

c0, c1 = D(cash[0]["available_cash_usd"]), D(cash[-1]["available_cash_usd"])
print("\n" + "-" * 76)
print("AUTHORITATIVE CASH")
print("  first reading %s  (%s)" % (c0, cash[0]["ts"][11:19]))
print("  last  reading %s  (%s)" % (c1, cash[-1]["ts"][11:19]))
print("  cash delta    %+0.5f" % (c1 - c0))
print("  gross own-cost deployed across all fills %0.4f" % spent)
