"""Create/retrieve one exact Gemini Toronto-Miami combo definition and audit its legs.

This is a bounded definition-write probe only. It never submits an order, RFQ,
position change, or other capital action. The intended logical cover is:
  combo YES(Over 0.5 regulation goals AND NOT Toronto by >2.5)
  + NO(Over 0.5 regulation goals)
  + YES(Toronto by >2.5)
which pays >=1 in ordinary determinate settlement states. Any discretionary or
undetermined-outcome branch remains rule risk and blocks certification as pure arb.
"""
from __future__ import annotations

import asyncio, json, re
from typing import Any
import httpx

from src.gemini_prediction_auth import credentials, has_credentials, rest_auth_headers
from src.gemini_sports_inventory import fetch_active_sports_events

BASE = "https://api.gemini.com"
PATH = "/v1/prediction-markets/combos"
TARGETS = {
    "TOTAL": ("MLS-2608222330-TOR-MIA-REGT", "GEMI-MLS-2608222330-TOR-MIA-REGT-O0", "Yes"),
    "SPREAD": ("MLS-2608222330-TOR-MIA-REGS", "GEMI-MLS-2608222330-TOR-MIA-REGS-TOR2", "No"),
}


def numeric_id(c: dict[str, Any]) -> str | None:
    for key in ("internalId", "contractNumericId", "contract_id", "contractId"):
        v = c.get(key)
        if v not in (None, "") and str(v).isdigit():
            return str(v)
    text = str(c.get("id") or "")
    if text.isdigit():
        return text
    m = re.fullmatch(r"\d+-(\d+)", text)
    return m.group(1) if m else None


def desc_text(c: dict[str, Any]) -> str:
    d = c.get("description")
    if isinstance(d, str):
        return d
    if not isinstance(d, dict):
        return ""
    parts=[]
    def walk(x: Any):
        if isinstance(x, dict):
            if isinstance(x.get("value"), str): parts.append(x["value"])
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(d)
    return " ".join(parts)


async def run_once() -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "GEMINI_TOR_MIA_COMBO_CREATE_AUDIT",
        "order_entry": False,
        "capital_at_risk": False,
        "combo_definition_write": True,
        "candidate_is_trade": False,
    }
    if not has_credentials():
        out["status"] = "NO_CREDENTIALS"
        print("GEMINI_TOR_MIA_COMBO_CREATE_AUDIT "+json.dumps(out,separators=(",",":")),flush=True)
        return out

    async with httpx.AsyncClient(timeout=20.0) as h:
        events = await fetch_active_sports_events(h)
        found: dict[str, dict[str, Any]] = {}
        for e in events:
            ticker=str(e.get("ticker") or "")
            for key,(wanted_event,wanted_symbol,required) in TARGETS.items():
                if ticker != wanted_event: continue
                for c in e.get("contracts") or []:
                    if not isinstance(c,dict): continue
                    sym=str(c.get("instrumentSymbol") or c.get("ticker") or "")
                    if sym != wanted_symbol: continue
                    cid=numeric_id(c)
                    if not cid: continue
                    text=desc_text(c)
                    found[key]={
                        "event":ticker,"event_title":e.get("title"),"symbol":sym,
                        "contract_id":cid,"label":c.get("label"),"required":required,
                        "market_state":c.get("marketState"),"status":c.get("status"),
                        "expiry":c.get("expiryDate") or e.get("expiryDate"),
                        "terms_url":c.get("termsAndConditionsUrl") or e.get("termsAndConditionsUrl"),
                        "description":text[:1800],
                        "mentions_postponed":("postpon" in text.lower() or "reschedul" in text.lower()),
                        "mentions_undetermined_review":("cannot be determined" in text.lower() or "outcome review" in text.lower()),
                    }
        out["targets"] = found
        if set(found) != set(TARGETS):
            out["status"]="TARGETS_INCOMPLETE"
            print("GEMINI_TOR_MIA_COMBO_CREATE_AUDIT "+json.dumps(out,separators=(",",":"),default=str),flush=True)
            return out

        payload={"legs":[
            {"contractId":found["TOTAL"]["contract_id"],"requiredOutcome":"Yes"},
            {"contractId":found["SPREAD"]["contract_id"],"requiredOutcome":"No"},
        ]}
        key,secret=credentials()
        headers=rest_auth_headers(key,secret,PATH,params=payload)
        headers["Content-Type"]="application/json"
        r=await h.post(BASE+PATH,headers=headers,json=payload)
        out["create_http_status"]=r.status_code
        try: body=r.json()
        except Exception: body={}
        combo=body.get("combo") if isinstance(body,dict) and isinstance(body.get("combo"),dict) else {}
        out["already_existed"]=body.get("alreadyExisted") if isinstance(body,dict) else None
        out["combo"]={
            "instrument_symbol":combo.get("instrumentSymbol"),
            "instrument_registered":combo.get("instrumentRegistered"),
            "leg_count":combo.get("legCount"),
            "status":combo.get("status"),
            "id":combo.get("id"),
        }
        out["create_ok"]=r.status_code in {200,201} and bool(combo.get("instrumentSymbol"))
        out["ordinary_state_cover_identity"]="C=A_AND_B; PORTFOLIO=C+NOT_A+NOT_B >= 1"
        out["rule_certified_pure_arb"]=False
        out["rule_risk_reason"]="GEMINI_UNDETERMINABLE_OUTCOME_REVIEW_PATH_REQUIRES_AUDIT"
        sym=str(combo.get("instrumentSymbol") or "")
        if sym:
            br=await h.get(f"{BASE}/v1/book/{sym}",params={"limit_bids":20,"limit_asks":20})
            out["book_http_status"]=br.status_code
            try: bb=br.json()
            except Exception: bb={}
            out["book_levels"]={"bids":len(bb.get("bids") or []) if isinstance(bb,dict) else 0,"asks":len(bb.get("asks") or []) if isinstance(bb,dict) else 0}

    print("GEMINI_TOR_MIA_COMBO_CREATE_AUDIT "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

if __name__=="__main__": asyncio.run(run_once())
