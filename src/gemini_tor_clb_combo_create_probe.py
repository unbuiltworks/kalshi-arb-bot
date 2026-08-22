"""Create/retrieve canonical combo: Toronto-Miami over 0.5 YES + Columbus >2.5 NO.
No order placement or capital action.
"""
from __future__ import annotations
import asyncio, json
from typing import Any, Mapping
import httpx
from src.gemini_prediction_auth import credentials, has_credentials, rest_auth_headers

PATH="/v1/prediction-markets/combos"; BASE="https://api.gemini.com"
LEGS=[{"contractId":"494125","requiredOutcome":"Yes"},{"contractId":"494155","requiredOutcome":"No"}]

async def run_once()->dict[str,Any]:
    out={"status":"GEMINI_TOR_CLB_COMBO_CREATE","legs":LEGS,"order_entry":False,"capital_at_risk":False}
    if not has_credentials():out["error"]="NO_CREDENTIALS";print(json.dumps(out));return out
    key,secret=credentials();headers=rest_auth_headers(key,secret,PATH,params={"legs":LEGS});headers["Content-Type"]="application/json"
    async with httpx.AsyncClient(timeout=20.0) as h:r=await h.post(f"{BASE}{PATH}",headers=headers,json={"legs":LEGS})
    try:b=r.json()
    except Exception:b={}
    combo=b.get("combo") if isinstance(b,Mapping) and isinstance(b.get("combo"),Mapping) else {}
    out.update({"http_status":r.status_code,"ok":r.status_code in {200,201},"already_existed":b.get("alreadyExisted") if isinstance(b,Mapping) else None,"instrument_symbol":combo.get("instrumentSymbol"),"instrument_registered":combo.get("instrumentRegistered"),"combo_status":combo.get("status"),"error_summary":str(b.get("message") or b.get("error") or "")[:160] if isinstance(b,Mapping) else ""})
    print("GEMINI_TOR_CLB_COMBO_CREATE "+json.dumps(out,separators=(",",":"),default=str),flush=True);return out

if __name__=="__main__":asyncio.run(run_once())
