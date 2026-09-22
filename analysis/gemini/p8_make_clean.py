import json, sys, datetime as dt
from collections import defaultdict
from fractions import Fraction as F
SP="/tmp/claude-0/-home-user-kalshi-arb-bot/198d2e31-d6d4-505d-b6e2-8eb3a285708f/scratchpad"
raw=json.load(open(SP+"/bulk.json"))
def iso(x):
    try: return dt.datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception: return None
LIM={"5m":(3,8),"15m":(12,20),"1h":(50,70)}
def dedupe(prints):
    g=defaultdict(list)
    for r in prints: g[int(r[0])].append(r)
    out=[]; prev=None
    for t in sorted(g):
        rows=g[t]; used=set()
        for i,a in enumerate(rows):
            if i in used: continue
            twin=None
            for j,b in enumerate(rows):
                if j<=i or j in used: continue
                if F(a[2])+F(b[2])==1 and F(a[3])==F(b[3]): twin=j; break
            if twin is None:
                out.append(a); used.add(i); prev=F(a[2]); continue
            b=rows[twin]
            keep=a if (prev is None or abs(F(a[2])-prev)<=abs(F(b[2])-prev)) else b
            out.append(keep); used.add(i); used.add(twin); prev=F(keep[2])
    return out
out=[]; drop_life=0; drop_twin=0
for c in raw:
    a,b=iso(c.get("start")), iso(c.get("expiry"))
    L=((b-a).total_seconds()/60) if (a and b and b>a) else None
    lo,hi=LIM[c["duration"]]
    if L is None or not (lo<=L<=hi): drop_life+=1; continue
    n0=len(c["prints"]); c=dict(c); c["prints"]=dedupe(c["prints"])
    drop_twin+=n0-len(c["prints"]); out.append(c)
json.dump(out, open(SP+"/bulk_v2.json","w"))
print(f"contracts {len(out)} (dropped {drop_life} mis-lifed); mirrored twins dropped {drop_twin}")
