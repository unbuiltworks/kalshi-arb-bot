#!/usr/bin/env python3
"""Closed-loop shadow replay on RECORDED ladder data.
Inventory-blind baseline vs inventory-aware, identical market data.
Every fill is MODELLED under the conservative queue rule; nothing executed."""
import json, sys, os
from decimal import Decimal as D
sys.path.insert(0,".")
os.environ.setdefault("L5_TS_MARKET","*"); os.environ.setdefault("L5_TS_EXPERIMENT_ID","x")
from src import l5_two_sided_probe as TS

TICK=D("0.01"); DEPTH=1; CLIP=D("1")
T=json.load(open("/tmp/claude-0/l5/win_tape.json"))
S=sys.argv[1] if len(sys.argv)>1 else "cpc-btc-updown-15m-2026-10-16-2030z"
lad=sorted(T["ladder"][S],key=lambda r:r["t"]); trd=sorted(T["trades"][S],key=lambda r:(r.get("t") or 0))
def dep(s,side,px):
    for p,q in (s.get(side) or []):
        if D(str(p))==px: return D(str(q))
    return None
def execd(t0,t1,pred):
    tot=D(0)
    for r in trd:
        t=r.get("t") or 0
        if t<t0 or t>t1: continue
        try: p=D(str(r["px"])); q=D(str(r["qty"]))
        except Exception: continue
        if pred(p): tot+=q
    return tot
def first_through(t0, px, above):
    for r in trd:
        t=r.get("t") or 0
        if t<t0: continue
        p=D(str(r["px"]))
        if (p>px) if above else (p<px): return t
    return None

def replay(aware: bool):
    s0=lad[0]; b0=D(str(s0["bid"])); a0=D(str(s0["ask"])); t0=s0["t"]; tN=lad[-1]["t"]
    up_px=b0-DEPTH*TICK; dn_px=(D(1)-a0)-DEPTH*TICK
    ev=[]; up_q=D(0); dn_q=D(0); up_c=D(0); dn_c=D(0)
    # conservative queue rule on each leg, with sequencing
    qa_u=dep(s0,"bids",up_px); qa_d=dep(s0,"asks",D(1)-dn_px)
    ex_u=execd(t0,tN, lambda p: p<=up_px); ex_d=execd(t0,tN, lambda p: p>=D(1)-dn_px)
    t_u=first_through(t0, up_px, above=False); t_d=first_through(t0, D(1)-dn_px, above=True)
    fills=[]
    if qa_u is not None and ex_u>qa_u and t_u: fills.append((t_u,"UP",up_px))
    if qa_d is not None and ex_d>qa_d and t_d: fills.append((t_d,"DOWN",dn_px))
    fills.sort()
    for (t,side,px) in fills:
        if side=="UP": up_q+=CLIP; up_c+=CLIP*px
        else: dn_q+=CLIP; dn_c+=CLIP*px
        ev.append("t+%.1fs MODELLED FILL %s @ %s"%(t-t0,side,px))
        # the decision the engine makes NEXT, with inventory now non-zero
        r=TS.inventory_aware_quote(
            up_qty=up_q, dn_qty=dn_q,
            up_cost_px=(up_c/up_q if up_q else None),
            dn_cost_px=(dn_c/dn_q if dn_q else None),
            blind_up_px=up_px, blind_dn_px=dn_px, tick=TICK)
        if aware and r.get("deficient"):
            ev.append("   aware: deficient %s, held %s, may pay up to %s vs blind %s -> %s"%(
                r["deficient"], r.get("held_cost"), r.get("max_completing_px"),
                r.get("blind_px"), r["action"]))
        elif not aware and r.get("deficient"):
            ev.append("   blind: (no inventory input) keeps quoting %s / %s"%(up_px,dn_px))
    return dict(up_q=up_q,dn_q=dn_q,up_c=up_c,dn_c=dn_c,ev=ev,
                up_px=up_px,dn_px=dn_px)

def econ(r, up_wins):
    M=min(r["up_q"],r["dn_q"])
    cu=(r["up_c"]/r["up_q"]) if r["up_q"] else D(0)
    cd=(r["dn_c"]/r["dn_q"]) if r["dn_q"] else D(0)
    matched=M*(D(1)-(cu+cd)) if M>0 else D(0)
    ru=r["up_q"]-M; rd=r["dn_q"]-M
    resid=ru*((D(1) if up_wins else D(0))-cu) + rd*((D(0) if up_wins else D(1))-cd)
    return matched,resid,matched+resid

print("window %s | ladder %d | trades %d"%(S,len(lad),len(trd)))
for aware in (False,True):
    r=replay(aware)
    print("\n=== %s ==="%("INVENTORY-AWARE" if aware else "INVENTORY-BLIND baseline"))
    print("   quoted UP %s / DOWN %s"%(r["up_px"],r["dn_px"]))
    for e in r["ev"]: print("   "+e)
    for uw in (True,False):
        m,res,tot=econ(r,uw)
        print("   settle UP=%-5s matched %+0.4f residual %+0.4f TOTAL %+0.4f"%(uw,m,res,tot))
