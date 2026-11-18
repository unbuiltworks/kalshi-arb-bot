"""SETTLED outcome of BTC5m strands: hold vs complete, on real winners.

Answers the owner's Priority 4 instruction for BTC5m with BTC5m's OWN
recorded data -- "do not transfer sports adverse-selection assumptions to
BTC5m without evidence."

RECORDED: every print (side, outcome, price, size), the window from the
slug, and the WINNER from resolutions.jsonl.
MODELLED: that a completion at the market-identity price would have
FILLED. No book exists for these windows, so executable liquidity and
queue position are unobservable and no fill is claimed.
HINDSIGHT is used ONLY to score outcomes, never to make a decision.
"""
import json, collections
from decimal import Decimal as D

DD = "data/l5/competitor_0x8dxd/"
res = {}
with open(DD + "resolutions.jsonl") as f:
    for line in f:
        try: r = json.loads(line)
        except Exception: continue
        if r.get("condition_id") and r.get("winner"):
            res[r["condition_id"]] = r["winner"]

win = collections.defaultdict(list)
cond = {}
with open(DD + "trades.jsonl") as f:
    for line in f:
        try: r = json.loads(line)
        except Exception: continue
        s = r.get("slug") or ""
        if "updown-5m" not in s: continue
        c = r.get("condition_id")
        if c not in res: continue
        try: w = int(s.split("-")[-1])
        except Exception: continue
        t = r["timestamp"] - w
        if 0 <= t <= 300:
            cond[s] = c
            win[s].append((t, (r.get("side") or "").upper(), r["outcome"],
                           float(r["price"]), float(r["size"])))

n = 0; hold_pnl = D(0); comp_pnl = D(0)
held_won = 0; held_lost = 0; costs = []
for s, rows in win.items():
    inv = {"Up": [D(0), D(0)], "Down": [D(0), D(0)]}
    for t, side, o, px, qty in sorted(rows):
        if side == "BUY":
            inv[o][0] += D(str(qty)); inv[o][1] += D(str(qty)) * D(str(px))
        elif inv[o][0] > 0:
            take = min(inv[o][0], D(str(qty)))
            a = inv[o][1] / inv[o][0]
            inv[o][0] -= take; inv[o][1] -= take * a
    held = [k for k in ("Up", "Down") if inv[k][0] > 0]
    if len(held) != 1:
        continue                       # not a strand at window end
    h = held[0]; q, c = inv[h]
    avg = c / q
    if not (D("0.01") <= avg <= D("0.99")):
        continue
    n += 1
    w = res[cond[s]]
    # (a) HOLD to settlement -- RECORDED outcome
    pay = q if h == w else D(0)
    hold_pnl += pay - c
    if h == w: held_won += 1
    else: held_lost += 1
    # (b) COMPLETE at the identity price -- MODELLED fill
    comp_px = D(1) - avg
    pair = avg + comp_px                      # == 1.00 by construction
    comp_pnl += q * (D(1) - pair)
    costs.append(avg)

print(f"BTC5m strands at window end, resolvable: {n:,}")
print(f"  held side WON  {held_won:,}   LOST {held_lost:,}"
      f"   win rate {100.0*held_won/max(1,n):.1f}%")
if costs:
    mc = sum(costs) / len(costs)
    print(f"  mean cost basis of the held leg: {mc:.4f}")
print()
print(f"(a) HOLD to settlement, RECORDED winners : ${hold_pnl:,.4f}")
print(f"(b) COMPLETE at identity, MODELLED fill  : ${comp_pnl:,.4f}")
print()
print("Per-contract, denominator-matched:")
tot_q = sum((inv for inv in []), D(0))
print(f"  holding   {hold_pnl/max(D(1),D(n)):+.4f} per strand")
print(f"  completing{comp_pnl/max(D(1),D(n)):+.4f} per strand")
print()
print("NOTE: (b) is identity-priced, so the pair is exactly 1.00 and its")
print("P&L is 0 by construction. It is the BOUND on completing at par,")
print("not a measurement of achievable completion. The informative")
print("number is (a): what holding actually cost, on real winners.")
