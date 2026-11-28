"""POST-FIRST-FILL CHAIN. SHADOW/HYPOTHETICAL -- nothing is submitted or real.

Follows EVERY hypothetical FIRST FILL to match, market end, or censoring, and
classifies exactly where the chain breaks. Conversion is reported PER 100
FIRST FILLS, not per market-state observation.

FROZEN POLICY, UNMODIFIED. Inventory, plan_action, complement_target and
pv2.proposal are imported from src.pmus_sports_l5_decision / the frozen
persistence-v2 model and used as-is. The ONLY thing not carried over is
MAX_ACTIONS, which that module documents as "bounded status emission" -- a
display cap on its status payload, not a policy parameter. Following a chain
to completion requires lifting it. DECISION_MIN_GAP_S (30s) is a real cadence
parameter and IS kept. No model, threshold or parameter is tuned.

TWO FILL MODELS, BOTH REPORTED, BECAUSE QUEUE POSITION IS UNOBSERVABLE:
  OPTIMISTIC   any compatible print at or through the resting price after the
               quote counts as a fill. This is the frozen module's `possible`
               class and is an UPPER BOUND.
  QUEUE_AWARE  cumulative compatible volume after the quote must EXCEED the
               depth resting at our price level when we joined. Everyone
               already at our price is ahead of us. This is a LOWER BOUND.
The truth lies between them and we never collapse it to one number.

A fill is NEVER inferred merely because ask <= resting bid. Book reach is
recorded as its own chain step, and a compatible PRINT is required beyond it.

FAILURE MODES, mutually exclusive, assigned at the FIRST point the chain
breaks. The owner's mode 1 is split because the data forces the distinction:
  1a POLICY_NEVER_RECOMMENDED_COMPLETE  the v2 model kept returning open/
     extend and the residual cap blocked further action. POLICY-side.
  1b COMPLEMENT_NO_LEGAL_PRICE          complete WAS recommended but no
     fee-safe maker price exists. ECONOMICS-side.
  2  COMPLEMENT_QUOTED_NEVER_REACHED
  3  REACHED_NO_COMPATIBLE_TRADE
  4  TRADE_BUT_INSUFFICIENT_QUEUE_EVIDENCE
  5  OPPOSITE_FILL_QTY_MISMATCH
  6  MATCHED
  7  CENSORED  observation ended first; NOT counted as a failure.
"""
import sys, os, json, collections, time, statistics as stt
import datetime as dt
from decimal import Decimal as D
sys.path.insert(0,"/home/user/kalshi-arb-bot")
from src.pmus_sports_l5_decision import (Inventory, plan_action,
    complement_target, DECISION_MIN_GAP_S, MAX_RESIDUAL_UNITS,
    _proposal, _touch_for)
from src import pmus_l5_persistence_v2 as pv2

RESOLUTION_HORIZON_S=300.0
SCR="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
MODEL=pv2.load_v2()

def ts(v):
    if isinstance(v,(int,float)): return float(v)
    try: return dt.datetime.fromisoformat(str(v)).timestamp()
    except Exception: return None

def load_books():
    """slug -> time-ordered book snapshots, per venue."""
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
            if t is None: continue
            out[(venue,d["slug"])].append({"t":t,"bid":float(d["bid"]),
                "ask":float(d["ask"]),
                "bid_qty":d.get("bid_qty"),"ask_qty":d.get("ask_qty"),
                "league":d.get("league"),"event":d.get("event"),
                "sports_type":d.get("sports_type")})
    for k in out: out[k].sort(key=lambda r:r["t"])
    return out

def load_flow():
    """slug -> prints. PMUS from causal volume deltas; INTL from real prints."""
    pm=collections.defaultdict(list)
    try:
        rows=collections.defaultdict(list)
        for l in open(SCR+"/flow_pmus.jsonl"):
            try: d=json.loads(l)
            except Exception: continue
            t=ts(d.get("t"))
            if t is None: continue
            rows[d["slug"]].append((t,d))
        for s,v in rows.items():
            v.sort(key=lambda r:r[0]); prev=None
            for t,d in v:
                if prev is not None:
                    try: dv=float(d["shares_traded_cum"])-float(prev["shares_traded_cum"])
                    except (TypeError,ValueError,KeyError): dv=0.0
                    if dv>0 and d.get("last_trade_px") is not None:
                        pm[("PMUS",s)].append({"t":t,"px":float(d["last_trade_px"]),
                          "size":dv,"dir":"unknown","prov":"VOLUME_DELTA"})
                prev=d
    except OSError: pass
    try:
        for l in open(SCR+"/flow_intl.jsonl"):
            try: d=json.loads(l)
            except Exception: continue
            t=ts(d.get("t")) or ts(d.get("observed_t"))
            if t is None: continue
            try: px=float(d["price"]); sz=float(d["size"])
            except (TypeError,ValueError,KeyError): continue
            pm[("INTL",d["slug"])].append({"t":t,"px":px,"size":sz,
               "dir":(d.get("side") or "unknown"),"prov":"REAL_PRINT"})
    except OSError: pass
    for k in pm: pm[k].sort(key=lambda r:r["t"])
    return pm

def book_at(se,t):
    prior=[r for r in se if r["t"]<=t]
    return prior[-1] if prior else None

def compatible(prints,t0,t1,px,side_yes):
    """Prints that could have traded against a resting BUY at px.

    Buying YES at px rests on the YES bid ladder; a SELLER hitting at or below
    px is compatible. Buying NO at px is a YES ask at (1-px); a BUYER lifting
    at or above (1-px) is compatible. Direction, where the venue states it,
    is used; where it does not, price alone decides and that is labelled.
    """
    lim=px if side_yes else (1.0-px)
    out=[]
    for p in prints:
        if p["t"]<=t0 or (t1 is not None and p["t"]>t1): continue
        if side_yes and p["px"]<=lim+1e-9: out.append(p)
        elif (not side_yes) and p["px"]>=lim-1e-9: out.append(p)
    return out

def replay(venue,slug,se,prints):
    """Sequential frozen-policy replay. Returns the chain record per FIRST FILL."""
    inv=Inventory(); last_rel=""; t_prev=0.0
    chains=[]; cur=None; extends=0
    saw_complete_rec=False; no_legal=False
    for r in se:
        t=r["t"]
        if t-t_prev<DECISION_MIN_GAP_S: continue
        t_prev=t
        bk={"bid":D(str(r["bid"])),"ask":D(str(r["ask"])),"t":t}
        prop=_proposal(MODEL,float(r["bid"]),inv,last_rel)
        if prop.get("off_distribution"): continue
        rel=prop["recommended_relation"]
        if rel=="complete": saw_complete_rec=True
        plan=plan_action(rel,inv,bk)
        if plan["decision"]!="quote":
            if plan.get("reason")=="no_legal_maker_complement": no_legal=True
            continue
        px=float(plan["_px"]); side_yes=plan["_side_yes"]; rl=plan["relation"]
        depth_ahead=(r["bid_qty"] if side_yes else r["ask_qty"]) or 0.0
        comp=compatible(prints,t,t+300.0,px,side_yes)
        opt_fill=len(comp)>0
        cum=0.0; qa_fill=False; qa_t=None
        for p in comp:
            cum+=p["size"]
            if cum>float(depth_ahead): qa_fill=True; qa_t=p["t"]; break
        reached=any((b["ask"]<=px if side_yes else b["bid"]>=(1.0-px))
                    for b in se if t<b["t"]<=t+300.0)
        if not opt_fill:
            last_rel=rl; continue
        inv.record(side_yes,D(str(px)))
        last_rel=rl
        if cur is not None and inv.residual==0 and cur.get("t_first") is not None:
            cur["policy_recommended_complete"]=saw_complete_rec
            cur["no_legal_complement"]=no_legal
            cur["final_residual"]=abs(inv.residual)
            cur["final_matched"]=inv.matched-cur.get("_matched_at_start",0)
            cur["series_end_t"]=se[-1]["t"]
            chains.append(cur); cur=None
        if rl in ("open",) and cur is None:
            cur={"venue":venue,"slug":slug,"league":r["league"],
                 "sports_type":r["sports_type"],"t_first":t,
                 "first_side":"YES" if side_yes else "NO","first_px":px,
                 "extends":0,"complement_quoted":False,"complement_px":None,
                 "complement_reached":False,"compatible_prints":0,
                 "opt_opposite_fill":False,"qa_opposite_fill":False,
                 "qa_first_fill":qa_fill,"depth_ahead_first":depth_ahead,
                 "t_opposite":None,"matched":0,
                 "_matched_at_start":inv.matched}
        elif cur is not None and rl=="extend":
            cur["extends"]+=1
        elif cur is not None and rl=="complete":
            cur["complement_quoted"]=True; cur["complement_px"]=px
            cur["complement_reached"]=reached
            cur["compatible_prints"]=len(comp)
            cur["opt_opposite_fill"]=opt_fill
            cur["qa_opposite_fill"]=qa_fill
            cur["t_opposite"]=qa_t or (comp[0]["t"] if comp else None)
            cur["matched"]=inv.matched
    if cur is not None:
        cur["policy_recommended_complete"]=saw_complete_rec
        cur["no_legal_complement"]=no_legal
        cur["final_residual"]=abs(inv.residual)
        cur["final_matched"]=inv.matched
        cur["series_end_t"]=se[-1]["t"]
        chains.append(cur)
    return chains

def classify(c,now):
    """MATCHED first, then CENSORING, then the failure modes.

    A chain is CENSORED until it has been observed for RESOLUTION_HORIZON_S
    after its first fill -- 300s, the longest opposite-flow horizon in the
    mandate. Before that the complement may still arrive, so assigning a
    terminal failure mode would understate conversion. After it, the chain is
    classified at the point it broke even though the market continues. Only
    MATCHED is safe to assign early, because matching is irreversible.
    """
    if c["final_matched"]>0 and c["final_residual"]==0: return "6_MATCHED"
    if (c["series_end_t"]-c["t_first"])<RESOLUTION_HORIZON_S:
        return "7_CENSORED_TOO_LITTLE_POST_FILL_OBSERVATION"
    if c["final_matched"]>0: return "5_OPPOSITE_FILL_QTY_MISMATCH"
    if c["qa_opposite_fill"] or c["opt_opposite_fill"]:
        return "5_OPPOSITE_FILL_QTY_MISMATCH"
    if not c["complement_quoted"]:
        if c["no_legal_complement"]: return "1b_COMPLEMENT_NO_LEGAL_PRICE"
        return "1a_POLICY_NEVER_RECOMMENDED_COMPLETE"
    if not c["complement_reached"]: return "2_COMPLEMENT_QUOTED_NEVER_REACHED"
    if c["compatible_prints"]==0: return "3_REACHED_NO_COMPATIBLE_TRADE"
    return "4_TRADE_BUT_INSUFFICIENT_QUEUE_EVIDENCE"

books=load_books(); flow=load_flow(); now=time.time()
allc=collections.defaultdict(list)
for (venue,slug),se in books.items():
    if len(se)<8: continue
    for c in replay(venue,slug,se,flow.get((venue,slug),[])):
        allc[venue].append(c)

out={"labels":"SHADOW/HYPOTHETICAL. No order exists, nothing is submitted, "
       "filled, settled or realized. Fills are hypothetical and bracketed by "
       "an OPTIMISTIC upper bound and a QUEUE_AWARE lower bound.",
     "model_sha16":MODEL["_sha256_16"],
     "as_of_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
     "frozen_policy_note":"Inventory, plan_action, complement_target and "
       "pv2.proposal used unmodified. Only MAX_ACTIONS -- documented in the "
       "module as bounded STATUS EMISSION -- is not applied, because following "
       "a chain to completion requires it. DECISION_MIN_GAP_S is kept."}
for venue,cs in allc.items():
    n=len(cs)
    modes=collections.Counter(classify(c,now) for c in cs)
    ext=[c["extends"] for c in cs]
    lat=[c["t_opposite"]-c["t_first"] for c in cs
         if c.get("t_opposite") and c.get("t_first")]
    matched=sum(1 for c in cs if classify(c,now)=="6_MATCHED")
    resolved=[c for c in cs
              if classify(c,now)!="7_CENSORED_TOO_LITTLE_POST_FILL_OBSERVATION"]
    out[venue]={"FIRST_FILLS":n,
      "matched_conversion_per_100_first_fills":round(100.0*matched/n,1) if n else None,
      "RESOLVED_first_fills":len(resolved),
      "matched_conversion_per_100_RESOLVED":
        round(100.0*matched/len(resolved),1) if resolved else None,
      "failure_modes":dict(sorted(modes.items())),
      "failure_modes_per_100_first_fills":
        {k:round(100.0*v/n,1) for k,v in sorted(modes.items())} if n else {},
      "extends_median":round(stt.median(ext),1) if ext else None,
      "extends_max":max(ext) if ext else None,
      "completion_latency_s_median":round(stt.median(lat),1) if lat else None,
      "queue_aware_first_fills":sum(1 for c in cs if c.get("qa_first_fill")),
      "optimistic_first_fills":n,
      "markets":len({c["slug"] for c in cs}),
      "by_league":dict(collections.Counter(c["league"] for c in cs).most_common())}
json.dump({**out,"chains":{v:allc[v] for v in allc}},
          open(SCR+"/chain_report.json","w"),indent=1,default=str)
for v in ("PMUS","INTL"):
    if v not in out: continue
    c=out[v]
    print("="*70); print("%s  FIRST FILLS = %d   (markets %d)"%(v,c["FIRST_FILLS"],c["markets"]))
    print("="*70)
    print("  matched conversion per 100 first fills : %s"%c["matched_conversion_per_100_first_fills"])
    print("  RESOLVED first fills (censored removed) : %d"%c["RESOLVED_first_fills"])
    print("  matched conversion per 100 RESOLVED    : %s"%c["matched_conversion_per_100_RESOLVED"])
    print("  first fills, OPTIMISTIC / QUEUE-AWARE  : %d / %d"
          %(c["optimistic_first_fills"],c["queue_aware_first_fills"]))
    print("  EXTENDs median / max                   : %s / %s"%(c["extends_median"],c["extends_max"]))
    print("  completion latency median              : %s"%c["completion_latency_s_median"])
    print("  failure modes (count | per 100 first fills):")
    for k,n2 in c["failure_modes"].items():
        print("     %-42s %4d | %5.1f"%(k,n2,c["failure_modes_per_100_first_fills"][k]))
    print("  by league:",c["by_league"])
    print()
