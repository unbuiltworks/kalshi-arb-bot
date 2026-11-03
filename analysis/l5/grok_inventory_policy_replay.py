"""Grok's inventory-aware entry policy, in the existing replay framework.

components: (1) time-decaying residual budget, (2) adverse-selection-adjusted
entry threshold, (3) existing completion logic preserved untouched.
PENALTY is calibrated ONLY on ledger legs settled before the tape begins.
"""
import json,glob,os,sys,datetime as dt,statistics
from decimal import Decimal as D, ROUND_HALF_EVEN
S=sys.argv[1]
TICK=D("0.01"); ENTRY_CUT=D(600); COMP_DL=D(600)
TAKER=D("0.0695"); MREB=D("0.0125"); QTY=D(2); CAP=D(5)
PEN={k:D(str(v)) for k,v in json.load(open(os.path.join(S,"penalty.json"))).items()}
def cents(x): return x.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)
def fee(th,C,p): return cents(th*C*p*(D(1)-p))
def touch(s,b,a): return b if s=="UP" else D(1)-a
def cross(s,b,a): return a if s=="UP" else D(1)-b
def band(o):
    o=float(o)
    for lo,hi in ((0,.20),(.20,.35),(.35,.50),(.50,.65),(.65,1.01)):
        if lo<=o<hi: return "%.2f-%.2f"%(lo,hi)
    return "0.65-1.01"
def penalty(own): return PEN.get(band(own), D("0.2306"))
def wstart(slug):
    d=slug.split("-15m-")[1]
    return dt.datetime.fromisoformat("%s-%s-%sT%s:%s:00+00:00"%(d[0:4],d[5:7],d[8:10],d[11:13],d[13:15])).timestamp()
def load():
    out=[]
    for p in sorted(glob.glob(os.path.join(S,"tape","*.jsonl"))):
        slug=os.path.basename(p)[:-6]
        bb=[json.loads(l) for l in open(p)]
        bb=[r for r in bb if r.get("kind")=="bbo" and r.get("two_sided")]
        if len(bb)<10: continue
        last=bb[-1]; bid=D(last["bid"]); ask=D(last["ask"])
        win="UP" if bid>=D("0.60") else ("DOWN" if ask<=D("0.40") else None)
        if win is None: continue
        w0=wstart(slug)
        t=[(D(int(dt.datetime.fromisoformat(r["t"]).timestamp()-w0)),D(r["bid"]),D(r["ask"])) for r in bb]
        mids=[float((b+a)/2) for _,b,a in t]
        out.append(dict(slug=slug,win=win,ticks=t,
                        vol=statistics.pstdev(mids) if len(mids)>2 else 0.0))
    return out

def sim(w, *, depth, band_lo, band_hi, comp_cap, mfloor, comp_dl,
        lam, decay, ev_select):
    ticks=w["ticks"]; win=w["win"]
    st="FLAT"; side=None; opx=None; ocost=None; comp=None; cpx=None
    matched=D(0); mcost=D(0); fees=D(0); hedge=D(0); peak=D(0)
    refusals={"EV_NEGATIVE":0,"BUDGET":0,"NO_BAND":0}
    considered=0
    for el,bid,ask in ticks:
        if st=="RESTING" and cross(side,bid,ask)<=opx:
            st="HELD"; ocost=opx; fees-=fee(MREB,QTY,opx); peak=max(peak,ocost*QTY); continue
        if st=="HELD" and cpx is not None and cross(comp,bid,ask)<=cpx:
            matched+=QTY; mcost=ocost+cpx; fees-=fee(MREB,QTY,cpx)
            if mcost>D(1): hedge+=(mcost-D(1))*QTY
            st="MATCHED"; continue
        if st=="MATCHED": break
        if st=="HELD":                                   # completion: UNTOUCHED
            if el>comp_dl: continue
            xp=cross(comp,bid,ask); pair=ocost+xp
            if pair<=comp_cap:
                f=fee(TAKER,QTY,xp); gain=(D(1)-pair)*QTY-f
                if gain>=mfloor:
                    matched+=QTY; mcost=pair; fees+=f
                    if pair>D(1): hedge+=(pair-D(1))*QTY
                    st="MATCHED"; continue
            tp=touch(comp,bid,ask)
            if ocost+tp<=comp_cap and (cpx is None or tp<cpx): cpx=tp
            continue
        if st=="FLAT":
            if el>ENTRY_CUT: continue
            considered+=1
            # ---- the time-decaying residual budget
            budget = CAP*max(D(0), D(1)-D(el)/ENTRY_CUT) if decay else CAP
            if QTY>budget: refusals["BUDGET"]+=1; continue
            # ---- candidate sides, each at its executable price
            cands=[]
            for s in ("UP","DOWN"):
                q=touch(s,bid,ask); p=q-depth*TICK
                if not (band_lo<=p<=band_hi): continue
                # risk-adjusted EV per unit. THE GROSS EDGE IS THE PAIR
                # MARGIN, not the distance behind the touch: with both legs
                # at depth d, pair = 1 - spread - 2*d*tick, so the margin a
                # completed pair earns is spread + 2*d*tick. Measuring (q-p)
                # instead made depth 0 look edgeless by construction, which
                # is an artifact of the formula and not of the economics.
                margin=(ask-bid) + 2*depth*TICK
                ev=margin - lam*penalty(p)
                cands.append((ev,p,s))
            if not cands: refusals["NO_BAND"]+=1; continue
            cands.sort(reverse=True) if ev_select else cands.sort(key=lambda x:x[1])
            ev,opx,side = cands[0]
            if lam>0 and ev<=0: refusals["EV_NEGATIVE"]+=1; opx=None; continue
            comp="UP" if side=="DOWN" else "DOWN"; st="RESTING"
    if st=="MATCHED":
        return dict(res="PAIR",pairs=QTY,m=(D(1)-mcost)*QTY-fees,r=D(0),ru=D(0),
                    hedge=hedge,peak=peak,ref=refusals,considered=considered)
    if st=="HELD":
        red=QTY if side==win else D(0)
        return dict(res="STRAND",pairs=D(0),m=D(0),r=red-ocost*QTY-fees,ru=QTY,
                    hedge=D(0),peak=peak,ref=refusals,considered=considered)
    return dict(res="NONE",pairs=D(0),m=D(0),r=D(0),ru=D(0),hedge=D(0),peak=D(0),
                ref=refusals,considered=considered)

W=load(); k=int(len(W)*0.6)
def agg(ws,**kw):
    rs=[sim(w,**kw) for w in ws]
    run=D(0); trough=D(0)
    for x in rs:
        run+=x["m"]+x["r"]; trough=min(trough,run)
    return dict(n=len(ws),npair=sum(1 for x in rs if x["res"]=="PAIR"),
        nstr=sum(1 for x in rs if x["res"]=="STRAND"),
        nnon=sum(1 for x in rs if x["res"]=="NONE"),
        pairs=sum(x["pairs"] for x in rs),m=sum(x["m"] for x in rs),
        r=sum(x["r"] for x in rs),ru=sum(x["ru"] for x in rs),
        h=sum(x["hedge"] for x in rs),peak=max([x["peak"] for x in rs] or [D(0)]),
        dd=trough,net=sum(x["m"]+x["r"] for x in rs),
        ref={k2:sum(x["ref"][k2] for x in rs) for k2 in ("EV_NEGATIVE","BUDGET","NO_BAND")})
BASE=dict(depth=2,band_lo=D("0.20"),band_hi=D("0.60"),comp_cap=D("0.96"),
          mfloor=D("0.25"),comp_dl=D(720),lam=D(0),decay=False,ev_select=False)
ARMS=[("BASELINE d2 cap0.96", dict(BASE)),
      ("D0a depth0 cap0.99",  dict(BASE,depth=0,comp_cap=D("0.99"))),
      ("D0b depth0 cap1.00",  dict(BASE,depth=0,comp_cap=D("1.00"))),
      ("D0c depth0 cap1.00 m0", dict(BASE,depth=0,comp_cap=D("1.00"),mfloor=D(0))),
      ("D1a depth1 cap0.98",  dict(BASE,depth=1,comp_cap=D("0.98"))),
      ("D1b depth1 cap1.00",  dict(BASE,depth=1,comp_cap=D("1.00"))),
      ("D2 depth2 cap1.00",   dict(BASE,depth=2,comp_cap=D("1.00"))),
     ]
H="%-32s %3s %4s %4s %4s %8s %8s %7s %7s %8s %8s"
print("windows=%d TRAIN=%d HOLDOUT=%d"%(len(W),k,len(W)-k))
print("\n"+H%("arm","n","pr","st","no","matched$","resid$","hedge$","maxDD$","NET$","trade%"))
for nm,kw in ARMS:
    for lab,ws in (("",W),("  train",W[:k]),("  HOLD",W[k:])):
        g=agg(ws,**kw)
        traded=g["npair"]+g["nstr"]
        print(H%((nm if lab=="" else lab),g["n"],g["npair"],g["nstr"],g["nnon"],
            "%+0.4f"%g["m"],"%+0.4f"%g["r"],"%0.3f"%g["h"],"%+0.4f"%g["dd"],
            "%+0.4f"%g["net"],"%3.0f%%"%(100*traded/max(1,g["n"]))))
    g=agg(W,**kw); print("     refusals: %s"%g["ref"])
