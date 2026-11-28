"""Arm B's gain, degraded. The oracle assumes instant repricing that always
succeeds and always fills when the wire reaches it. Each knob below removes
one of those assumptions. No fill is credited that the degraded model refuses.
"""
import json,glob,os,sys,datetime as dt,random
from decimal import Decimal as D, ROUND_HALF_EVEN
S=sys.argv[1]
TICK=D("0.01"); ENTRY_CUT=D(600); COMP_DL=D(600)
TAKER=D("0.0695"); MREB=D("0.0125"); QTY=D(2); DEPTH=2
LO,HI=D("0.20"),D("0.60"); CAP=D("0.96")
def cents(x): return x.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)
def fee(th,C,p): return cents(th*C*p*(D(1)-p))
def touch(s,b,a): return b if s=="UP" else D(1)-a
def cross(s,b,a): return a if s=="UP" else D(1)-b
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
        last=bb[-1]
        win="UP" if D(last["bid"])>=D("0.60") else ("DOWN" if D(last["ask"])<=D("0.40") else None)
        if win is None: continue
        w0=wstart(slug)
        out.append(dict(slug=slug,win=win,ticks=[
            (D(int(dt.datetime.fromisoformat(r["t"]).timestamp()-w0)),D(r["bid"]),D(r["ask"])) for r in bb]))
    return out
def sim(w, aggressive, p_fill, reprice_lag_ticks, rng):
    ticks=w["ticks"]; win=w["win"]
    st="FLAT"; side=opx=ocost=comp=cpx=None; armed_at=None
    matched=D(0); mnet=D(0); fees=D(0); rq=0
    for i,(el,bid,ask) in enumerate(ticks):
        if st=="RESTING" and cross(side,bid,ask)<=opx:
            st="HELD"; ocost=opx; fees-=fee(MREB,QTY,opx); continue
        if st=="HELD" and cpx is not None and armed_at is not None and i>=armed_at:
            if cross(comp,bid,ask)<=cpx and rng.random()<p_fill:   # queue sensitivity
                f=-fee(MREB,QTY,cpx); matched+=QTY; mnet=ocost+cpx+f/QTY; fees+=f
                st="MATCHED"; continue
        if st=="MATCHED": break
        if st=="HELD":
            if el>COMP_DL: continue
            tp=touch(comp,bid,ask)
            if ocost+tp<=CAP:
                if cpx is None or (aggressive and tp!=cpx) or tp<cpx:
                    if cpx!=tp:
                        cpx=tp; rq+=1
                        armed_at=i+reprice_lag_ticks    # latency: not live yet
            continue
        if st=="FLAT":
            if el>ENTRY_CUT: continue
            c=[]
            for s in ("UP","DOWN"):
                p=touch(s,bid,ask)-DEPTH*TICK
                if LO<=p<=HI: c.append((p,s))
            if not c: continue
            c.sort(); opx,side=c[0]; comp="UP" if side=="DOWN" else "DOWN"; st="RESTING"
    if st=="MATCHED": return (D(1)-mnet)*QTY, 1, 0, rq
    if st=="HELD":
        red=QTY if side==win else D(0)
        return red-ocost*QTY-fees, 0, 1, rq
    return D(0),0,0,rq
W=load(); k=int(len(W)*0.6)
def run(aggressive,p_fill,lag,trials=200):
    tr=[];ho=[];prs=[];rqs=[]
    for t in range(trials if p_fill<1 else 1):
        rng=random.Random(1000+t)
        a=sum(sim(w,aggressive,p_fill,lag,rng)[0] for w in W[:k])
        b=sum(sim(w,aggressive,p_fill,lag,rng)[0] for w in W[k:])
        rng=random.Random(1000+t)
        rs=[sim(w,aggressive,p_fill,lag,rng) for w in W]
        tr.append(float(a));ho.append(float(b))
        prs.append(sum(x[1] for x in rs)); rqs.append(sum(x[3] for x in rs))
    n=len(tr)
    return sum(tr)/n,sum(ho)/n,sum(prs)/n,sum(rqs)/n
print("ARM B SENSITIVITY  (p_fill = probability a touched resting order actually fills;")
print("                    lag = ticks before a replacement is live, ~4.4s median each)")
print("\n%-40s %8s %9s %7s %6s"%("configuration","train","HOLDOUT","pairs","rq"))
base=run(False,1.0,0)
print("%-40s %+8.4f %+9.4f %7.1f %6.0f"%("A existing requote, oracle fills",*base))
for lag in (0,1,2):
    for pf in (1.0,0.75,0.50,0.25):
        t,h,p,r=run(True,pf,lag)
        tag="B aggressive p_fill=%.2f lag=%d tick"%(pf,lag)
        print("%-40s %+8.4f %+9.4f %7.1f %6.0f  %s"%(tag,t,h,p,r,
              "BEATS A" if h>base[1] else ""))
