import json, glob, sys, re, collections, statistics as st
recs={}
for p in sorted(glob.glob(sys.argv[1]+"/mcp-Railway-get-logs-*.txt")):
    try: d=json.load(open(p))
    except Exception: continue
    for r in d.get("deploy") or []:
        m=r["message"]
        if not m.startswith("MAKER_SHADOW_WINDOW "): continue
        try: o=json.loads(m[len("MAKER_SHADOW_WINDOW "):])
        except Exception: continue
        recs[(o.get("slug"), r["timestamp"][:19])]=o
fam=collections.defaultdict(list)
for (slug,_),o in recs.items():
    m=re.match(r'^([a-z]+-updown-\d+m)-(\d+)$', slug or "")
    if m: fam[m.group(1)].append(o)

WIN={'btc-updown-15m':900,'eth-updown-15m':900,'btc-updown-5m':300,'eth-updown-5m':300}
print("OBSERVABILITY (how much of each window the collector actually watched)")
print("%-16s %8s %12s %12s %10s"%("family","windows","med_obs_s","nominal_s","obs_frac"))
for k in sorted(fam):
    obs=[r.get("secs_observed") or 0 for r in fam[k]]
    print("%-16s %8d %12.0f %12d %10.2f"%(k,len(obs),st.median(obs),WIN[k],st.median(obs)/WIN[k]))

print("\nL5 ACTIVITY NORMALIZED PER OBSERVED HOUR (target trader's own behaviour)")
print("%-16s %12s %14s %14s %16s"%("family","obs_hours","L5_fills/hr","L5_qty/hr","L5_matched_qty/hr"))
for k in sorted(fam):
    rs=fam[k]; h=sum((r.get("secs_observed") or 0) for r in rs)/3600
    fl=sum(r["L5"].get("fills") or 0 for r in rs)
    q=sum((r["L5"].get("qty_up") or 0)+(r["L5"].get("qty_down") or 0) for r in rs)
    mq=sum(r["L5"].get("matched") or 0 for r in rs)
    print("%-16s %12.1f %14.1f %14.0f %16.1f"%(k,h,fl/h,q/h,mq/h))

print("\nL5 MATCHED-PAIR ECONOMICS (pair_cost = sum of each side's own VWAP basis; <1.00 = locked gross edge)")
print("%-16s %8s %10s %10s %10s %10s %10s %12s"%("family","n_pairs","frac<1.00","frac<0.99","frac<0.97","p10","median","mean_edge_c"))
for k in sorted(fam):
    pc=[r["L5"]["pair_cost"] for r in fam[k] if r["L5"].get("pair_cost") is not None]
    if not pc: print("%-16s %8d"%(k,0)); continue
    pcs=sorted(pc); n=len(pcs)
    edge=[(1.0-x)*100 for x in pcs]
    print("%-16s %8d %10.3f %10.3f %10.3f %10.4f %10.4f %12.2f"%(k,n,
      sum(1 for x in pcs if x<1.0)/n, sum(1 for x in pcs if x<0.99)/n,
      sum(1 for x in pcs if x<0.97)/n, pcs[int(.1*(n-1))], st.median(pcs),
      sum(edge)/n))

print("\nOUR SHADOW EXECUTION REALISM (tiers, contracts; modelled fills are NOT executions)")
print("%-16s %10s %12s %12s %14s %14s %12s"%("family","orders","mod_fills","price_elig","queue_poss","mod_fill_qty","fill/intent"))
for k in sorted(fam):
    rs=fam[k]
    oi=sum(r["OURS"].get("orders_intended") or 0 for r in rs)
    of=sum(r["OURS"].get("modelled_fills") or 0 for r in rs)
    tp=sum(r["TIERS_contracts"].get("price_eligible") or 0 for r in rs)
    tq=sum(r["TIERS_contracts"].get("queue_possible") or 0 for r in rs)
    tm=sum(r["TIERS_contracts"].get("modelled_fill") or 0 for r in rs)
    print("%-16s %10d %12d %12.0f %14.0f %14.0f %12.3f"%(k,oi,of,tp,tq,tm,(of/oi) if oi else 0))

print("\nOUR MODELLED MATCHED PAIRS (how often the clone itself locked a two-sided position)")
print("%-16s %10s %12s %12s %14s"%("family","windows","our_matched>0","our_pair_cost_n","med_pair_cost"))
for k in sorted(fam):
    rs=fam[k]
    mm=sum(1 for r in rs if (r["OURS"].get("matched") or 0)>0)
    pc=[r["OURS"]["pair_cost"] for r in rs if r["OURS"].get("pair_cost") is not None]
    print("%-16s %10d %12d %12d %14s"%(k,len(rs),mm,len(pc),
      ("%.4f"%st.median(pc)) if pc else "-"))

print("\nRESIDUAL (one-sided) EXPOSURE our shadow would have carried to settlement")
print("%-16s %14s %14s %16s"%("family","our_resid_tot","our_qty_tot","resid_frac"))
for k in sorted(fam):
    rs=fam[k]
    rq=sum(r["OURS"].get("residual") or 0 for r in rs)
    tq=sum((r["OURS"].get("qty_up") or 0)+(r["OURS"].get("qty_down") or 0) for r in rs)
    print("%-16s %14.1f %14.1f %16s"%(k,rq,tq,("%.3f"%(rq/tq)) if tq else "-"))
