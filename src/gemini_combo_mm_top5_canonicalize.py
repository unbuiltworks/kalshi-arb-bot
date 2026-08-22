"""Create/retrieve exactly five shadow-selected Gemini combo definitions.

This is metadata/instrument registration only: no orders, positions, or capital.
It validates Gemini's product-level correlation gate before any live promotion.
"""
from __future__ import annotations

import asyncio, json
from typing import Any, Mapping
import httpx

from src.gemini_prediction_auth import credentials, has_credentials, rest_auth_headers

BASE="https://api.gemini.com"
PATH="/v1/prediction-markets/combos"
CANDIDATES=[
    {"name":"tottenham_draw_no__nym_chw_over25_yes","legs":[("431650","No"),("498224","Yes")]},
    {"name":"tor_mia_over05_yes__chc_sea_over15_yes","legs":[("494125","Yes"),("498299","Yes")]},
    {"name":"tor_by25_no__sea_by25_no","legs":[("494118","No"),("494109","No")]},
    {"name":"clb_by25_no__sea_cin_over05_yes","legs":[("494155","No"),("494116","Yes")]},
    {"name":"dal_by25_no__chi_nyrb_over05_yes","legs":[("494222","No"),("494143","Yes")]},
]

async def create_one(http:httpx.AsyncClient,row:dict[str,Any])->dict[str,Any]:
    payload={"legs":[{"contractId":cid,"requiredOutcome":outcome} for cid,outcome in row["legs"]]}
    result={"name":row["name"],"legs":payload["legs"],"combo_definition_write":True,"order_entry":False,"capital_at_risk":False}
    if not has_credentials():
        result["ok"]=False;result["error_summary"]="NO_CREDENTIALS";return result
    key,secret=credentials();headers=rest_auth_headers(key,secret,PATH,params=payload);headers["Content-Type"]="application/json"
    try:
        r=await http.post(f"{BASE}{PATH}",headers=headers,json=payload)
        result["http_status"]=r.status_code
        try:b=r.json()
        except Exception:b={}
        combo=b.get("combo") if isinstance(b,Mapping) and isinstance(b.get("combo"),Mapping) else {}
        result.update({
            "ok":r.status_code in {200,201} and bool(combo.get("instrumentSymbol")) and combo.get("instrumentRegistered") is True and str(combo.get("status") or "").lower()=="active",
            "already_existed":b.get("alreadyExisted") if isinstance(b,Mapping) else None,
            "instrument_symbol":combo.get("instrumentSymbol"),
            "instrument_registered":combo.get("instrumentRegistered"),
            "combo_status":combo.get("status"),
            "error_summary":str((b.get("message") or b.get("error") or "") if isinstance(b,Mapping) else "")[:220],
        })
    except Exception as exc:
        result["ok"]=False;result["error_summary"]=f"{type(exc).__name__}:{str(exc)[:180]}"
    return result

async def run_once()->dict[str,Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        rows=[]
        for candidate in CANDIDATES:
            rows.append(await create_one(http,candidate))
    out={"status":"GEMINI_COMBO_MM_TOP5_CANONICALIZE","candidate_count":len(rows),"accepted_count":sum(1 for x in rows if x.get("ok")),"rejected_count":sum(1 for x in rows if not x.get("ok")),"results":rows,"order_entry":False,"capital_at_risk":False}
    print("GEMINI_COMBO_MM_TOP5_CANONICALIZE "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

if __name__=="__main__":asyncio.run(run_once())
