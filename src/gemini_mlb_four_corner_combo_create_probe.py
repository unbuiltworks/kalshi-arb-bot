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

from src.gemini_prediction_auth import has_credentials, private_rest_request
from src.gemini_sports_inventory import fetch_active_sports_events

TARGETS = {
    "ATL": ("atlanta", "milwaukee", "atlanta"),
    "MIL": ("atlanta", "milwaukee", "milwaukee"),
    "WSH": ("washington", "miami", "washington"),
    "MIA": ("washington", "miami", "miami"),
}
CORNERS = (("ATL", "WSH"), ("ATL", "MIA"), ("MIL", "WSH"), ("MIL", "MIA"))


def _find_contracts(events: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for event in events:
        title = str(event.get("title") or "").lower()
        ticker = str(event.get("ticker") or "")
        # Exact Aug-22 target game times/tickers only; do not accidentally use Aug 23.
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
                cid = c.get("contractId") or c.get("id")
                if wanted_label in label and symbol and cid not in (None, ""):
                    found[code] = {"contractId": str(cid), "symbol": symbol, "label": str(c.get("label") or "")}
    return found


async def run_once() -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "GEMINI_MLB_FOUR_CORNER_COMBO_CREATE_PROBE",
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
        print("GEMINI_MLB_FOUR_CORNER_COMBO_CREATE_PROBE " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await fetch_active_sports_events(http)
    contracts = _find_contracts(events)
    out["contracts_found"] = sorted(contracts)
    out["contract_symbols"] = {k: v["symbol"] for k, v in contracts.items()}
    # Internal contract IDs are intentionally not logged; they are only sent to Gemini.
    if set(contracts) != set(TARGETS):
        out["status"] = "TARGET_CONTRACTS_INCOMPLETE"
        print("GEMINI_MLB_FOUR_CORNER_COMBO_CREATE_PROBE " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    results: list[dict[str, Any]] = []
    for left, right in CORNERS:
        params = {
            "legs": [
                {"contractId": contracts[left]["contractId"], "requiredOutcome": "Yes"},
                {"contractId": contracts[right]["contractId"], "requiredOutcome": "Yes"},
            ]
        }
        try:
            r = await private_rest_request("POST", "/v1/prediction-markets/combos", params=params, timeout=10.0)
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
    print("GEMINI_MLB_FOUR_CORNER_COMBO_CREATE_PROBE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
