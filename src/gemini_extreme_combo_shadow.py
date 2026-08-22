"""Read-only Gemini sports extreme-outcome combo candidate scanner.

Finds high-probability YES/NO outcomes whose exact complement is cheap, then
scores 2-leg canonical-combo maker quotes under q=1 fees. No combo creation and
no order entry.
"""
from __future__ import annotations
import asyncio,json,re
from decimal import Decimal, ROUND_CEILING
from itertools import combinations
from typing import Any,Mapping
import httpx
from src.gemini_sports_inventory import fetch_active_sports_events

D=Decimal; ONE=D("1"); MAKER=D("0.0175"); TAKER=D("0.07"); TARGET=D("0.03")

def dec(v:Any):
 try:x=D(str(v))
 except Exception:return None
 return x if x.is_finite() else None

def fee(p:D,rate:D):
 return (rate*p*(ONE-p)*D("100")).to_integral_value(rounding=ROUND_CEILING)/D("100")

def numeric_id(c:Mapping[str,Any]):
 s=str(c.get("id") or "")
 if s.isdigit():return s
 m=re.fullmatch(r"\d+-(\d+)",s)
 return m.group(1) if m else None

def max_combo_price(hedges:D):
 for cents in range(99,0,-1):
  p=D(cents)/D("100")
  if p+fee(p,MAKER)+hedges <= ONE-TARGET:return p
 return None

async def run_once():
 async with httpx.AsyncClient(timeout=30.0) as h: events=await fetch_active_sports_events(h)
 legs=[]
 for e in events:
  et=str(e.get("ticker") or ""); title=str(e.get("title") or "")
  # Sports only from fetcher; avoid contracts already closed/paused.
  for c in e.get("contracts") or []:
   if not isinstance(c,Mapping) or str(c.get("status") or "").lower()!="active" or str(c.get("marketState") or "").lower()!="open":continue
   prices=c.get("prices") if isinstance(c.get("prices"),Mapping) else {}
   ask=dec(prices.get("bestAsk")); bid=dec(prices.get("bestBid")); cid=numeric_id(c); sym=str(c.get("instrumentSymbol") or "")
   if ask is None or bid is None or not cid or not sym or not(D("0")<=bid<ask<=ONE):continue
   yes_comp_cost=ask+fee(ask,TAKER)              # complement cost if selected outcome is NO
   noask=ONE-bid; no_comp_cost=noask+fee(noask,TAKER) # complement cost if selected outcome is YES
   mid=(ask+bid)/D("2")
   # Keep exact complements <= 6c all-in. Selected side is therefore high probability.
   if yes_comp_cost<=D("0.06"):
    legs.append({"event":et,"event_title":title,"symbol":sym,"contract_id":cid,"label":c.get("label"),"required":"No","complement":"Yes","complement_ask":ask,"complement_fee":fee(ask,TAKER),"complement_all_in":yes_comp_cost,"selected_mid":ONE-mid,"expiry":c.get("expiryDate"),"terms":c.get("termsAndConditionsUrl")})
   if no_comp_cost<=D("0.06"):
    legs.append({"event":et,"event_title":title,"symbol":sym,"contract_id":cid,"label":c.get("label"),"required":"Yes","complement":"No","complement_ask":noask,"complement_fee":fee(noask,TAKER),"complement_all_in":no_comp_cost,"selected_mid":mid,"expiry":c.get("expiryDate"),"terms":c.get("termsAndConditionsUrl")})
 legs.sort(key=lambda x:(x["complement_all_in"],-x["selected_mid"]))
 # Limit candidate universe to strongest 80 while preserving broad event diversity.
 top=legs[:80]; plans=[]
 for a,b in combinations(top,2):
  if a["contract_id"]==b["contract_id"]:continue
  # Favor different physical events to avoid correlation assumptions in fill-likelihood proxy.
  hedges=a["complement_all_in"]+b["complement_all_in"]
  mp=max_combo_price(hedges)
  if mp is None:continue
  locked=ONE-(mp+fee(mp,MAKER)+hedges)
  indep=a["selected_mid"]*b["selected_mid"]
  gap=indep-mp
  plans.append({"maker_price":mp,"locked":locked,"independence_fair":indep,"fair_minus_maker":gap,"same_event":a["event"]==b["event"],"hedge_cost":hedges,"legs":[a,b]})
 plans.sort(key=lambda x:(x["fair_minus_maker"],x["same_event"],-x["maker_price"]))
 def cleanleg(x):return {k:(str(v) if isinstance(v,D) else v) for k,v in x.items()}
 def cleanplan(p):return {"maker_price":str(p["maker_price"]),"locked":str(p["locked"]),"independence_fair":str(p["independence_fair"]),"fair_minus_maker":str(p["fair_minus_maker"]),"same_event":p["same_event"],"hedge_cost":str(p["hedge_cost"]),"legs":[cleanleg(x) for x in p["legs"]]}
 out={"status":"GEMINI_EXTREME_COMBO_SHADOW","sports_events":len(events),"extreme_legs":len(legs),"top_extreme_legs":[cleanleg(x) for x in legs[:30]],"pair_plans_scored":len(plans),"best_pair_plans":[cleanplan(x) for x in plans[:20]],"maker_rate":"0.0175","taker_rate":"0.07","target_locked":"0.03","maker_rebate_counted":False,"classification":"SHADOW_COMBO_NOT_CREATED","order_entry":False,"read_only":True}
 print("GEMINI_EXTREME_COMBO_SHADOW "+json.dumps(out,separators=(",",":"),default=str),flush=True)
 return out

def main():asyncio.run(run_once())
if __name__=="__main__":main()
