"""Zero-capital validation/create probe for a handful of extreme Gemini combos.

Creates or retrieves at most four canonical combo definitions. No orders, fills,
RFQs/RFS, positions, or capital actions. Logs only non-secret validation errors and
returned combo metadata.
"""
from __future__ import annotations
import asyncio, json
from typing import Any
import httpx
from src.gemini_prediction_auth import credentials, has_credentials, rest_auth_headers

BASE="https://api.gemini.com"
PATH="/v1/prediction-markets/combos"
CASES=[
    ("TOR_TOTAL_YES__TOR_SPREAD_NO", [("494125","Yes"),("494118","No")]),
    ("TOR_TOTAL_YES__CLB_SPREAD_NO", [("494125","Yes"),("494155","No")]),
    ("TOR_TOTAL_YES__DAL_SPREAD_NO", [("494125","Yes"),("494222","No")]),
    ("TOR_SPREAD_NO__CLB_SPREAD_NO", [("494118","No"),("494155","No")]),
]

async def run_once()->dict[str,Any]:
    out={"status":"GEMINI_EXTREME_COMBO_CREATE_VALIDATION","order_entry":False,"capital_at_risk":False,"combo_definition_write":True,"cases":[]}
    if not has_credentials():
        out["status"]="NO_CREDENTIALS"
        print("GEMINI_EXTREME_COMBO_CREATE_VALIDATION "+json.dumps(out,separators=(",",":")),flush=True)
        return out
    key,secret=credentials()
    async with httpx.AsyncClient(timeout=20.0) as h:
        for name,legs in CASES:
            payload={"legs":[{"contractId":cid,"requiredOutcome":outcome} for cid,outcome in legs]}
            headers=rest_auth_headers(key,secret,PATH,params=payload)
            headers["Content-Type"]="application/json"
            try:
                r=await h.post(BASE+PATH,headers=headers,json=payload)
                try: body=r.json()
                except Exception: body={}
                combo=body.get("combo") if isinstance(body,dict) and isinstance(body.get("combo"),dict) else {}
                row={
                    "name":name,"http_status":r.status_code,"ok":r.status_code in {200,201},
                    "error":str(body.get("error") or "")[:120] if isinstance(body,dict) else "",
                    "code":str(body.get("code") or "")[:120] if isinstance(body,dict) else "",
                    "message":str(body.get("message") or "")[:240] if isinstance(body,dict) else "",
                    "already_existed":body.get("alreadyExisted") if isinstance(body,dict) else None,
                    "instrument_symbol":combo.get("instrumentSymbol"),"instrument_registered":combo.get("instrumentRegistered"),
                    "leg_count":combo.get("legCount"),"combo_status":combo.get("status"),
                }
                sym=str(combo.get("instrumentSymbol") or "")
                if sym:
                    br=await h.get(f"{BASE}/v1/book/{sym}",params={"limit_bids":20,"limit_asks":20})
                    try: bb=br.json()
                    except Exception: bb={}
                    row["book_http_status"]=br.status_code
                    row["book_levels"]={"bids":len(bb.get("bids") or []) if isinstance(bb,dict) else 0,"asks":len(bb.get("asks") or []) if isinstance(bb,dict) else 0}
                out["cases"].append(row)
            except Exception as exc:
                out["cases"].append({"name":name,"ok":False,"transport_error":f"{type(exc).__name__}:{str(exc)[:160]}"})
    out["created_or_retrieved"]=[x.get("instrument_symbol") for x in out["cases"] if x.get("ok") and x.get("instrument_symbol")]
    print("GEMINI_EXTREME_COMBO_CREATE_VALIDATION "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

if __name__=="__main__": asyncio.run(run_once())
