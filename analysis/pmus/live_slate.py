"""LIVE SLATE habitat ranking. READ-ONLY analysis of already-collected rows.

Reach is the CANONICAL definition carried over verbatim from
pmus_habitat_analyze.py (which produced 08d9fc8b): from a state (b0,a0),
BUY-reach is the ask descending to <= b0 within the horizon, SELL-reach is
the bid rising to >= a0. States whose series ends before the horizon are
RIGHT-CENSORED and EXCLUDED -- never scored as misses.

Series are split whenever (a) the sampling gap exceeds 4x the cadence, which
makes the 05:04-10:23 collector outage break the series instead of bridging
it, or (b) the market's live flag flips, so a PRE segment and a LIVE segment
of the SAME market are never pooled. That split is what makes the
same-market PRE -> LIVE comparison possible.
"""
import sys, json, collections, statistics as st

SCR="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
SRC=SCR+"/nfl_sunday.jsonl"

# CANONICAL corrected reference figures (08d9fc8b). The pre-censoring-correction
# numbers 76.0% / 16.8% are SUPERSEDED and are not used here.
REF={"pm_intl":{"spread":0.010,"le1c":96.3,"reach120":80.6,"med_reach":16.0},
     "gemini" :{"spread":0.280,"le1c":0.2 ,"reach120":24.4,"med_reach":72.0}}

def load():
    rows=[]
    for l in open(SRC):
        try: d=json.loads(l)
        except Exception: continue
        if d.get("_marker"): continue
        if d.get("bid") is None or d.get("ask") is None: continue
        if d["ask"]<=d["bid"]: continue          # crossed/locked -> not a state
        rows.append(d)
    return rows

def series(rows):
    """slug -> contiguous, single-live-state segments.

    The split threshold is derived from the slug's OWN median sampling gap,
    not from a fixed constant: live markets are swept every sweep (~8s) and
    pregame every 4th (~32s), so one constant either shreds the pregame
    series or bridges real outages. 3x the market's own median gap breaks
    the 05:04-10:23 collector outage under both cadences.
    """
    by=collections.defaultdict(list)
    for r in rows: by[r["slug"]].append(r)
    out=[]
    for slug,rs in by.items():
        rs.sort(key=lambda r:r["t"])
        gaps=[b["t"]-a["t"] for a,b in zip(rs,rs[1:])]
        med=st.median(gaps) if gaps else 8.0
        thr=max(45.0,3.0*med)
        cur=[]
        for r in rs:
            brk = cur and ((r["t"]-cur[-1]["t"])>thr
                           or bool(r.get("live"))!=bool(cur[-1].get("live")))
            if brk:
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
        if horizon is not None and (tend-r["t"])<horizon: continue
        b0,a0=r["bid"],r["ask"]; hb=hs=None
        for j in range(i+1,len(se)):
            dt=se[j]["t"]-r["t"]
            if horizon is not None and dt>horizon: break
            if hb is None and se[j]["ask"]<=b0: hb=dt
            if hs is None and se[j]["bid"]>=a0: hs=dt
            if hb is not None and hs is not None: break
        outb.append(hb); outs.append(hs)
    return outb,outs

def pct(v):
    v=[x for x in v if x is not None or True]
    return None if not v else round(100.0*sum(1 for x in v if x is not None)/len(v),1)

def metrics(sl,label):
    sp=[];db=[];da=[];tmove=[];osc=[];obs=0.0;cads=[]
    R={30:[],60:[],120:[]};S={120:[]};waits=[]
    for se in sl:
        if len(se)<4: continue
        obs += se[-1]["t"]-se[0]["t"]; cads.append(cadence(se))
        sp += [r["ask"]-r["bid"] for r in se]
        db += [r["bid_qty"] for r in se if r.get("bid_qty")]
        da += [r["ask_qty"] for r in se if r.get("ask_qty")]
        mins=(se[-1]["t"]-se[0]["t"])/60.0
        if mins>0:
            ch=sum(1 for i in range(1,len(se))
                   if se[i]["bid"]!=se[i-1]["bid"] or se[i]["ask"]!=se[i-1]["ask"])
            tmove.append(ch/mins)
            mids=[(r["bid"]+r["ask"])/2 for r in se]
            osc.append(sum(abs(b-a) for a,b in zip(mids,mids[1:]))/mins)
        cad_s=cadence(se)
        for h in (30,60,120):
            if h<cad_s: continue      # horizon below the sampling floor: unmeasurable
            b,_=reach(se,h); R[h]+=b
        _,s2=reach(se,120); S[120]+=s2
        bb,_=reach(se,120); waits+=[x for x in bb if x is not None]
    if not sp: return None
    return {"label":label,"markets":len({id(x) for x in sl}),"segments":len(sl),
      "states":len(sp),"obs_min":round(obs/60.0,1),
      "spread_med":round(st.median(sp),4),
      "pc_le_1c":round(100.0*sum(1 for x in sp if x<=0.0101)/len(sp),1),
      "pc_le_2c":round(100.0*sum(1 for x in sp if x<=0.0201)/len(sp),1),
      "pc_le_3c":round(100.0*sum(1 for x in sp if x<=0.0301)/len(sp),1),
      "pc_le_5c":round(100.0*sum(1 for x in sp if x<=0.0501)/len(sp),1),
      "depth_bid_med":round(st.median(db),1) if db else None,
      "depth_ask_med":round(st.median(da),1) if da else None,
      "touch_chg_min":round(st.median(tmove),2) if tmove else None,
      "osc_min":round(st.median(osc),4) if osc else None,
      "cad_med":round(st.median(cads),1) if cads else None,"n30":len(R[30]),"n60":len(R[60]),"n120":len(R[120]),
      "reach30":pct(R[30]),"reach60":pct(R[60]),"reach120":pct(R[120]),
      "sell_reach120":pct(S[120]),
      "median_reach_s":round(st.median(waits),1) if waits else None}

def similarity(m):
    """Distance to the L5 International habitat on the discriminating axes.
    Reach is primary; spread alone is insufficient. Lower is closer."""
    r=REF["pm_intl"]
    if m["reach120"] is None: return None
    d_reach=abs(m["reach120"]-r["reach120"])/r["reach120"]
    d_sp=abs(m["spread_med"]-r["spread"])/max(r["spread"],1e-9)
    mr=m["median_reach_s"]
    d_mr=abs(mr-r["med_reach"])/r["med_reach"] if mr is not None else 2.0
    return round(2.0*d_reach + 1.0*min(d_sp,5.0) + 1.0*min(d_mr,5.0),3)

rows=load(); sl=series(rows)
live=[s for s in sl if s[0].get("live")]
pre =[s for s in sl if not s[0].get("live")]
print("rows=%d segments=%d (live %d / pre %d)"%(len(rows),len(sl),len(live),len(pre)))

# ---- per live market ----
grp=collections.defaultdict(list)
for se in live:
    r=se[0]
    grp[(r["league"],r["event"],r["sports_type"])].append(se)
res=[]
for k,v in grp.items():
    m=metrics(v,"|".join(k))
    if m: m["sim"]=similarity(m); m["key"]=k; res.append(m)
res.sort(key=lambda m:(m["sim"] is None, m["sim"] if m["sim"] is not None else 9e9))
print()
print("=== LIVE SLATE RANKED BY SIMILARITY TO L5 INTERNATIONAL HABITAT ===")
hdr=("%-46s %5s %6s %6s %6s %6s %6s %6s %6s %5s %7s %6s"
     %("league|event|market_type","st","sprdM","<=1c%","<=2c%","r30%","r60%","r120%","medR","n120","tchg/m","sim"))
print(hdr); print("-"*len(hdr))
for m in res:
    print("%-46s %5d %6.4f %6s %6s %6s %6s %6s %6s %5d %7s %6s"
      %(m["label"][:46],m["states"],m["spread_med"],m["pc_le_1c"],m["pc_le_2c"],
        m["reach30"],m["reach60"],m["reach120"],
        ("%.0f"%m["median_reach_s"]) if m["median_reach_s"] is not None else "-",
        m["n120"],m["touch_chg_min"],m["sim"] if m["sim"] is not None else "-"))
print()
for lab,sel in (("LIVE ALL",live),("PRE ALL",pre)):
    m=metrics(sel,lab)
    if m: print("%-9s states=%-6d sprdM=%.4f <=1c=%s%% <=2c=%s%% r120=%s%% medR=%s n120=%d tchg/m=%s depth b/a=%s/%s"
                %(lab,m["states"],m["spread_med"],m["pc_le_1c"],m["pc_le_2c"],
                  m["reach120"],m["median_reach_s"],m["n120"],m["touch_chg_min"],
                  m["depth_bid_med"],m["depth_ask_med"]))
print()
print("REFERENCE (canonical 08d9fc8b, right-censoring corrected)")
print("  Polymarket Intl BTC15m @8s : sprdM 0.010  <=1c 96.3%  r120 80.6%  medR 16s")
print("  Gemini BTC5m/15m        @8s : sprdM 0.280  <=1c  0.2%  r120 24.4%  medR 72s")
