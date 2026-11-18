import json, collections
D="data/l5/competitor_0x8dxd/"
res={}
with open(D+"resolutions.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except Exception: continue
        if r.get("condition_id") and r.get("winner"): res[r["condition_id"]]=r["winner"]
B=lambda: {"bs":0.0,"bc":0.0,"ss":0.0,"sc":0.0}
acc=collections.defaultdict(lambda: collections.defaultdict(B))
with open(D+"trades.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except Exception: continue
        c=r.get("condition_id"); o=r.get("outcome")
        if not c or o is None or c not in res: continue
        sz=float(r.get("size") or 0); px=float(r.get("price") or 0)
        e=acc[c][o]
        if (r.get("side") or "").upper()=="BUY": e["bs"]+=sz; e["bc"]+=sz*px
        else: e["ss"]+=sz; e["sc"]+=sz*px

two=set(); 
for c,s in acc.items():
    if len(s)>=2 and all(v["bs"]>0 for v in s.values()) and min(v["bs"]-v["ss"] for v in s.values())>0:
        two.add(c)
def pnl_old(c):   # w3 basis: payout may go negative
    s=acc[c]; w=res[c]
    net_cash=sum(v["bc"]-v["sc"] for v in s.values())
    nw=s.get(w,B()); payout=(nw["bs"]-nw["ss"])
    return payout-net_cash
def pnl_new(c):   # floored payout
    s=acc[c]; w=res[c]
    net_cash=sum(v["bc"]-v["sc"] for v in s.values())
    nw=s.get(w,B()); payout=max(0.0, nw["bs"]-nw["ss"])
    return payout-net_cash
old_all=sum(pnl_old(c) for c in acc)
old_two=sum(pnl_old(c) for c in two)
old_rest=old_all-old_two
sells_in_two=sum(v["sc"] for c in two for v in acc[c].values())
short_conds=[c for c in acc if min(v["bs"]-v["ss"] for v in acc[c].values())< -1e-9]
print(f"total (old basis, 3,991)            : ${old_all:14,.2f}   <- the $122,002.39 figure")
print(f"  of which the 3,025 two-sided set  : ${old_two:14,.2f}")
print(f"  of which the other {len(acc)-len(two):,} conditions : ${old_rest:14,.2f}")
print()
print(f"buy-only decomposition reported     : $      91,428.55")
print(f"ORIGINAL GAP                        : ${old_all-91428.55:14,.2f}")
print(f"  (a) conditions omitted entirely   : ${old_rest:14,.2f}")
print(f"  (b) sell proceeds ignored by basis: ${old_two-91428.55:14,.2f}")
print(f"      sell cash inside two-sided set: ${sells_in_two:14,.2f}")
print()
print(f"=== DATA COMPLETENESS TEST ===")
print(f"conditions where SELLS EXCEED BUYS on a side (impossible without")
print(f"pre-existing inventory, i.e. MISSING BUYS): {len(short_conds):,} of {len(acc):,} ({100.0*len(short_conds)/len(acc):.1f}%)")
print(f"total (floored payout, same 3,991)  : ${sum(pnl_new(c) for c in acc):14,.2f}")
print(f"swing from that one treatment alone : ${sum(pnl_new(c) for c in acc)-old_all:14,.2f}")
