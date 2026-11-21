"""A/B/C/D: isolate the COMPLETION improvement. Opening policy identical in all arms.
A 0.96 + existing (downward-only) requote
B 0.96 + aggressive reprice ORACLE (instant, always succeeds, no queue) -> UPPER BOUND
C 0.99 stranded-only + existing requote
D 0.99 stranded-only + aggressive reprice ORACLE -> UPPER BOUND
"""
import json,glob,os,sys,datetime as dt,statistics
from decimal import Decimal as D, ROUND_HALF_EVEN
S=sys.argv[1]
TICK=D("0.01"); ENTRY_CUT=D(600); COMP_DL=D(600)
TAKER=D("0.0695"); MREB=D("0.0125"); QTY=D(2); DEPTH=2
LO,HI=D("0.20"),D("0.60"); OPEN_CAP=D("0.96")
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
        last=bb[-1]; bid=D(last["bid"]); ask=D(last["ask"])
        win="UP" if bid>=D("0.60") else ("DOWN" if ask<=D("0.40") else None)
        if win is None: continue
        w0=wstart(slug)
        out.append(dict(slug=slug,win=win,ticks=[
            (D(int(dt.datetime.fromisoformat(r["t"]).timestamp()-w0)),D(r["bid"]),D(r["ask"])) for r in bb]))
    return out
def net_pair(cost,px,qty,kind):
    f = -fee(MREB,qty,px) if kind=="MAKER" else fee(TAKER,qty,px)
    return cost+px+f/qty, f
def sim(w, comp_cap, aggressive):
    ticks=w["ticks"]; win=w["win"]
    st="FLAT"; side=opx=ocost=comp=cpx=None
    matched=D(0); mnet=D(0); fees=D(0); peak=D(0)
    reprices=0; between=0
    for el,bid,ask in ticks:
        if st=="RESTING" and cross(side,bid,ask)<=opx:
            st="HELD"; ocost=opx; fees-=fee(MREB,QTY,opx); peak=max(peak,ocost*QTY); continue
        if st=="HELD" and cpx is not None and cross(comp,bid,ask)<=cpx:
            n,f=net_pair(ocost,cpx,QTY,"MAKER")
            matched+=QTY; mnet=n; fees+=f
            if n>OPEN_CAP: between+=1
            st="MATCHED"; continue
        if st=="MATCHED": break
        if st=="HELD":
            if el>COMP_DL: continue
            tp=touch(comp,bid,ask)
            n,_f=net_pair(ocost,tp,QTY,"MAKER")
            if n<=comp_cap:
                if cpx is None:
                    cpx=tp; reprices+=1
                elif aggressive and tp!=cpx:
                    cpx=tp; reprices+=1          # ORACLE: instant, always succeeds
                elif tp<cpx:
                    cpx=tp; reprices+=1          # existing: downward only
            continue
        if st=="FLAT":
            if el>ENTRY_CUT: continue
            c=[]
            for s in ("UP","DOWN"):
                p=touch(s,bid,ask)-DEPTH*TICK
                if LO<=p<=HI: c.append((p,s))
            if not c: continue
            c.sort(); opx,side=c[0]; comp="UP" if side=="DOWN" else "DOWN"; st="RESTING"
    if st=="MATCHED":
        return dict(res="PAIR",pairs=QTY,m=(D(1)-mnet)*QTY,r=D(0),ru=D(0),peak=peak,
                    rq=reprices,btw=between)
    if st=="HELD":
        red=QTY if side==win else D(0)
        return dict(res="STRAND",pairs=D(0),m=D(0),r=red-ocost*QTY-fees,ru=QTY,peak=peak,
                    rq=reprices,btw=0)
    return dict(res="NONE",pairs=D(0),m=D(0),r=D(0),ru=D(0),peak=D(0),rq=0,btw=0)
W=load(); k=int(len(W)*0.6)
ARMS=[("A 0.96 + existing requote      ",OPEN_CAP,False),
      ("B 0.96 + aggressive ORACLE     ",OPEN_CAP,True),
      ("C 0.99 stranded + existing     ",D("0.99"),False),
      ("D 0.99 stranded + aggr ORACLE  ",D("0.99"),True)]
H="%-32s %3s %4s %4s %7s %9s %9s %9s %6s %5s %8s"
print("windows=%d TRAIN=%d HOLDOUT=%d   (opening policy IDENTICAL in all arms)"%(len(W),k,len(W)-k))
print("\n"+H%("arm","n","pr","st","pr/win","matched$","resid$","NET$","rq","0.96+","maxExp$"))
for nm,cap,agg in ARMS:
    for lab,ws in (("",W),("  train",W[:k]),("  HOLD",W[k:])):
        rs=[sim(w,cap,agg) for w in ws]
        npair=sum(1 for x in rs if x["res"]=="PAIR"); nstr=sum(1 for x in rs if x["res"]=="STRAND")
        m=sum(x["m"] for x in rs); r=sum(x["r"] for x in rs)
        print(H%((nm if lab=="" else lab),len(ws),npair,nstr,
            "%0.2f"%(float(sum(x['pairs'] for x in rs))/max(1,len(ws))),
            "%+0.4f"%m,"%+0.4f"%r,"%+0.4f"%(m+r),
            sum(x["rq"] for x in rs),sum(x["btw"] for x in rs),
            "%0.2f"%max([x["peak"] for x in rs] or [D(0)])))
