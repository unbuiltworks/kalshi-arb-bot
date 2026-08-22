"""Read-only Gemini prediction combo production availability probe.

No authenticated writes, RFQs, orders, balances, or private identifiers. Tests the
public production combo list endpoint and reports only status/schema/compact MLB
examples if enabled.
"""
from __future__ import annotations
import asyncio,json
import httpx

URL="https://api.gemini.com/v1/prediction-markets/combos"

async def run_once():
    out={"status":"GEMINI_COMBO_PRODUCTION_PROBE","read_only":True,"order_entry":False,"rfq":False,"combo_creation":False}
    async with httpx.AsyncClient(timeout=20.0) as h:
        try:
            r=await h.get(URL,params={"status":"Active","instrumentRegistered":"true","limit":500,"offset":0})
            out["http_status"]=r.status_code
            try: body=r.json()
            except Exception: body={}
            out["body_keys"]=sorted(body.keys()) if isinstance(body,dict) else []
            combos=body.get("combos") or [] if isinstance(body,dict) else []
            out["combo_count"]=len(combos)
            examples=[]
            for row in combos:
                if not isinstance(row,dict): continue
                contract=row.get("contract") or {}
                legs=row.get("legs") or []
                text=(str(contract.get("contractName") or "")+" "+json.dumps(legs,default=str)).lower()
                if not any(x in text for x in ("atlanta","milwaukee","washington","miami","mlb","baseball")): continue
                examples.append({
                    "symbol":contract.get("contractTicker") or contract.get("instrumentSymbol"),
                    "name":contract.get("contractName"),
                    "status":contract.get("contractStatus"),
                    "leg_count":len(legs),
                    "legs":[{
                        "contractId":x.get("contractId"),
                        "requiredOutcome":x.get("requiredOutcome"),
                        "ticker":((x.get("contract") or {}).get("contractTicker") if isinstance(x.get("contract"),dict) else None),
                        "name":((x.get("contract") or {}).get("contractName") if isinstance(x.get("contract"),dict) else None),
                    } for x in legs if isinstance(x,dict)],
                })
                if len(examples)>=10: break
            out["mlb_examples"]=examples
            if r.status_code!=200:
                msg=str(body.get("message") or body.get("error") or "") if isinstance(body,dict) else ""
                out["error_class"]="PRODUCTION_DISABLED" if r.status_code==503 else "HTTP_ERROR"
                out["error_summary"]=msg[:160]
        except Exception as exc:
            out["transport_error"]=f"{type(exc).__name__}:{str(exc)[:160]}"
    print("GEMINI_COMBO_PRODUCTION_PROBE "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

def main(): asyncio.run(run_once())
if __name__=="__main__": main()
