"""Bounded Gemini MLB four-corner canonical combo creation probe.

Creates or retrieves exactly four 2-leg canonical combo contract definitions for:
ATL/MIL x WSH/MIA on Aug 22, 2026. This performs NO order placement, RFQ, fill,
position, or capital action. Credentials remain in Railway and are never logged.

A created combo is NOT a trade and is NOT executable evidence until its book is
freshly priced and full settlement/void equivalence is certified.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from src.gemini_prediction_auth import credentials, has_credentials, rest_auth_headers
from src.gemini_sports_inventory import fetch_active_sports_events

TARGETS = {
    "ATL": ("atlanta", "milwaukee", "atlanta"),
    "MIL": ("atlanta", "milwaukee", "milwaukee"),
    "WSH": ("washington", "miami", "washington"),
    "MIA": ("washington", "miami", "miami"),
}
CORNERS = (("ATL", "WSH"), ("ATL", "MIA"), ("MIL", "WSH"), ("MIL", "MIA"))
PATH = "/v1/prediction-markets/combos"
BASE = "https://api.gemini.com"


def _numeric_contract_id(c: dict[str, Any]) -> str | None:
    """Combo API requires the numeric internal ID, not composite sports contractId."""
    for key in ("id", "internalId", "contractNumericId", "contract_id"):
        value = c.get(key)
        if value not in (None, "") and str(value).isdigit():
            return str(value)
    value = c.get("contractId")
    if value not in (None, "") and str(value).isdigit():
        return str(value)
    return None


def _find_contracts(events: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for event in events:
        title = str(event.get("title") or "").lower()
        ticker = str(event.get("ticker") or "")
        if ticker not in {"MLB-2608221810-ATL-MIL-M", "MLB-2608222010-WSH-MIA-M"}:
            continue
        for code, (a, b, wanted_label) in TARGETS.items():
            if not (a in title and b in title):
                continue
            for c in event.get("contracts") or []:
                if not isinstance(c, dict):
                    continue
                label = str(c.get("label") or c.get("contractName") or "").lower()
                symbol = str(c.get("instrumentSymbol") or c.get("ticker") or "")
                cid = _numeric_contract_id(c)
                if wanted_label in label and symbol and cid:
                    found[code] = {"contractId": cid, "symbol": symbol, "label": str(c.get("label") or "")}
    return found


async def _create_combo_json(http: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    key, secret = credentials()
    headers = rest_auth_headers(key, secret, PATH, params=body)
    headers["Content-Type"] = "application/json"
    return await http.post(f"{BASE}{PATH}", headers=headers, json=body)


async def run_once() -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "GEMINI_MLB_FOUR_CORNER_COMBO_CREATE_PROBE_V3",
        "target": "ATL/MIL x WSH/MIA Aug-22",
        "requested_corners": ["+".join(x) for x in CORNERS],
        "order_entry": False,
        "rfq": False,
        "capital_at_risk": False,
        "combo_definition_write": True,
        "candidate_is_trade": False,
    }
    if not has_credentials():
        out["status"] = "NO_CREDENTIALS"
        print("GEMINI_MLB_FOUR_CORNER_COMBO_CREATE_PROBE_V3 " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await fetch_active_sports_events(http)
        contracts = _find_contracts(events)
        out["contracts_found"] = sorted(contracts)
        out["contract_symbols"] = {k: v["symbol"] for k, v in contracts.items()}
        if set(contracts) != set(TARGETS):
            out["status"] = "TARGET_NUMERIC_CONTRACT_IDS_INCOMPLETE"
            # Log only public field names for target contract objects, never credentials.
            target_key_sets=[]
            for event in events:
                if str(event.get("ticker") or "") in {"MLB-2608221810-ATL-MIL-M", "MLB-2608222010-WSH-MIA-M"}:
                    for c in event.get("contracts") or []:
                        if isinstance(c,dict):
                            target_key_sets.append(sorted(str(k) for k in c.keys()))
            out["target_contract_public_key_sets"] = target_key_sets[:4]
            print("GEMINI_MLB_FOUR_CORNER_COMBO_CREATE_PROBE_V3 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        results: list[dict[str, Any]] = []
        for left, right in CORNERS:
            payload = {
                "legs": [
                    {"contractId": contracts[left]["contractId"], "requiredOutcome": "Yes"},
                    {"contractId": contracts[right]["contractId"], "requiredOutcome": "Yes"},
                ]
            }
            try:
                r = await _create_combo_json(http, payload)
                try:
                    body = r.json()
                except Exception:
                    body = {}
                combo = body.get("combo") if isinstance(body, dict) and isinstance(body.get("combo"), dict) else {}
                results.append({
                    "corner": f"{left}+{right}",
                    "http_status": r.status_code,
                    "ok": r.status_code in {200, 201},
                    "already_existed": body.get("alreadyExisted") if isinstance(body, dict) else None,
                    "instrument_symbol": combo.get("instrumentSymbol"),
                    "instrument_registered": combo.get("instrumentRegistered"),
                    "leg_count": combo.get("legCount"),
                    "status": combo.get("status"),
                    "error_summary": str(body.get("message") or body.get("error") or "")[:160] if isinstance(body, dict) else "",
                })
            except Exception as exc:
                results.append({"corner": f"{left}+{right}", "ok": False, "transport_error": f"{type(exc).__name__}:{str(exc)[:160]}"})

    out["results"] = results
    out["successful_combo_definitions"] = sum(bool(x.get("ok")) for x in results)
    out["all_four_available"] = out["successful_combo_definitions"] == 4
    print("GEMINI_MLB_FOUR_CORNER_COMBO_CREATE_PROBE_V3 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
