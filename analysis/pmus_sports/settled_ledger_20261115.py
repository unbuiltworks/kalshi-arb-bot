import json, glob, sys, time, math, urllib.request, collections
from decimal import Decimal as D, ROUND_CEILING
S=sys.argv[1]
def ev(f):
    for r in json.load(open(f))["deploy"]:
        m=r["message"]
        if m.startswith("PMUS_TWO_SIDED "):
            yield r["timestamp"], json.loads(m.split(" ",1)[1])
fills=[]; seen=set()
for f in sorted(glob.glob(S+"/fills/w*.json")):
    for ts,e in ev(f):
        if e.get("kind")!="FILL": continue
        k=(e["order_id"],e["qty"],e["t"])
        if k in seen: continue
        seen.add(k); fills.append((ts,e))
exit_ids={e["order_id"] for ts,e in ev(S+"/fills/exits.json") if e.get("kind")=="ORDER"}
mk=collections.defaultdict(lambda:{"Y":D(0),"cY":D(0),"N":D(0),"cN":D(0),"first":None,"taker":collections.defaultdict(lambda:[D(0),D(0)])})
for ts,e in fills:
    m=mk[e["slug"]]; q=D(e["qty"]); p=D(e["px"])
    s="Y" if e["side"]=="YES" else "N"
    m[s]+=q; m["c"+s]+=q*p
    m["first"]=min(m["first"] or ts, ts)
    if e["order_id"] in exit_ids:
        t=m["taker"][e["order_id"]]; t[0]+=q; t[1]=p
def fee(q,p): return (D("0.06")*q*p*(1-p)).quantize(D("0.01"),rounding=ROUND_CEILING)
GW="https://gateway.polymarket.us"
cache=S+"/settle.json"
try: st=json.load(open(cache))
except Exception: st={}
for slug in mk:
    if slug in st and st[slug].get("final"): continue
    try:
        with urllib.request.urlopen(f"{GW}/v1/markets/{slug}/bbo",timeout=10) as r:
            md=json.load(r).get("marketData") or {}
        st[slug]={"state":md.get("state"),"px":md.get("settlementPx"),
                  "final":md.get("state") in ("MARKET_STATE_EXPIRED","MARKET_STATE_SETTLED","MARKET_STATE_RESOLVED") and md.get("settlementPx") is not None}
    except Exception as x: st[slug]={"err":str(x)[:80],"final":False}
    time.sleep(0.15)
json.dump(st,open(cache,"w"))
REC=sys.argv[2] if len(sys.argv)>2 else "2026-11-15T18:30:30"
rows=[]
for slug,m in mk.items():
    s=st.get(slug,{}); fees=sum(fee(q,p) for q,p in m["taker"].values() if q>0)
    Y,N=m["Y"],m["N"]; pairs=min(Y,N)
    aY=m["cY"]/Y if Y else D(0); aN=m["cN"]/N if N else D(0)
    pair_pnl=pairs*(1-aY-aN) if pairs else D(0)
    resid_side="Y" if Y>N else ("N" if N>Y else None); rq=abs(Y-N)
    r={"slug":slug,"sport":slug.split("-")[1],"Y":Y,"N":N,"pairs":pairs,"pair_pnl":pair_pnl,"fees":fees,
       "resid_side":resid_side,"rq":rq,"lead_px":(aY if resid_side=="Y" else aN) if resid_side else None,
       "state":s.get("state"),"final":s.get("final"),"px":s.get("px"),"new":m["first"]>=REC,
       "cost":m["cY"]+m["cN"],"taker_q":sum(q for q,p in m["taker"].values())}
    if s.get("final"):
        px=D(str(s["px"]["value"] if isinstance(s["px"],dict) else s["px"]))
        r["settled"]=Y*px+N*(1-px)-m["cY"]-m["cN"]
        r["resid_pnl"]=r["settled"]-pair_pnl
    rows.append(r)
json.dump(rows,open(S+"/ledger.json","w"),default=str)
fin=[r for r in rows if r["final"]]; opn=[r for r in rows if not r["final"]]
tot=sum(r["settled"] for r in fin); fe=sum(r["fees"] for r in fin); fall=sum(r["fees"] for r in rows)
print("fill events",len(fills),"contracts",sum(D(e["qty"]) for _,e in fills),"markets",len(rows))
print("settled markets",len(fin),"gross settled",tot,"est fees settled",fe,"NET",tot-fe)
print("pair pnl settled",sum(r["pair_pnl"] for r in fin),"residual pnl settled",sum(r["resid_pnl"] for r in fin))
print("unsettled markets",len(opn),"open cost",sum(r["cost"] for r in opn),"states",collections.Counter(r["state"] for r in opn))
print("fees est all",fall,"(venue charged 4.26)")
sp=collections.defaultdict(lambda:[0,D(0),D(0),D(0),D(0)])
for r in fin:
    a=sp[r["sport"]]; a[0]+=1; a[1]+=r["pair_pnl"]; a[2]+=r["resid_pnl"]; a[3]+=r["fees"]; a[4]+=r["settled"]-r["fees"]
print("sport n pair resid fees net")
for k,a in sorted(sp.items(),key=lambda kv: kv[1][4]): print(k,a[0],*[str(x.quantize(D('0.01'))) for x in a[1:]])
print("worst")
for r in sorted(fin,key=lambda r:r["settled"]-r["fees"])[:8]:
    print(r["slug"],"Y",r["Y"],"N",r["N"],"px",r["px"],"net",(r["settled"]-r["fees"]).quantize(D('0.01')),"resid",r["rq"],r["resid_side"],"lead",r["lead_px"])
print("new-controller markets",sum(1 for r in rows if r["new"]),"settled",sum(1 for r in fin if r["new"]))
