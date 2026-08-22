"""Create/retrieve exactly one Crystal Palace NO + Alexander Albon NO Gemini combo.

This writes only a canonical combo contract definition. It submits no order/RFQ,
creates no position, and risks no capital. Credentials remain in Railway.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from src.gemini_prediction_auth import credentials, has_credentials, rest_auth_headers

BASE = "https://api.gemini.com"
PATH = "/v1/prediction-markets/combos"
LEGS = [
    {"contractId": "431643", "requiredOutcome": "No"},
    {"contractId": "396739", "requiredOutcome": "No"},
]


async def run_once() -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "GEMINI_CRY_ALBON_COMBO_CREATE_PROBE",
        "order_entry": False,
        "rfq": False,
        "capital_at_risk": False,
        "combo_definition_write": True,
        "candidate_is_trade": False,
        "legs": [
            {"contractId": "431643", "requiredOutcome": "No", "label": "Crystal Palace"},
            {"contractId": "396739", "requiredOutcome": "No", "label": "Alexander Albon"},
        ],
    }
    if not has_credentials():
        out["result"] = "NO_CREDENTIALS"
        print("GEMINI_CRY_ALBON_COMBO_CREATE_PROBE " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    payload = {"legs": LEGS}
    key, secret = credentials()
    headers = rest_auth_headers(key, secret, PATH, params=payload)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.post(f"{BASE}{PATH}", headers=headers, json=payload)
    try:
        body = response.json()
    except Exception:
        body = {}
    combo = body.get("combo") if isinstance(body, dict) and isinstance(body.get("combo"), dict) else {}
    out.update({
        "http_status": response.status_code,
        "ok": response.status_code in {200, 201},
        "already_existed": body.get("alreadyExisted") if isinstance(body, dict) else None,
        "instrument_symbol": combo.get("instrumentSymbol"),
        "instrument_registered": combo.get("instrumentRegistered"),
        "leg_count": combo.get("legCount"),
        "combo_status": combo.get("status"),
        "error_summary": str(body.get("message") or body.get("error") or "")[:180] if isinstance(body, dict) else "",
    })
    print("GEMINI_CRY_ALBON_COMBO_CREATE_PROBE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
