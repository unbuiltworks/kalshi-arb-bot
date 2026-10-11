"""Frozen L5 continuous-inventory SHADOW, both venues. SHADOW/HYPOTHETICAL.

Runs src.pmus_sports_l5_decision (the FROZEN sequential L5 decision layer, on
the frozen persistence-v2 model) unmodified over the live shadow feed. Nothing
here is submitted, filled, settled or realized. Every "fill" is the module's
own `possible` class: an UPPER BOUND confirmed only by a later trade at or
through the resting price, with QUEUE POSITION UNPROVEN.

INTL cohort is REFERENCE / FIDELITY research on L5's own venue, which we do
not and will not execute on. PMUS cohort is TRANSFER / EXECUTABILITY research.
The two are reported separately and never pooled.
"""
import sys, json, collections, time, statistics as st
from decimal import Decimal as D
sys.path.insert(0,"/home/user/kalshi-arb-bot")
from src import pmus_sports_l5_decision as dec
from src import pmus_l5_persistence_v2 as pv2
from src import pmus_status_report as srep

SCR="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
COHORTS={"PMUS":SCR+"/shadow_pmus.jsonl","INTL":SCR+"/shadow_intl.jsonl"}
model=pv2.load_v2()

def cohort(name,path):
    try: rows=[json.loads(l) for l in open(path) if l.strip()]
    except OSError: return None
    by=collections.defaultdict(list)
    for r in rows: by[r["slug"]].append(r)
    now=time.time()
    firsts=0; extends=[]; opp=0; matched=0; seq=[]
    pair=[]; resid=[]; rdur=[]; fills_tot=0; mkts=0; lat=[]; caps=0
    per=[]
    for slug,obs in by.items():
        obs.sort(key=lambda o:srep._ts(o) or 0)
        if len(obs)<8: continue
        mkts+=1
        d=dec.decide_market(slug,obs,now,model,window_s=7200.0)
        acts=d.get("actions_replayed") or []
        qs=[a for a in acts if a.get("decision")=="quote"]
        fl=[a for a in qs if a.get("fill_class")=="possible"]
        fills_tot+=len(fl)
        caps+=sum(1 for a in acts
                  if str(a.get("reason","")).startswith("residual_cap"))
        rel=[a.get("relation") for a in fl]
        if fl:
            firsts+=1
            ext=sum(1 for r in rel[1:] if r=="extend")
            extends.append(ext)
            o=sum(1 for r in rel if r=="complete")
            opp+=o
            lat+=[a.get("fill_latency_s") for a in fl
                  if a.get("fill_latency_s") is not None]
            seq.append("%s|%s"%(slug[:34],"->".join(r[:3].upper() for r in rel)))
        inv=d.get("inventory_final") or {}
        if inv.get("matched"): matched+=inv["matched"]
        if inv.get("residual") is not None: resid.append(abs(inv["residual"]))
        for pc in (d.get("pair_costs_theoretical") or []):
            try: pair.append(float(pc))
            except (TypeError,ValueError): pass
        per.append({"slug":slug,"obs":len(obs),"quotes":len(qs),
                    "possible_fills":len(fl),"relations":rel,
                    "inventory_final":inv,
                    "executable_now":d.get("executable_now"),
                    "stale_reason":d.get("stale_reason"),
                    "next":(d.get("next_action") or {}).get("reason")
                            or (d.get("next_action") or {}).get("relation")})
    return {"cohort":name,"markets_with_enough_obs":mkts,
      "markets_with_a_first_fill":firsts,"possible_fills_total":fills_tot,
      "opposite_COMPLETE_fills":opp,"matched_units":matched,
      "matched_conversion_per_100_first_fills":
        round(100.0*matched/firsts,1) if firsts else None,
      "extends_before_match_median":
        round(st.median(extends),1) if extends else None,
      "extends_before_match_max":max(extends) if extends else None,
      "residual_cap_blocks":caps,
      "completion_latency_s_median":round(st.median(lat),1) if lat else None,
      "pair_basis_n":len(pair),
      "pair_basis_median":round(st.median(pair),4) if pair else None,
      "pct_pair_basis_below_1":
        round(100.0*sum(1 for x in pair if x<1.0)/len(pair),1) if pair else None,
      "residual_units_median":round(st.median(resid),1) if resid else None,
      "residual_units_max":max(resid) if resid else None,
      "sequences":seq[:12],"per_market":per}

out={"labels":"SHADOW/HYPOTHETICAL. No order exists. Every fill is the frozen "
       "module's `possible` class -- an UPPER BOUND, queue position UNPROVEN. "
       "Nothing is submitted, filled, settled or realized.",
     "model_sha16":model["_sha256_16"],"model_path":model["_path"],
     "as_of_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
     "fee_treatment":"PMUS maker fee is ZERO. The maker REBATE is credited by "
       "the venue but is NEVER counted here and never merged into trading P&L. "
       "No rebate or reward is assumed on either venue.",
     "cohort_roles":{"INTL":"REFERENCE/FIDELITY on L5's own venue -- not "
        "executable for us, never a trading target",
        "PMUS":"TRANSFER/EXECUTABILITY"}}
for n,p in COHORTS.items():
    c=cohort(n,p)
    if c: out[n]=c
json.dump(out,open(SCR+"/shadow_report.json","w"),indent=1,default=str)
for n in ("PMUS","INTL"):
    c=out.get(n)
    if not c: continue
    print("="*74); print("%s COHORT -- %s"%(n,out["cohort_roles"][n])); print("="*74)
    for k in ("markets_with_enough_obs","markets_with_a_first_fill",
              "possible_fills_total","opposite_COMPLETE_fills","matched_units",
              "matched_conversion_per_100_first_fills",
              "extends_before_match_median","extends_before_match_max",
              "residual_cap_blocks","completion_latency_s_median",
              "pair_basis_n","pair_basis_median","pct_pair_basis_below_1",
              "residual_units_median","residual_units_max"):
        print("  %-42s %s"%(k,c[k]))
    print("  sequences (OPE=open, EXT=extend, COM=complete):")
    for s in c["sequences"]: print("     ",s)
    print()
