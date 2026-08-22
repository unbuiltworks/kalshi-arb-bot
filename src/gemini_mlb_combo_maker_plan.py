"""Read-only planner for bounded q=1 Gemini combo maker-arb quotes.

For each of four exact two-game combo corners, calculate the highest maker BUY
price (YES and NO directions) that leaves >=3c after conservative q1 maker fee on
the combo and taker fees on the two immediately-acquired single-game hedges.
Maker rebates are intentionally ignored.
"""
from __future__ import annotations
import asyncio, json
from decimal import Decimal, ROUND_CEILING
from typing import Any, Mapping
import httpx

D=Decimal; ONE=D("1"); TARGET=D("0.03"); BASE="https://api.gemini.com"
MAKER_RATE=D("0.0175"); TAKER_RATE=D("0.07")
SINGLES={
 "ATL":"GEMI-MLB-2608221810-ATL-MIL-M-ATL","MIL":"GEMI-MLB-2608221810-ATL-MIL-M-MIL",
 "WSH":"GEMI-MLB-2608222010-WSH-MIA-M-WSH","MIA":"GEMI-MLB-2608222010-WSH-MIA-M-MIA"}
COMBOS={
 ("ATL","WSH"):"GEMI-CMB-260823-79E3B2DFF8FD088F-CMB",
 ("ATL","MIA"):"GEMI-CMB-260823-E7A3F3D7D3F85505-CMB",
 ("MIL","WSH"):"GEMI-CMB-260823-7B688FFB75657D84-CMB",
 ("MIL","MIA"):"GEMI-CMB-260823-C10BB79B90AC3A31-CMB"}
OPP={"ATL":"MIL","MIL":"ATL","WSH":"MIA","MIA":"WSH"}

def dec(v:Any)->D|None:
 try: x=D(str(v))
 except Exception:return None
 return x if x.is_finite() else None

def level(x:Any):
 if not isinstance(x,Mapping):return None
 p=dec(x.get("price")); q=dec(x.get("amount") if x.get("amount") is not None else x.get("quantity"))
 return (p,q) if p is not None and q is not None and q>0 else None

def book(body:Any):
 if not isinstance(body,Mapping):return None
 asks=[z for z in (level(x) for x in body.get("asks") or []) if z]
 bids=[z for z in (level(x) for x in body.get("bids") or []) if z]
 if not asks or not bids:return None
 ap,aq=min(asks,key=lambda z:z[0]); bp,bq=max(bids,key=lambda z:z[0])
 if not (D("0")<bp<ap<ONE):return None
 return {"ask":ap,"ask_depth":aq,"bid":bp,"bid_depth":bq,"mid":(ap+bp)/D("2")}

def fee(p:D,rate:D)->D:
 raw=rate*p*(ONE-p)
 return (raw*D("100")).to_integral_value(rounding=ROUND_CEILING)/D("100")

def allin_taker(p:D)->D:return p+fee(p,TAKER_RATE)
def allin_maker(p:D)->D:return p+fee(p,MAKER_RATE)

def max_maker_price(hedge_cost:D)->D|None:
 # Tick is 1c. Find highest quote that preserves target net locked profit.
 for cents in range(99,0,-1):
  p=D(cents)/D("100")
  if allin_maker(p)+hedge_cost <= ONE-TARGET:
   return p
 return None

async def run_once():
 syms=list(SINGLES.values())+list(COMBOS.values()); books={}; counts={}
 async with httpx.AsyncClient(timeout=15.0) as h:
  async def one(s):
   r=await h.get(f"{BASE}/v1/book/{s}",params={"limit_bids":20,"limit_asks":20})
   try:b=r.json()
   except Exception:b={}
   books[s]=book(b) if r.status_code==200 else None
   counts[s]={"http":r.status_code,"bids":len(b.get("bids") or []) if isinstance(b,Mapping) else 0,"asks":len(b.get("asks") or []) if isinstance(b,Mapping) else 0}
  await asyncio.gather(*(one(s) for s in syms))
 rows=[]
 for (x,y),cs in COMBOS.items():
  # Buy combo YES(x&y), hedge with YES(opposite x)+YES(opposite y).
  hx=books.get(SINGLES[OPP[x]]); hy=books.get(SINGLES[OPP[y]])
  if hx and hy and hx["ask_depth"]>=ONE and hy["ask_depth"]>=ONE:
   hc=allin_taker(hx["ask"])+allin_taker(hy["ask"]); p=max_maker_price(hc)
   if p:
    locked=ONE-(allin_maker(p)+hc)
    fair=(ONE-books[SINGLES[x]]["mid"])*(ONE-books[SINGLES[y]]["mid"]) if books.get(SINGLES[x]) and books.get(SINGLES[y]) else None
    rows.append({"corner":f"{x}+{y}","outcome":"YES","combo_symbol":cs,"maker_price":str(p),"maker_fee":str(fee(p,MAKER_RATE)),"hedge_cost":str(hc),"locked_profit_if_filled_now":str(locked),"hedges":[{"symbol":SINGLES[OPP[x]],"ask":str(hx["ask"]),"taker_all_in":str(allin_taker(hx["ask"])),"depth":str(hx["ask_depth"])},{"symbol":SINGLES[OPP[y]],"ask":str(hy["ask"]),"taker_all_in":str(allin_taker(hy["ask"])),"depth":str(hy["ask_depth"])}],"rough_independence_fair":str(fair) if fair is not None else None})
  # Buy combo NO(x&y), hedge with YES(x)+YES(y).
  sx=books.get(SINGLES[x]); sy=books.get(SINGLES[y])
  if sx and sy and sx["ask_depth"]>=ONE and sy["ask_depth"]>=ONE:
   hc=allin_taker(sx["ask"])+allin_taker(sy["ask"]); p=max_maker_price(hc)
   if p:
    locked=ONE-(allin_maker(p)+hc)
    fair=ONE-(books[SINGLES[x]]["mid"]*books[SINGLES[y]]["mid"]) if books.get(SINGLES[x]) and books.get(SINGLES[y]) else None
    rows.append({"corner":f"{x}+{y}","outcome":"NO","combo_symbol":cs,"maker_price":str(p),"maker_fee":str(fee(p,MAKER_RATE)),"hedge_cost":str(hc),"locked_profit_if_filled_now":str(locked),"hedges":[{"symbol":SINGLES[x],"ask":str(sx["ask"]),"taker_all_in":str(allin_taker(sx["ask"])),"depth":str(sx["ask_depth"])},{"symbol":SINGLES[y],"ask":str(sy["ask"]),"taker_all_in":str(allin_taker(sy["ask"])),"depth":str(sy["ask_depth"])}],"rough_independence_fair":str(fair) if fair is not None else None})
 rows.sort(key=lambda r:(abs(D(r["maker_price"])-D(r["rough_independence_fair"])) if r["rough_independence_fair"] else D("9"),-D(r["locked_profit_if_filled_now"])))
 out={"status":"GEMINI_MLB_COMBO_MAKER_PLAN_Q1","target_locked_profit":"0.03","maker_rate":"0.0175","taker_rate":"0.07","maker_rebate_counted":False,"raw_books":counts,"single_top_books":{k:{kk:str(vv) for kk,vv in books[s].items()} if books.get(s) else None for k,s in SINGLES.items()},"plans":rows,"best_fill_likelihood_proxy":rows[0] if rows else None,"rule_classification":"LIVE_PROBE_RULE_RISK_DUE_DISCRETIONARY_UNDETERMINABLE_OUTCOME_PATH","order_entry":False,"read_only":True}
 print("GEMINI_MLB_COMBO_MAKER_PLAN_Q1 "+json.dumps(out,separators=(",",":"),default=str),flush=True)
 return out

def main():asyncio.run(run_once())
if __name__=="__main__":main()
