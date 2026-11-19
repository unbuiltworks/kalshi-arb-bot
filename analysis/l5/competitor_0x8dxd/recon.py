import json, collections
D="data/l5/competitor_0x8dxd/"
res={}
with open(D+"resolutions.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except Exception: continue
        if r.get("condition_id") and r.get("winner"): res[r["condition_id"]]=r["winner"]

# per condition -> per outcome -> dict of gross buy/sell shares and cash
B=lambda: {"bs":0.0,"bc":0.0,"ss":0.0,"sc":0.0}
acc=collections.defaultdict(lambda: collections.defaultdict(B))
unres=collections.defaultdict(lambda: collections.defaultdict(B))
with open(D+"trades.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except Exception: continue
        c=r.get("condition_id"); o=r.get("outcome")
        if not c or o is None: continue
        sz=float(r.get("size") or 0); px=float(r.get("price") or 0)
        tgt = acc if c in res else unres
        e=tgt[c][o]
        if (r.get("side") or "").upper()=="BUY": e["bs"]+=sz; e["bc"]+=sz*px
        else:                                     e["ss"]+=sz; e["sc"]+=sz*px

tot=flip=matched=residual=0.0
n_oneside=0; n_short=0; pnl_oneside=0.0
excluded_from_2sided=0.0
for c,sides in acc.items():
    w=res[c]
    # TOTAL for this condition, the authoritative number
    buy_c=sum(v["bc"] for v in sides.values())
    sell_c=sum(v["sc"] for v in sides.values())
    payout=0.0
    for o,v in sides.items():
        net=v["bs"]-v["ss"]
        if o==w and net>0: payout+=net
    pnl = sell_c + payout - buy_c
    tot += pnl
    # DECOMPOSE
    net_sh={}; avgb={}
    ok=True
    for o,v in sides.items():
        net_sh[o]=v["bs"]-v["ss"]
        if v["bs"]>0: avgb[o]=v["bc"]/v["bs"]
        else: ok=False
        if net_sh[o]<-1e-9: n_short+=1
    # flip component: shares bought then sold
    f_c=0.0
    for o,v in sides.items():
        if v["ss"]>0 and v["bs"]>0:
            f_c += v["sc"] - v["ss"]*avgb[o]
    flip += f_c
    two = ok and len(sides)>=2 and min(net_sh.values())>0
    if two:
        mp=min(net_sh.values())
        pc=sum(avgb.values())
        matched += mp*(1.0-pc)
        for o in sides:
            s=net_sh[o]-mp
            if s>0: residual += s*((1.0 if o==w else 0.0)-avgb[o])
    else:
        n_oneside+=1
        pnl_oneside += pnl - f_c
        excluded_from_2sided += pnl
print(f"=== AUTHORITATIVE TOTAL, {len(acc):,} resolvable conditions ===")
print(f"  trading P&L (sells + payout - buys) : ${tot:14,.2f}")
print()
print(f"=== DECOMPOSITION (must sum to the total) ===")
print(f"  matched-pair contribution           : ${matched:14,.2f}")
print(f"  residual contribution               : ${residual:14,.2f}")
print(f"  flip contribution (bought->sold)    : ${flip:14,.2f}")
print(f"  one-sided / unpaired conditions     : ${pnl_oneside:14,.2f}   (n={n_oneside:,})")
s=matched+residual+flip+pnl_oneside
print(f"  --------------------------------------------------------")
print(f"  sum                                 : ${s:14,.2f}")
print(f"  tie-out residual                    : ${tot-s:14,.2f}")
print(f"  net-short-side anomalies            : {n_short:,}")
print()
ub=sum(v["bc"] for s_ in unres.values() for v in s_.values())
us=sum(v["sc"] for s_ in unres.values() for v in s_.values())
print(f"=== NOT REALIZED, NOT PROFIT ===")
print(f"  unresolved conditions               : {len(unres):,}")
print(f"  cash paid into them                 : ${ub:14,.2f}")
print(f"  cash received back                  : ${us:14,.2f}")
print(f"  open/unknown (NO resolution in tape): ${us-ub:14,.2f}  <- UNKNOWN, excluded")
print()
print(f"=== ABSENT FROM THE TAPE ENTIRELY ===")
print("  MERGE rows (pair -> $1)   : no such activity type captured")
print("  per-trade fees            : not present on trade rows")
