"""Aggregate MAKER_SHADOW_WINDOW records (Polymarket L5 maker shadow, Railway
service l5-book-shadow) by market family. READ-ONLY evidence. Modelled fills
are never executions."""
import json, glob, sys, re, collections, statistics as st
files=sorted(glob.glob(sys.argv[1]+"/mcp-Railway-get-logs-*.txt"))
recs={}
caps=[]
for p in files:
    try: d=json.load(open(p))
    except Exception: continue
    rows=d.get("deploy") or []
    if len(rows)>=500: caps.append((p.split("-")[-1][:13], len(rows)))
    for r in rows:
        m=r["message"]
        if not m.startswith("MAKER_SHADOW_WINDOW "): continue
        try: o=json.loads(m[len("MAKER_SHADOW_WINDOW "):])
        except Exception: continue
        recs[(o.get("slug"), r["timestamp"][:19])]=o
print("files:",len(files),"unique window records:",len(recs))
if caps: print("SLICES AT 500-ROW CAP (truncated):",caps)
fam=collections.defaultdict(list)
for (slug,_),o in recs.items():
    m=re.match(r'^([a-z]+-updown-\d+m)-(\d+)$', slug or "")
    if m: fam[m.group(1)].append(o)
print()
hdr=("family","windows","L5_wins_any_fill","L5_fills_tot","L5_qty_tot","L5_matched>0",
     "our_orders","our_mfills","wins_mfill>0","tier_price_elig","tier_queue","tier_mfill","med_secs_obs")
def f(v,n=1):
    return ("%."+str(n)+"f")%v if isinstance(v,float) else str(v)
print("%-16s %7s %8s %9s %10s %8s %9s %8s %9s %11s %9s %9s %8s"%hdr)
summary={}
for k in sorted(fam):
    rs=fam[k]
    l5f=sum(r["L5"].get("fills") or 0 for r in rs)
    l5q=sum((r["L5"].get("qty_up") or 0)+(r["L5"].get("qty_down") or 0) for r in rs)
    l5any=sum(1 for r in rs if (r["L5"].get("fills") or 0)>0)
    l5m=sum(1 for r in rs if (r["L5"].get("matched") or 0)>0)
    oi=sum(r["OURS"].get("orders_intended") or 0 for r in rs)
    of=sum(r["OURS"].get("modelled_fills") or 0 for r in rs)
    ow=sum(1 for r in rs if (r["OURS"].get("modelled_fills") or 0)>0)
    tp=sum(r["TIERS_contracts"].get("price_eligible") or 0 for r in rs)
    tq=sum(r["TIERS_contracts"].get("queue_possible") or 0 for r in rs)
    tm=sum(r["TIERS_contracts"].get("modelled_fill") or 0 for r in rs)
    so=st.median([r.get("secs_observed") or 0 for r in rs])
    print("%-16s %7d %8d %9d %10.0f %8d %9d %8d %9d %11.0f %9.0f %9.0f %8.0f"%(
        k,len(rs),l5any,l5f,l5q,l5m,oi,of,ow,tp,tq,tm,so))
    summary[k]=dict(windows=len(rs),l5_windows_with_fill=l5any,l5_fills=l5f,l5_qty=l5q,
        l5_windows_matched=l5m,our_orders_intended=oi,our_modelled_fills=of,
        our_windows_with_modelled_fill=ow,tier_price_eligible=tp,tier_queue_possible=tq,
        tier_modelled_fill=tm,median_secs_observed=so)
# economics: our modelled basis (entry price) and residual/matched, per family
print()
print("%-16s %22s %22s %18s"%("family","our_basis_up med/n","our_basis_down med/n","our_pair_cost n"))
for k in sorted(fam):
    rs=fam[k]
    bu=[r["OURS"]["basis_up"] for r in rs if r["OURS"].get("basis_up") is not None]
    bd=[r["OURS"]["basis_down"] for r in rs if r["OURS"].get("basis_down") is not None]
    pc=[r["OURS"]["pair_cost"] for r in rs if r["OURS"].get("pair_cost") is not None]
    print("%-16s %22s %22s %18s"%(k,
      ("%.4f / %d"%(st.median(bu),len(bu))) if bu else "- / 0",
      ("%.4f / %d"%(st.median(bd),len(bd))) if bd else "- / 0",
      ("%.4f / %d"%(st.median(pc),len(pc))) if pc else "- / 0"))
# L5 pair cost (what the target trader actually achieved) per family
print()
print("%-16s %26s %22s"%("family","L5_pair_cost med/min/max/n","L5_basis_up med/n"))
for k in sorted(fam):
    rs=fam[k]
    pc=[r["L5"]["pair_cost"] for r in rs if r["L5"].get("pair_cost") is not None]
    bu=[r["L5"]["basis_up"] for r in rs if r["L5"].get("basis_up") is not None]
    print("%-16s %26s %22s"%(k,
      ("%.4f/%.4f/%.4f/%d"%(st.median(pc),min(pc),max(pc),len(pc))) if pc else "- / 0",
      ("%.4f / %d"%(st.median(bu),len(bu))) if bu else "- / 0"))
json.dump(summary, open(sys.argv[2],"w"), indent=2)
ts=sorted(k[1] for k in recs)
print("\nwindow-record time span:", ts[0], "->", ts[-1])
