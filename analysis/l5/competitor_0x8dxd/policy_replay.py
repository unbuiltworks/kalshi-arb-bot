"""What the market-progress completion policy would do at RECORDED strands.

RECORDED vs MODELLED, and the line matters. Recorded: the window, the
side, the price and the size of every print, and the market's start/end
from its slug. Modelled: that a resting completion at the complement's
last observed print would have FILLED, and p_win taken from that same
print as a market-implied probability. No recorded book exists for these
windows, so queue position and executable depth are NOT modelled and no
fill is claimed. p_win uses only information available at the decision
instant -- never the resolution.
"""
import os, json, collections
from decimal import Decimal as D
os.environ["L5_COMPLETION_POLICY"] = "ON"
from src import l5_completion_policy as P

DD = "data/l5/competitor_0x8dxd/"
win = collections.defaultdict(list)
with open(DD + "trades.jsonl") as f:
    for line in f:
        try: r = json.loads(line)
        except Exception: continue
        s = r.get("slug") or ""
        if "updown-5m" not in s: continue
        try: w = int(s.split("-")[-1])
        except Exception: continue
        t = r["timestamp"] - w
        if 0 <= t <= 300:
            win[s].append((t, (r.get("side") or "").upper(), r["outcome"],
                           float(r["price"]), float(r["size"]), w))

def run(patient_ceiling):
    stages = collections.Counter(); acts = collections.Counter()
    strands = 0; refusals = collections.Counter(); locked = D(0)
    return stages, acts, strands, refusals, locked, patient_ceiling

CEILINGS = (P.PATIENT_CEILING, P.COMPARATOR_PATIENT_CEILING)
results = {}
for CEIL in CEILINGS:
  stages = collections.Counter(); acts = collections.Counter()
  strands = 0; refusals = collections.Counter(); locked = D(0)
  for s in sorted(win)[:400]:
      rows = sorted(win[s])
      inv = {"UP": [D(0), D(0)], "DOWN": [D(0), D(0)]}
      last = {}
      for t, side, outcome, px, qty, w in rows:
          o = "UP" if outcome == "Up" else "DOWN"
          if side == "BUY":
              last[o] = px
              inv[o][0] += D(str(qty)); inv[o][1] += D(str(qty)) * D(str(px))
          # a strand: one side held, the other flat
          held = [k for k in ("UP", "DOWN") if inv[k][0] > 0]
          if len(held) != 1:
              continue
          h = held[0]
          #: A STRAND MEANS THEY NEVER BOUGHT THE OTHER SIDE, so there is no
          #: print for it. The complement's executable price is taken from
          #: the market identity instead -- DOWN bid = 1 - UP ask -- applied
          #: to the held side's own last print. PROXY, not a recorded price,
          #: and the same caveat as the book proxy: no depth, no queue.
          if h not in last:
              continue
          comp = max(0.01, min(0.99, 1.0 - last[h]))
          strands += 1
          prog = P.progress(now=w + t, start=w, end=w + 300)
          avg = inv[h][1] / inv[h][0]
          d = P.decide(held_side=h, held_qty=str(inv[h][0]),
                       held_avg_cost=str(avg),
                       complete_bid=str(comp),
                       p_win=str(min(0.99, max(0.01, last[h]))),
                       prog=prog, stranded_now=0,
                     patient_ceiling=CEIL)
          stages[d["stage"]["stage_name"]] += 1
          acts[d["action"]] += 1
          mc = (d["options"].get("MAKER_COMPLETE") or {})
          if mc.get("permissible") is not True and mc.get("why"):
              refusals[mc["why"]] += 1
  results[str(CEIL)] = (dict(stages), dict(acts), strands,
                        dict(refusals), str(locked))

print("RECORDED strand decision points per run, 400 BTC5m windows")
print("RECORDED: window, side, price, size, slug timing.")
print("MODELLED: that a resting completion would have FILLED, the")
print("complement price from the market identity, p_win from the held")
print("side's own print. NO fill is claimed and no decision below is a fill.")
for ceil, (stg, act, n, ref, _lk) in results.items():
    tag = "AUTHORIZED" if ceil == str(P.PATIENT_CEILING) else "COMPARATOR"
    print(f"\n=== patient ceiling {ceil}  ({tag}) ===")
    print(f"  decision points {n:,}")
    print("  stages   " + "  ".join(
        f"{k} {stg.get(k,0):,}" for k in
        ("PATIENT","STEP","BREAKEVEN","FINAL")))
    print("  actions  " + "  ".join(f"{k} {v:,}" for k, v in
                                    sorted(act.items())))
    if ref:
        print("  refusals " + "  ".join(f"{k} {v:,}" for k, v in
                                        sorted(ref.items())))
