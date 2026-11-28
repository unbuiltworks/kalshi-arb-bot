"""Read-only replay of the RN1 quoting rule on a two-venue quote recording.
MODELLED on quotes, not fills. A resting bid 1 tick behind the touch is counted
filled only when the market trades THROUGH it (best bid falls below our price, or
the ask reaches it) - i.e. every modelled fill is an adverse one. No queue model."""
import json, sys, collections, statistics as st
TICK=0.01; CAP=0.97; QTY=2
def replay(rows, late_t=None, guard=True, depth=True, band=(0.25,0.75)):
    pos={"Y":0,"N":0}; cost={"Y":0.0,"N":0.0}
    rest={"Y":None,"N":None}        # resting px
    pairs=[]; fills=[]; lone_since=None; lone_durs=[]
    blocked=collections.Counter(); n=0
    for r in rows:
        if None in (r.get("ub"),r.get("ua"),r.get("ib"),r.get("ia")): continue
        n+=1; ub,ua=r["ub"],r["ua"]; imid=(r["ib"]+r["ia"])/2
        # 1. fills of what was resting before this snapshot
        for s in ("Y","N"):
            p=rest[s]
            if p is None: continue
            touch = ub if s=="Y" else 1-ua          # this side's best bid
            opp   = ua if s=="Y" else 1-ub          # this side's ask
            if touch < p-1e-9 or opp <= p+1e-9:
                pos[s]+=QTY; cost[s]+=p*QTY; fills.append((r["t"],s,p)); rest[s]=None
        if pos["Y"] and pos["N"]:
            m=min(pos["Y"],pos["N"]); pc=cost["Y"]/pos["Y"]+cost["N"]/pos["N"]
            pairs.append(pc)
            for s in ("Y","N"):
                cost[s]-=cost[s]/pos[s]*m; pos[s]-=m
            if lone_since: lone_durs.append(r["t"]-lone_since); lone_since=None
        heavy = "Y" if pos["Y"]>pos["N"] else "N" if pos["N"]>pos["Y"] else None
        if heavy and not lone_since: lone_since=r["t"]
        late = late_t is not None and r["t"]>=late_t
        # 2. new targets
        for s in ("Y","N"):
            touch = ub if s=="Y" else 1-ua
            px=round(touch-TICK,2)
            if heavy and s!=heavy:                   # completion: never guarded
                avg=cost[heavy]/pos[heavy]
                px=round(min(px, CAP-avg),2); rest[s]=px if px>0 else None; continue
            if heavy and s==heavy: rest[s]=None; continue   # no extension in this replay
            why=None
            if late: why="late"
            elif not (band[0]<=ub<=band[1]): why="band"
            elif depth and (r["ubq"] if s=="Y" else r["uaq"])<10: why="depth"
            elif guard and px > (imid if s=="Y" else 1-imid)-TICK+1e-9: why="guard"
            if why: blocked[(s,why)]+=1; rest[s]=None
            # the controller keeps a resting bid that is within one tick of
            # its target (REPRICE_TICKS 1) instead of chasing every move
            elif rest[s] is not None and abs(rest[s]-px)<=TICK+1e-9 and \
                    not (guard and rest[s] > (imid if s=="Y" else 1-imid)-TICK+1e-9):
                pass
            else: rest[s]=px
        # pair cap on the two openings
        if rest["Y"] and rest["N"] and not heavy and rest["Y"]+rest["N"]>CAP+1e-9:
            rest["N"]=None; blocked[("N","paircap")]+=1
    last=[r for r in rows if r.get("ub") and r.get("ua")][-1]
    mid=(last["ub"]+last["ua"])/2
    resid=0.0; rq=0
    for s in ("Y","N"):
        if pos[s]:
            rq+=pos[s]; resid+= pos[s]*(mid if s=="Y" else 1-mid) - cost[s]
    return {"snapshots":n,"fills":len(fills),"pairs":len(pairs)*QTY,
            "avg_pair_cost":round(st.mean(pairs),4) if pairs else None,
            "pair_pnl":round(sum((1-p)*QTY for p in pairs),2),
            "residual_contracts":rq,"residual_mark_pnl":round(resid,2),
            "median_lone_s":round(st.median(lone_durs)) if lone_durs else None,
            "max_lone_s":round(max(lone_durs)) if lone_durs else None,
            "blocked":{("%s:%s"%k):v for k,v in blocked.most_common(8)},
            "pair_costs":[round(p,2) for p in pairs]}
if __name__=="__main__":
    path=sys.argv[1]; late=float(sys.argv[2]) if len(sys.argv)>2 and sys.argv[2]!="-" else None
    by=collections.defaultdict(list)
    for l in open(path):
        r=json.loads(l); by[r["slug"]].append(r)
    for slug,rows in by.items():
        v=[r for r in rows if r.get("ub") and r.get("ua")]
        print(slug, "minutes %.0f"%((rows[-1]["t"]-rows[0]["t"])/60), "px %.2f->%.2f range %.2f-%.2f"%(v[0]["ub"],v[-1]["ub"],min(r["ub"] for r in v),max(r["ua"] for r in v)))
        for name,kw in (("with guard+depth",{}),("no guard",{"guard":False}),("no depth gate",{"depth":False})):
            print("   %-18s"%name, json.dumps(replay(rows,late_t=late,**kw)))
