"""P3 PRE-REGISTERED TEST: does L5's FIRST buy follow `open_side_yes = cheaper touch`?

PRE-REGISTRATION (thresholds fixed BEFORE any result is inspected):

  H_cheap : L5 opens fresh inventory on the CHEAPER outcome.
            Our engine applies this DETERMINISTICALLY, so if L5 does the same
            the first-buy fill price must sit below 0.50 essentially always.

  Test statistic: share of markets whose FIRST buy filled at price < 0.50.
  Proxy justification: in a binary Up/Down market the two outcomes price to
  ~1.00, so a fill at p on one side means the other side was ~(1 - p). A buy
  at p < 0.50 IS a buy of the cheaper side. This uses only L5's public fills
  and needs no book reconstruction.

  DECISION RULE, fixed in advance:
      SUPPORTED  >= 0.80 below 0.50
      PARTIAL     0.60 - 0.80
      REFUTED    <= 0.60          (no better than a coin flip plus noise)

  OOS: markets are split by first-buy time, 70% earliest = IN-SAMPLE,
  30% latest = OUT-OF-SAMPLE. Both are reported; the verdict is taken on OOS.

Alternatives scored on the same rows (reported, not used to move the verdict):
  price band at open, TTE at open, horizon, asset, and whether the side opened
  went on to WIN.
"""
import json, gzip, collections, re, sys
sys.path.insert(0,"/home/user/kalshi-arb-bot")
from analysis.l5.redemption import audit as A

SUPPORTED_AT, REFUTED_AT = 0.80, 0.60

first = {}   # cid -> (ts, price, outcome, qty, end, asset, horizon)
red_out = {}
for r in A.iter_activity():
    t=str(r.get("type") or "").upper(); cid=str(r.get("conditionId") or "")
    w=A.window_of(r)
    if t=="TRADE" and str(r.get("side") or "").upper()=="BUY":
        ts=int(r.get("timestamp") or 0); px=float(r.get("price") or 0)
        if px<=0: continue
        cur=first.get(cid)
        if cur is None or ts<cur[0]:
            first[cid]=(ts,px,str(r.get("outcome")),float(r.get("size") or 0),
                        w[2] if w else None, w[0] if w else None, w[1] if w else None)
    elif t=="REDEEM":
        red_out.setdefault(cid,str(r.get("outcome")))

# (cid, ts, px, outcome, qty, end, asset, horizon)
rows=[(cid,)+v for cid,v in first.items() if v[4] is not None]
rows.sort(key=lambda x:x[1])
n=len(rows); cut=int(0.70*n)
IS,OOS=rows[:cut],rows[cut:]
def share(rs): 
    return (sum(1 for x in rs if x[2]<0.50)/len(rs)) if rs else None
print(f"markets with a first buy and a known window: {n}")
print(f"\n=== PRE-REGISTERED TEST: first buy at price < 0.50 ===")
for lbl,rs in (("ALL",rows),("IN-SAMPLE (70% earliest)",IS),("OUT-OF-SAMPLE (30% latest)",OOS)):
    s=share(rs); print(f"   {lbl:28s} n={len(rs):6d}  share below 0.50 = {s:.4f}")
oos=share(OOS)
verdict=("SUPPORTED" if oos>=SUPPORTED_AT else "REFUTED" if oos<=REFUTED_AT else "PARTIAL")
print(f"\n   PRE-REGISTERED VERDICT on OOS ({oos:.4f}): {verdict}")
print(f"   (thresholds fixed in advance: SUPPORTED >={SUPPORTED_AT}, REFUTED <={REFUTED_AT})")

px=sorted(x[2] for x in rows)
print(f"\n   first-buy price distribution: p10={px[n//10]:.3f} p25={px[n//4]:.3f} "
      f"p50={px[n//2]:.3f} p75={px[3*n//4]:.3f} p90={px[9*n//10]:.3f}")
b=collections.Counter()
for x in rows:
    p=x[2]; b["<0.20" if p<0.20 else "0.20-0.35" if p<0.35 else "0.35-0.50" if p<0.50
            else "0.50-0.65" if p<0.65 else "0.65-0.80" if p<0.80 else ">=0.80"]+=1
print("   bands:", {k:f"{v} ({100.0*v/n:.1f}%)" for k,v in sorted(b.items())})

print("\n=== ALTERNATIVES (reported, do not move the verdict) ===")
byh=collections.defaultdict(list); bya=collections.defaultdict(list)
for x in rows: byh[x[7]].append(x[2]); bya[x[6]].append(x[2])
for k,v in sorted(byh.items()):
    print(f"   horizon {k}: n={len(v)} share<0.50={sum(1 for p in v if p<0.50)/len(v):.4f} median={sorted(v)[len(v)//2]:.3f}")
for k,v in sorted(bya.items()):
    print(f"   asset {k}:   n={len(v)} share<0.50={sum(1 for p in v if p<0.50)/len(v):.4f} median={sorted(v)[len(v)//2]:.3f}")
tte=[(x[5]-x[1]) for x in rows if x[5]]
tte_s=sorted(tte); print(f"   TTE at first buy: p10={tte_s[len(tte_s)//10]}s p50={tte_s[len(tte_s)//2]}s p90={tte_s[9*len(tte_s)//10]}s")
lo=[x for x in rows if x[5] and (x[5]-x[1])<=tte_s[len(tte_s)//2]]; hi=[x for x in rows if x[5] and (x[5]-x[1])>tte_s[len(tte_s)//2]]
print(f"   share<0.50 | low TTE (late open)  n={len(lo)}: {share(lo):.4f}")
print(f"   share<0.50 | high TTE (early open) n={len(hi)}: {share(hi):.4f}")
won=[x for x in rows if red_out.get(x[0])==x[3]]
print(f"\n   first-opened side went on to WIN: {len(won)}/{n} ({100.0*len(won)/n:.2f}%)")
cheap_win=[x for x in rows if x[2]<0.50 and red_out.get(x[0])==x[3]]
cheap=[x for x in rows if x[2]<0.50]
if cheap: print(f"   of cheap opens, win rate: {100.0*len(cheap_win)/len(cheap):.2f}%")
dear=[x for x in rows if x[2]>=0.50]; dear_win=[x for x in dear if red_out.get(x[0])==x[3]]
if dear: print(f"   of dear  opens, win rate: {100.0*len(dear_win)/len(dear):.2f}%")
