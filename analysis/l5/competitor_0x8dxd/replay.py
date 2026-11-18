import os, json, collections
from decimal import Decimal as D
os.environ["L5_BTC5M_CONTROLLER"]="ON"
os.environ["L5_BTC5M_STRADDLE_BOOK_RELATIVE"]="YES"
os.environ["L5_BTC5M_STRADDLE_JOIN_TICKS"]="0"
os.environ["L5_BTC5M_STRADDLE_PAIR_CEILING"]="0.98"
os.environ["L5_BTC5M_INVENTORY_RANKING"]="YES"
from src import l5_btc5m_controller as C

P = C.Params(entry_mode=C.STRADDLE,
             straddle_p_pair_natural=D("0.070"),
             straddle_p_complete=D("0.131"),
             straddle_p_lone_win=D("0.0981"),
             straddle_price=D("0.08"), straddle_book_relative=True,
             straddle_pair_ceiling=D("0.98"), straddle_join_ticks=0,
             inventory_ranking_enabled=True, straddle_fill_model_measured=True,
             clip_usd=D("0.30"), max_exposure_usd=D("5.00"))

DD="data/l5/competitor_0x8dxd/"
win=collections.defaultdict(list)
with open(DD+"trades.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except Exception: continue
        s=r.get("slug") or ""
        if "updown-5m" not in s: continue
        try: w=int(s.split("-")[-1])
        except Exception: continue
        t=r["timestamp"]-w
        if 0<=t<=300:
            win[s].append((t,(r.get("side") or "").upper(),r["outcome"],
                           float(r["price"]),float(r["size"])))

theirs=collections.Counter(); ours=collections.Counter(); whys=collections.Counter()
agree=0; compared=0; SAMPLE=300
for s in sorted(win)[:SAMPLE]:
    inv={"UP":{"qty":D("0"),"cost":D("0")},"DOWN":{"qty":D("0"),"cost":D("0")}}
    last={}
    for t,side,outcome,p,q in sorted(win[s]):
        o = "UP" if outcome=="Up" else "DOWN"
        if side=="BUY": last[o]=p
        if "UP" in last and "DOWN" in last:
            bid=D(str(round(last["UP"],2))); ask=D(str(round(1.0-last["DOWN"],2)))
            if ask<=bid: ask=bid+D("0.01")
            try:
                d=C.decide(book={"bid":str(bid),"ask":str(ask),"age_s":0.0},
                           t_in_window_s=float(t),inventory=inv,resting=[],
                           params=P,p_up=0.5,p_source="PROXY",p_age_s=0.0)
            except Exception as e:
                d={"action":"ERROR","why":type(e).__name__,"orders":[]}
            ours[d.get("action")]+=1; whys[str(d.get("why"))[:44]]+=1; compared+=1
            pl=d.get("orders") or []
            if pl and pl[0].get("side")==o: agree+=1
        if side!="BUY": theirs["SELL/FLIP"]+=1
        elif inv[o]["qty"]>0: theirs["EXTEND"]+=1
        elif inv["UP"]["qty"]+inv["DOWN"]["qty"]>0: theirs["COMPLETE"]+=1
        else: theirs["OPEN"]+=1
        if side=="BUY":
            inv[o]["qty"]+=D(str(q)); inv[o]["cost"]+=D(str(q))*D(str(p))
        elif inv[o]["qty"]>0:
            take=min(inv[o]["qty"],D(str(q))); avg=inv[o]["cost"]/inv[o]["qty"]
            inv[o]["qty"]-=take; inv[o]["cost"]-=take*avg
print(f"windows replayed {SAMPLE}   decision points {compared:,}")
print("\n0x8dxd ACTUAL:")
for k,v in theirs.most_common(): print(f"   {k:11} {v:7,}")
print("\nOUR CONTROLLER at the same states:")
for k,v in ours.most_common(): print(f"   {str(k):18} {v:7,}")
print(f"\nside agreement when we placed: {agree:,}")
print("\nOUR REASON CODES:")
for k,v in whys.most_common(8): print(f"   {v:7,}  {k}")
