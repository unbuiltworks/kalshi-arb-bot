import json, collections, statistics
D="data/l5/competitor_0x8dxd/"
res={}
with open(D+"resolutions.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except Exception: continue
        if r.get("condition_id") and r.get("winner"): res[r["condition_id"]]=r["winner"]
# BUY-ONLY aggregation: gross buys per side, so sells cannot distort average cost
buys=collections.defaultdict(lambda: collections.defaultdict(lambda:[0.0,0.0]))
net =collections.defaultdict(lambda: collections.defaultdict(lambda:[0.0,0.0]))
with open(D+"trades.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except Exception: continue
        c=r.get("condition_id"); o=r.get("outcome")
        if not c or o is None or c not in res: continue
        sz=float(r.get("size") or 0); px=float(r.get("price") or 0)
        if (r.get("side") or "").upper()=="BUY":
            b=buys[c][o]; b[0]+=sz; b[1]+=sz*px
            n=net[c][o];  n[0]+=sz; n[1]+=sz*px
        else:
            n=net[c][o];  n[0]-=sz; n[1]-=sz*px

costs=[]; wts=[]; matched_pnl=0.0; resid_pnl=0.0
for c,sides in buys.items():
    if len(sides)<2: continue
    sh={o:v[0] for o,v in sides.items()}
    if min(sh.values())<=0: continue
    avg={o:(sides[o][1]/sides[o][0]) for o in sides}
    pc=sum(avg.values())
    mp=min(sh.values())
    costs.append(pc); wts.append(mp)
    # matched shares pay exactly 1.0 and cost pc
    matched_pnl += mp*(1.0-pc)
    # residual: surplus side shares, cost at its average, pay 1 if it won
    w=res[c]
    for o in sides:
        surplus=sh[o]-mp
        if surplus>0:
            resid_pnl += surplus*((1.0 if o==w else 0.0)-avg[o])
costs_sorted=sorted(costs)
wmean=sum(c*w for c,w in zip(costs,wts))/sum(wts)
print(f"conditions with BUYS on both sides : {len(costs):,}")
print(f"combined pair cost  mean           : {statistics.mean(costs):.4f}")
print(f"                    median         : {statistics.median(costs):.4f}")
print(f"                    share-weighted : {wmean:.4f}")
print(f"                    p10 / p90      : {costs_sorted[len(costs)//10]:.4f} / {costs_sorted[9*len(costs)//10]:.4f}")
print(f"                    share <= 1.00  : {100.0*sum(1 for c in costs if c<=1.0)/len(costs):.1f}%")
print()
print(f"DECOMPOSITION on those conditions (buy-side basis)")
print(f"  matched-pair P&L   : ${matched_pnl:14,.2f}")
print(f"  residual P&L       : ${resid_pnl:14,.2f}")
print(f"  sum                : ${matched_pnl+resid_pnl:14,.2f}")
