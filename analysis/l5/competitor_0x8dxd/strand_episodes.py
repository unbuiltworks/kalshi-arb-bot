"""EVERY strand episode in a 5m window, not just the ones that survived.

WHY THIS REPLACES strand_settled.py's HEADLINE. That script measured
strands still one-sided at window END and found the held side won 67.9%.
With 21,126 SELL prints in the tape, legs sold off before the end were
excluded, so a wallet that sells losers and holds winners would produce
exactly that number from selection alone. This walks the whole window and
records an episode the moment inventory goes one-sided, however it later
ends, which removes that bias.

AN EPISODE opens when exactly one side is held and closes when the other
side is acquired (PAIRED), the held side is sold out (SOLD), or the window
ends (HELD_TO_END). Only HELD_TO_END is scored against the resolution.

RECORDED: prints (side, outcome, price, size), window from the slug,
winner from resolutions.jsonl. MODELLED: nothing. No fills are assumed and
no book is reconstructed. This is 0x8dxd's behaviour, not ours.
"""
import json, collections
from decimal import Decimal as D

DD = "data/l5/competitor_0x8dxd/"
res, asset = {}, {}
with open(DD + "resolutions.jsonl") as f:
    for line in f:
        try: r = json.loads(line)
        except Exception: continue
        if r.get("condition_id") and r.get("winner"):
            res[r["condition_id"]] = r["winner"]

win = collections.defaultdict(list); cond = {}
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
            asset[s] = (r.get("title") or "?").split()[0]
            win[s].append((t, (r.get("side") or "").upper(),
                           r["outcome"], float(r["price"]), float(r["size"])))

end = collections.Counter(); by_asset = collections.Counter()
held_won = held_lost = 0
hold_pnl = D(0); paired_cost = []; durations = []
for s, rows in win.items():
    inv = {"Up": [D(0), D(0)], "Down": [D(0), D(0)]}
    ep = None                      # (side, t_open)
    for t, side, o, px, qty in sorted(rows):
        if side == "BUY":
            inv[o][0] += D(str(qty)); inv[o][1] += D(str(qty)) * D(str(px))
        elif inv[o][0] > 0:
            take = min(inv[o][0], D(str(qty)))
            a = inv[o][1] / inv[o][0]
            inv[o][0] -= take; inv[o][1] -= take * a
        held = [k for k in ("Up", "Down") if inv[k][0] > 0]
        if ep is None and len(held) == 1:
            ep = (held[0], t)                       # episode opens
        elif ep is not None:
            if len(held) == 2:                      # paired
                end["PAIRED"] += 1; by_asset[(asset[s], "PAIRED")] += 1
                a_up = inv["Up"][1] / inv["Up"][0]
                a_dn = inv["Down"][1] / inv["Down"][0]
                paired_cost.append(a_up + a_dn)
                durations.append(t - ep[1]); ep = None
            elif len(held) == 0:                    # sold out
                end["SOLD"] += 1; by_asset[(asset[s], "SOLD")] += 1
                durations.append(t - ep[1]); ep = None
    if ep is not None:                              # ran to the window end
        h = ep[0]; q, c = inv[h]; avg = c / q
        end["HELD_TO_END"] += 1; by_asset[(asset[s], "HELD_TO_END")] += 1
        w = res[cond[s]]
        hold_pnl += (q if h == w else D(0)) - c
        if h == w: held_won += 1
        else: held_lost += 1

tot = sum(end.values())
print(f"STRAND EPISODES across {len(win):,} resolvable 5m windows: {tot:,}")
for k in ("PAIRED", "SOLD", "HELD_TO_END"):
    print(f"  {k:12} {end[k]:6,}  ({100.0*end[k]/max(1,tot):5.1f}%)")
print()
n = held_won + held_lost
print(f"HELD_TO_END scored on RECORDED winners: {n:,}")
print(f"  won {held_won:,}  lost {held_lost:,}   win rate "
      f"{100.0*held_won/max(1,n):.1f}%")
print(f"  P&L of holding those to settlement: ${hold_pnl:,.2f}")
if paired_cost:
    pc = sorted(paired_cost)
    print(f"\nPAIRED episodes: combined pair cost")
    print(f"  median {pc[len(pc)//2]:.4f}   mean "
          f"{sum(pc)/len(pc):.4f}   at/below par "
          f"{100.0*sum(1 for x in pc if x<=1)/len(pc):.1f}%")
if paired_cost:
    pc = sorted(paired_cost)
    print("\nWHAT EACH CANDIDATE CEILING WOULD REFUSE, of 3,725 real pairings:")
    for c in ("0.96","0.97","0.98","0.99","1.00"):
        from decimal import Decimal as _D
        ok = sum(1 for x in pc if x <= _D(c))
        print(f"  ceiling {c}: permits {ok:5,} ({100.0*ok/len(pc):5.1f}%), "
              f"refuses {len(pc)-ok:5,} ({100.0*(len(pc)-ok)/len(pc):5.1f}%)")
if durations:
    dn = sorted(durations)
    print(f"\nepisode duration s: p50 {dn[len(dn)//2]:.0f}  "
          f"p90 {dn[int(0.9*len(dn))]:.0f}")
print("\nBY ASSET:")
assets = sorted({a for a, _ in by_asset})
for a in assets:
    row = [by_asset[(a, k)] for k in ("PAIRED", "SOLD", "HELD_TO_END")]
    if sum(row) >= 25:
        print(f"  {a:10} paired {row[0]:5,}  sold {row[1]:5,}  "
              f"held {row[2]:5,}")
