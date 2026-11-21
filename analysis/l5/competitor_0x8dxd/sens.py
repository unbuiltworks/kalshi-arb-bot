import os, json, collections
from decimal import Decimal as D
os.environ["L5_BTC5M_CONTROLLER"]="ON"
from src import l5_btc5m_controller as C
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
keys=sorted(win)[:300]
def run(ceil, clip):
    P=C.Params(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
               straddle_book_relative=True, straddle_pair_ceiling=D(ceil),
               straddle_join_ticks=0, inventory_ranking_enabled=True,
               straddle_fill_model_measured=True,
               straddle_p_pair_natural=D("0.070"), straddle_p_complete=D("0.131"),
               straddle_p_lone_win=D("0.0981"), clip_usd=D(clip),
               max_exposure_usd=D("5.00"))
    acts=0; why=collections.Counter()
    for s in keys:
        inv={"UP":{"qty":D("0"),"cost":D("0")},"DOWN":{"qty":D("0"),"cost":D("0")}}
        last={}
        for t,side,outcome,p,q in sorted(win[s]):
            o="UP" if outcome=="Up" else "DOWN"
            if side=="BUY": last[o]=p
            if "UP" in last and "DOWN" in last:
                bid=D(str(round(last["UP"],2))); ask=D(str(round(1.0-last["DOWN"],2)))
                if ask<=bid: ask=bid+D("0.01")
                try:
                    d=C.decide(book={"bid":str(bid),"ask":str(ask),"age_s":0.0},
                               t_in_window_s=float(t),inventory=inv,resting=[],
                               params=P,p_up=0.5,p_source="PROXY",p_age_s=0.0)
                except Exception: d={"action":"ERR","why":"ERR","orders":[]}
                if d.get("orders"): acts+=1
                why[str(d.get("why"))[:30]]+=1
            if side=="BUY":
                inv[o]["qty"]+=D(str(q)); inv[o]["cost"]+=D(str(q))*D(str(p))
            elif inv[o]["qty"]>0:
                take=min(inv[o]["qty"],D(str(q))); avg=inv[o]["cost"]/inv[o]["qty"]
                inv[o]["qty"]-=take; inv[o]["cost"]-=take*avg
    return acts, why
print(f"{'pair_ceiling':>13} {'clip$':>7} {'we_place':>9}  top refusal")
for ceil in ("0.96","0.98","1.00"):
    a,w = run(ceil,"0.30")
    top=[k for k,_ in w.most_common(1)]
    print(f"{ceil:>13} {'0.30':>7} {a:9,}  {top[0] if top else '-'}")
print("\nOFFLINE SENSITIVITY ONLY. No production parameter is changed by this.")
