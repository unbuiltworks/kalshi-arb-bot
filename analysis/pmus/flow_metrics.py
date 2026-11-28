"""FLOW metrics, joined to the habitat ranking. Reach metrics are UNCHANGED
and are NOT redefined or removed -- these are ADDITIONAL columns whose whole
purpose is to separate BOOK REACH habitat from EXECUTABLE RECYCLING habitat.

Definitions, stated rather than assumed:
  prints/min              any trade in the market, per observed minute.
  contracts/min           traded size per observed minute.
  compat/min              prints COMPATIBLE with a maker order resting at the
                          touch: a print at or below the best bid (someone
                          sold into where our bid would rest). This is the
                          flow that can actually fill passive inventory.
  two_sided/min           minutes-rate of prints occurring on BOTH sides
                          within the same 60s bucket.
  P(reach->trade|10/30/60) given a state whose ask descends to the bid (a
                          REACH), the probability a COMPATIBLE print follows
                          within 10/30/60s. This is the number that tells us
                          whether book reach is executable at all.
Right-censoring is handled the same way as in the reach metrics: a reach with
less than the horizon of remaining observation is EXCLUDED, not scored a miss.
"""
import sys, json, collections, statistics as st
import datetime as dt

SCR="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"

def ts(v):
    if isinstance(v,(int,float)): return float(v)
    try: return dt.datetime.fromisoformat(str(v)).timestamp()
    except Exception: return None

def books():
    out=collections.defaultdict(list)
    for venue,f in (("PMUS","nfl_sunday.jsonl"),("INTL","intl_live.jsonl")):
        try: fh=open(SCR+"/"+f)
        except OSError: continue
        for l in fh:
            try: d=json.loads(l)
            except Exception: continue
            if d.get("_marker") or not d.get("live"): continue
            if d.get("bid") is None or d.get("ask") is None: continue
            t=ts(d.get("t"))
            if t is None or d["ask"]<=d["bid"]: continue
            out[(venue,d["slug"])].append({"t":t,"bid":float(d["bid"]),
              "ask":float(d["ask"]),"league":d.get("league"),
              "sports_type":d.get("sports_type")})
    for k in out: out[k].sort(key=lambda r:r["t"])
    return out

def flow():
    pm=collections.defaultdict(list)
    try:
        rows=collections.defaultdict(list)
        for l in open(SCR+"/flow_pmus.jsonl"):
            try: d=json.loads(l)
            except Exception: continue
            t=ts(d.get("t"))
            if t is not None: rows[d["slug"]].append((t,d))
        for s,v in rows.items():
            v.sort(key=lambda r:r[0]); prev=None
            for t,d in v:
                if prev is not None:
                    try: dv=float(d["shares_traded_cum"])-float(prev["shares_traded_cum"])
                    except (TypeError,ValueError,KeyError): dv=0.0
                    if dv>0 and d.get("last_trade_px") is not None:
                        pm[("PMUS",s)].append({"t":t,"px":float(d["last_trade_px"]),
                          "size":dv,"side":None})
                prev=d
    except OSError: pass
    try:
        for l in open(SCR+"/flow_intl.jsonl"):
            try: d=json.loads(l)
            except Exception: continue
            t=ts(d.get("t")) or ts(d.get("observed_t"))
            try: px=float(d["price"]); sz=float(d["size"])
            except (TypeError,ValueError,KeyError): continue
            if t is None: continue
            pm[("INTL",d["slug"])].append({"t":t,"px":px,"size":sz,
              "side":d.get("side")})
    except OSError: pass
    for k in pm: pm[k].sort(key=lambda r:r["t"])
    return pm

def metrics(se,pr,cov0):
    """Windowed to the INTERSECTION of book coverage and FLOW coverage.

    The book collectors started well before the flow tap, so measuring prints
    per minute over the full book span divides real prints by minutes in which
    no flow was being recorded at all. That understates every flow rate and
    would have made the negative result look stronger than the data supports.
    cov0 is when flow collection began for this venue.
    """
    se=[r for r in se if r["t"]>=cov0]
    if len(se)<8: return None
    mins=(se[-1]["t"]-se[0]["t"])/60.0
    if mins<=0: return None
    t0,t1=se[0]["t"],se[-1]["t"]
    p=[x for x in pr if t0<=x["t"]<=t1]
    compat=0
    for x in p:
        b=[r for r in se if r["t"]<=x["t"]]
        if b and x["px"]<=b[-1]["bid"]+1e-9: compat+=1
    buckets=collections.defaultdict(set)
    for x in p:
        b=[r for r in se if r["t"]<=x["t"]]
        if not b: continue
        buckets[int(x["t"]//60)].add("S" if x["px"]<=b[-1]["bid"]+1e-9 else "B")
    two=sum(1 for v in buckets.values() if len(v)>1)
    # P(reach -> compatible print within H), right-censored excluded
    res={}
    for H in (10.0,30.0,60.0):
        hit=0; n=0
        for i,r in enumerate(se):
            if (t1-r["t"])<H: continue
            reach=any(se[j]["ask"]<=r["bid"] for j in range(i+1,len(se))
                      if se[j]["t"]-r["t"]<=H)
            if not reach: continue
            n+=1
            if any(r["t"]<x["t"]<=r["t"]+H and x["px"]<=r["bid"]+1e-9 for x in p):
                hit+=1
        res["P_reach_to_trade_%ds"%int(H)]=(round(100.0*hit/n,1) if n else None)
        res["n_reach_%ds"%int(H)]=n
    return {"obs_min":round(mins,1),"prints":len(p),
      "prints_min":round(len(p)/mins,2),
      "contracts_min":round(sum(x["size"] for x in p)/mins,1),
      "compat_min":round(compat/mins,2),"compat_prints":compat,
      "two_sided_min":round(two/mins,2),**res}

bk=books(); fl=flow()
# when did flow collection actually begin, per venue?
cov={}
for (venue,_),pr in fl.items():
    if not pr: continue
    cov[venue]=min(cov.get(venue,9e18),pr[0]["t"])
for venue,f in (("PMUS","flow_pmus.jsonl"),("INTL","flow_intl.jsonl")):
    try:
        first=None
        for l in open(SCR+"/"+f):
            try: d=json.loads(l)
            except Exception: continue
            t=ts(d.get("t")) or ts(d.get("observed_t"))
            if t: first=t; break
        if first: cov[venue]=min(cov.get(venue,9e18),first)
    except OSError: pass
print("flow coverage starts:",{k:dt.datetime.utcfromtimestamp(v).strftime("%H:%M:%SZ")
      for k,v in cov.items()})
g=collections.defaultdict(lambda:{"se":[],"pr":[]})
for (venue,slug),se in bk.items():
    if len(se)<8: continue
    k="%s|%s|%s"%(venue,se[0]["league"],se[0]["sports_type"])
    g[k]["se"].append(se); g[k]["pr"].append(fl.get((venue,slug),[]))
    g[k].setdefault("venue",venue)
rows=[]
for k,v in g.items():
    agg={"prints":0,"contracts":0.0,"compat":0,"two":0,"mins":0.0,
         "n":{10:0,30:0,60:0},"h":{10:0,30:0,60:0},"mkts":len(v["se"])}
    for se,pr in zip(v["se"],v["pr"]):
        m=metrics(se,pr,cov.get(v.get("venue"),0.0))
        if not m: continue
        agg["mins"]+=m["obs_min"]; agg["prints"]+=m["prints"]
        agg["contracts"]+=m["contracts_min"]*m["obs_min"]
        agg["compat"]+=m["compat_prints"]; agg["two"]+=m["two_sided_min"]*m["obs_min"]
        for H in (10,30,60):
            n=m["n_reach_%ds"%H] or 0; pct=m["P_reach_to_trade_%ds"%H]
            agg["n"][H]+=n
            if pct is not None: agg["h"][H]+=round(pct*n/100.0)
    if agg["mins"]<=0: continue
    rows.append({"key":k,"mkts":agg["mkts"],"obs_min":round(agg["mins"],0),
      "prints_min":round(agg["prints"]/agg["mins"],2),
      "contracts_min":round(agg["contracts"]/agg["mins"],1),
      "compat_min":round(agg["compat"]/agg["mins"],2),
      "two_sided_min":round(agg["two"]/agg["mins"],2),
      **{"P_r2t_%ds"%H:(round(100.0*agg["h"][H]/agg["n"][H],1) if agg["n"][H] else None)
         for H in (10,30,60)},
      **{"n_reach_%ds"%H:agg["n"][H] for H in (10,30,60)}})
rows.sort(key=lambda r:-(r["compat_min"] or 0))
H=("%-42s %4s %7s %9s %9s %9s %9s %8s %8s %8s %7s"
   %("venue|sport|market_type","mkt","obs_min","prints/m","contr/m","compat/m",
     "2sided/m","P_r2t10","P_r2t30","P_r2t60","n_r60"))
print("FLOW METRICS -- ADDED TO, NOT REPLACING, THE REACH METRICS")
print(H); print("-"*len(H))
for r in rows:
    print("%-42s %4d %7.0f %9.2f %9.1f %9.2f %9.2f %8s %8s %8s %7d"
      %(r["key"][:42],r["mkts"],r["obs_min"],r["prints_min"],r["contracts_min"],
        r["compat_min"],r["two_sided_min"],r["P_r2t_10s"],r["P_r2t_30s"],
        r["P_r2t_60s"],r["n_reach_60s"]))
json.dump(rows,open(SCR+"/flow_metrics.json","w"),indent=1,default=str)
