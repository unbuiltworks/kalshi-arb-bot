"""A/C on the FOUR VENUE-CONFIRMED fills: does a 0.99 stranded-only ceiling,
net of fees, convert any window that 0.96 refused?"""
import json,os,sys,datetime as dt
from decimal import Decimal as D, ROUND_HALF_EVEN
S=sys.argv[1]
TAKER=D("0.0695"); MREB=D("0.0125")
def cents(x): return x.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)
def fee(th,C,p): return cents(th*C*p*(D(1)-p))
def touch(s,b,a): return b if s=="UP" else D(1)-a
def cross(s,b,a): return a if s=="UP" else D(1)-b
FILLS=[("cpc-btc-updown-15m-2026-11-02-2215z","DOWN","0.39","1.92","2026-11-02T22:17:09.431655+00:00","UP"),
       ("cpc-btc-updown-15m-2026-11-02-2230z","UP",  "0.47","2.04","2026-11-02T22:32:10.618852+00:00","DOWN"),
       ("cpc-btc-updown-15m-2026-11-02-2245z","DOWN","0.45","2.00","2026-11-02T22:45:35.850902+00:00","UP"),
       ("cpc-btc-updown-15m-2026-11-02-2300z","UP",  "0.21","1.68","2026-11-02T23:00:21.372391+00:00","DOWN")]
def wstart(slug):
    d=slug.split("-15m-")[1]
    return dt.datetime.fromisoformat("%s-%s-%sT%s:%s:00+00:00"%(d[0:4],d[5:7],d[8:10],d[11:13],d[13:15])).timestamp()
print("NET pair cost = held_own_cost + complement_px +/- fee/qty   (maker rebate is a CREDIT)")
print("%-6s %-5s %-7s %-9s %-10s %-10s %-9s %-9s"%("win","comp","best_px","gross","fee/qty","NET","<=0.96?","<=0.99?"))
conv=[]
for slug,side,cost,qty,futc,comp in FILLS:
    cost=D(cost); qty=D(qty); ft=dt.datetime.fromisoformat(futc).timestamp(); w0=wstart(slug)
    bb=[json.loads(l) for l in open(os.path.join(S,"tape",slug+".jsonl"))]
    bb=[r for r in bb if r.get("kind")=="bbo" and r.get("two_sided")
        and dt.datetime.fromisoformat(r["t"]).timestamp()>ft
        and D(int(dt.datetime.fromisoformat(r["t"]).timestamp()-w0))<=D(600)]
    best=None
    for r in bb:
        b,a=D(r["bid"]),D(r["ask"])
        for kind,px in (("MAKER",touch(comp,b,a)),("TAKER",cross(comp,b,a))):
            f = -fee(MREB,qty,px) if kind=="MAKER" else fee(TAKER,qty,px)
            net = cost+px+f/qty
            if best is None or net<best[0]: best=(net,px,kind,f,r["t"])
    if best is None: print("%-6s no post-fill tape"%slug[-5:]); continue
    net,px,kind,f,t=best
    ok96 = net<=D("0.96"); ok99 = net<=D("0.99")
    print("%-6s %-5s %-7s %-9s %-10s %-10s %-9s %-9s  via %s @%s"%(
        slug[-5:],comp,px,cost+px,("%+0.4f"%(f/qty)),("%0.4f"%net),ok96,ok99,kind,t[11:19]))
    if ok99 and not ok96: conv.append((slug[-5:],net,qty,px))
print()
print("WINDOWS CONVERTED BY 0.99 THAT 0.96 REFUSED: %d"%len(conv))
tot=D(0)
for w,net,qty,px in conv:
    gain=(D(1)-net)*qty; tot+=gain
    print("   %s net=%s qty=%s -> matched profit %+0.4f (vs stranded loss)"%(w,net,qty,gain))
print("   total matched profit from the conversion: %+0.4f"%tot)
