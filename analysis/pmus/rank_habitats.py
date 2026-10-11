"""Two rankings -- category/family and individual market -- across both venues.

Heterogeneity, not venue-wide averages. Every row carries its own sample size;
nothing is pooled across venue, sport, market family or live-state.

Reach is the CANONICAL definition (08d9fc8b), carried verbatim: from state
(b0,a0), BUY-reach is the ask descending to <= b0 within the horizon and
SELL-reach is the bid rising to >= a0. States whose series ends before the
horizon are RIGHT-CENSORED and EXCLUDED, never scored as misses.

Two metrics are added for this mandate, both stated as definitions rather than
inherited:
  two_sided_R : % of states where BOTH directions reach inside 120s.
  recycle/hr  : NON-OVERLAPPING buy-reach events per observed hour. After a
                reach at t_hit the scan resumes AT t_hit, so one long wait
                cannot be counted many times. This is the "how often does
                opposing flow actually come back" rate that continuous
                inventory lives on.
"""
import sys, json, collections, statistics as st

SCR="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
SOURCES={"POLYMARKET_US":SCR+"/nfl_sunday.jsonl",
         "POLYMARKET_INTERNATIONAL":SCR+"/intl_live.jsonl"}

# canonical corrected reference, 08d9fc8b. International BTC15m CRYPTO.
REF={"spread":0.010,"le1c":96.3,"reach120":80.6,"med_reach":16.0,"move":4.88}
GEM={"spread":0.280,"le1c":0.2,"reach120":24.4,"med_reach":72.0,"move":5.18}

def load():
    rows=[]
    for venue,path in SOURCES.items():
        try: fh=open(path)
        except OSError: continue
        for l in fh:
            try: d=json.loads(l)
            except Exception: continue
            if d.get("_marker"): continue
            if d.get("bid") is None or d.get("ask") is None: continue
            if d["ask"]<=d["bid"]: continue
            d.setdefault("venue",venue)
            rows.append(d)
    return rows

def series(rows):
    by=collections.defaultdict(list)
    for r in rows: by[(r["venue"],r["slug"])].append(r)
    out=[]
    for _,rs in by.items():
        rs.sort(key=lambda r:r["t"])
        gaps=[b["t"]-a["t"] for a,b in zip(rs,rs[1:])]
        thr=max(45.0,3.0*(st.median(gaps) if gaps else 8.0))
        cur=[]
        for r in rs:
            if cur and ((r["t"]-cur[-1]["t"])>thr
                        or bool(r.get("live"))!=bool(cur[-1].get("live"))):
                if len(cur)>=4: out.append(cur)
                cur=[]
            cur.append(r)
        if len(cur)>=4: out.append(cur)
    return out

def cadence(se):
    g=[b["t"]-a["t"] for a,b in zip(se,se[1:])]
    return st.median(g) if g else 8.0

def reach(se,horizon):
    outb=[];outs=[]
    tend=se[-1]["t"]
    for i,r in enumerate(se):
        if (tend-r["t"])<horizon: continue
        b0,a0=r["bid"],r["ask"]; hb=hs=None
        for j in range(i+1,len(se)):
            dt=se[j]["t"]-r["t"]
            if dt>horizon: break
            if hb is None and se[j]["ask"]<=b0: hb=dt
            if hs is None and se[j]["bid"]>=a0: hs=dt
            if hb is not None and hs is not None: break
        outb.append(hb); outs.append(hs)
    return outb,outs

def recycles(se,horizon=120.0):
    """Non-overlapping buy-reach events; returns (count, observed_hours)."""
    n=0; i=0; L=len(se)
    while i<L:
        b0=se[i]["bid"]; hit=None
        for j in range(i+1,L):
            if se[j]["t"]-se[i]["t"]>horizon: break
            if se[j]["ask"]<=b0: hit=j; break
        if hit is None: i+=1
        else: n+=1; i=hit          # resume AT the hit -> never double-counts
    hours=(se[-1]["t"]-se[0]["t"])/3600.0
    return n,hours

def pct(v): return None if not v else round(100.0*sum(1 for x in v if x is not None)/len(v),1)

def metrics(sl):
    sp=[];db=[];da=[];mv=[];osc=[];cads=[];obs=0.0
    R={30:[],60:[],120:[]};S=[];both=[];waits=[];rc=0;rh=0.0
    for se in sl:
        if len(se)<4: continue
        obs+=se[-1]["t"]-se[0]["t"]; cads.append(cadence(se))
        sp+=[r["ask"]-r["bid"] for r in se]
        db+=[r["bid_qty"] for r in se if r.get("bid_qty")]
        da+=[r["ask_qty"] for r in se if r.get("ask_qty")]
        mins=(se[-1]["t"]-se[0]["t"])/60.0
        if mins>0:
            ch=sum(1 for i in range(1,len(se))
                   if se[i]["bid"]!=se[i-1]["bid"] or se[i]["ask"]!=se[i-1]["ask"])
            mv.append(ch/mins)
            mids=[(r["bid"]+r["ask"])/2 for r in se]
            osc.append(sum(abs(b-a) for a,b in zip(mids,mids[1:]))/mins)
        cs=cadence(se)
        for h in (30,60,120):
            if h<cs: continue
            b,_=reach(se,h); R[h]+=b
        b120,s120=reach(se,120)
        S+=s120; waits+=[x for x in b120 if x is not None]
        both+=[1 if (x is not None and y is not None) else None
               for x,y in zip(b120,s120)]
        n,h=recycles(se); rc+=n; rh+=h
    if not sp: return None
    return {"markets":len({(x[0]["venue"],x[0]["slug"]) for x in sl if len(x)>=4}),"segments":len(sl),"states":len(sp),
      "obs_min":round(obs/60.0,1),"cad":round(st.median(cads),1) if cads else None,
      "spread_med":round(st.median(sp),4),
      "le1c":round(100.0*sum(1 for x in sp if x<=0.0101)/len(sp),1),
      "le2c":round(100.0*sum(1 for x in sp if x<=0.0201)/len(sp),1),
      "depth_b":round(st.median(db),1) if db else None,
      "move_min":round(st.median(mv),2) if mv else None,
      "osc_min":round(st.median(osc),4) if osc else None,
      "n30":len(R[30]),"n60":len(R[60]),"n120":len(R[120]),
      "r30":pct(R[30]),"r60":pct(R[60]),"r120":pct(R[120]),
      "sell_r120":pct(S),"two_sided":pct(both),
      "med_reach":round(st.median(waits),1) if waits else None,
      "recycle_hr":round(rc/rh,2) if rh>0 else None,"recycle_n":rc}

def dist(m):
    """Distance to the International BTC15m reference. Reach is PRIMARY.

    The latency term is SCALED BY THE REACH RATE. Median time-to-reach is
    conditioned on reaching at all, so a market that reaches 5% of the time
    with a fast median is not close to the reference -- it is a market that
    almost never recycles. Unscaled, that market ranked second. This is a
    correction to the metric's definition, not a threshold tuned to results:
    no habitat threshold moved.
    """
    if m["r120"] is None: return None
    d=3.0*abs(m["r120"]-REF["reach120"])/REF["reach120"]
    d+=min(abs(m["spread_med"]-REF["spread"])/REF["spread"],5.0)
    mr=m["med_reach"] if m["med_reach"] is not None else 300.0
    lat=min(abs(mr-REF["med_reach"])/REF["med_reach"],5.0)
    d+=lat*min(1.0,(m["r120"] or 0.0)/REF["reach120"])
    return round(d,3)

MIN_N=int(sys.argv[1]) if len(sys.argv)>1 else 40
rows=load(); sl=series(rows)
live=[s for s in sl if s[0].get("live")]
pre=[s for s in sl if not s[0].get("live")]
print("rows=%d  segments=%d  (live %d / pre %d)   MIN n120 for ranking = %d"
      %(len(rows),len(sl),len(live),len(pre),MIN_N))

HDR=("%-44s %4s %6s %6s %6s %6s %6s %6s %6s %6s %6s %6s %6s %6s"
     %("venue|sport|market_type","mkt","state","sprdM","<=1c%","mv/min",
       "r30%","r60%","r120%","medR","2sid%","rcy/h","n120","dist"))
def show(res,title,keyname):
    print(); print("="*len(HDR)); print(title); print("="*len(HDR))
    print(HDR); print("-"*len(HDR))
    ok=[m for m in res if (m["n120"] or 0)>=MIN_N]
    thin=[m for m in res if (m["n120"] or 0)<MIN_N]
    for m in ok:
        print("%-44s %4d %6d %6.4f %6s %6s %6s %6s %6s %6s %6s %6s %6d %6s"
          %(m[keyname][:44],m["markets"],m["states"],m["spread_med"],m["le1c"],
            m["move_min"],m["r30"],m["r60"],m["r120"],
            ("%.0f"%m["med_reach"]) if m["med_reach"] is not None else "-",
            m["two_sided"],m["recycle_hr"],m["n120"],
            m["dist"] if m["dist"] is not None else "-"))
    if thin:
        print("-- BELOW SAMPLE FLOOR (n120 < %d), shown not discarded --"%MIN_N)
        for m in sorted(thin,key=lambda x:-(x["n120"] or 0))[:14]:
            print("%-44s %4d %6d %6.4f %6s %6s %6s %6s %6s %6s %6s %6s %6d %6s"
              %(m[keyname][:44],m["markets"],m["states"],m["spread_med"],m["le1c"],
                m["move_min"],m["r30"],m["r60"],m["r120"],
                ("%.0f"%m["med_reach"]) if m["med_reach"] is not None else "-",
                m["two_sided"],m["recycle_hr"],m["n120"],
                m["dist"] if m["dist"] is not None else "-"))

def build(keyfn,keyname,pool):
    g=collections.defaultdict(list)
    for se in pool: g[keyfn(se[0])].append(se)
    out=[]
    for k,v in g.items():
        m=metrics(v)
        if not m: continue
        m[keyname]=k; m["dist"]=dist(m); out.append(m)
    out.sort(key=lambda m:(m["dist"] is None,m["dist"] if m["dist"] is not None else 9e9))
    return out

cat=build(lambda r:"%s|%s|%s"%("INTL" if r["venue"].endswith("INTERNATIONAL") else "PMUS",
                              r["league"],r["sports_type"]),"key",live)
show(cat,"RANKING 1 -- CATEGORY / MARKET FAMILY (LIVE only, no pooling)","key")
ind=build(lambda r:"%s|%s"%("INTL" if r["venue"].endswith("INTERNATIONAL") else "PMUS",
                            r["slug"]),"key",live)
show(ind[:30],"RANKING 2 -- INDIVIDUAL MARKET (LIVE only, top 30 by distance)","key")

print(); print("REFERENCE  Intl BTC15m CRYPTO  sprdM 0.010  <=1c 96.3%  r120 80.6%  medR 16s  mv 4.88")
print("CONTROL    Gemini BTC5m/15m     sprdM 0.280  <=1c  0.2%  r120 24.4%  medR 72s  mv 5.18")
json.dump({"category":cat,"individual":ind},open(SCR+"/rankings.json","w"),indent=1,default=str)
