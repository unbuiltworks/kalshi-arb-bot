"""Read-only reconciliation for the single authorized ETH5 V3 q=1 probe."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping

import httpx

from src.gemini_prediction_auth import private_rest_request
from src.gemini_kalshi_live_service import _gemini_available_usd

SYMBOL = "GEMI-ETH05M2608290140-UP"
TICKER = "ETH05M2608290140"
CLIENT_IDS = {
    "b5-2e7d166da943ab92403ec120-yes",
    "b5-2e7d166da943ab92403ec120-no",
}
SAFE_FIELDS = (
    "orderId", "clientOrderId", "symbol", "side", "type", "timeInForce",
    "price", "quantity", "filledQuantity", "remainingQuantity",
    "averageExecutionPrice", "eventOutcome", "status", "fee", "fees",
    "createdAt", "updatedAt", "timestamp", "expiryDate",
)
SAFE_EVENT_FIELDS = (
    "ticker", "title", "status", "marketState", "result", "resolution",
    "settlementValue", "settlementPrice", "expiryDate", "settledAt", "updatedAt",
)
SAFE_CONTRACT_FIELDS = (
    "instrumentSymbol", "marketState", "status", "result", "resolution",
    "settlementValue", "settlementPrice", "expiryDate", "updatedAt",
)


def safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in SAFE_FIELDS if key in row}


def safe_event(row: Mapping[str, Any]) -> dict[str, Any]:
    item = {key: row.get(key) for key in SAFE_EVENT_FIELDS if key in row}
    contracts = []
    for contract in row.get("contracts") or []:
        if not isinstance(contract, Mapping):
            continue
        if str(contract.get("instrumentSymbol") or "") == SYMBOL:
            contracts.append({key: contract.get(key) for key in SAFE_CONTRACT_FIELDS if key in contract})
    item["contracts"] = contracts
    return item


async def main() -> None:
    now_ms = int(time.time() * 1000)
    out: dict[str, Any] = {
        "symbol": SYMBOL,
        "ticker": TICKER,
        "available_usd": str(await _gemini_available_usd()),
        "orders": {},
        "queries": {},
        "event_matches": [],
    }
    for status in ("filled", "cancelled"):
        response = await private_rest_request(
            "POST",
            "/v1/prediction-markets/orders/history",
            params={
                "status": status,
                "symbol": SYMBOL,
                "limit": 100,
                "offset": 0,
                "from": now_ms - 3_600_000,
                "to": now_ms + 1_000,
            },
            timeout=5.0,
        )
        out["queries"][f"orders_{status}"] = {"http": response.status_code}
        if response.status_code != 200:
            continue
        body = response.json()
        rows = body.get("orders", []) if isinstance(body, Mapping) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            cid = str(row.get("clientOrderId") or "")
            if cid in CLIENT_IDS:
                out["orders"][cid] = safe_row(row)

    async with httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-reconcile/1.0"}) as http:
        candidates = [None, "settled", "closed", "completed", "expired", "active"]
        for status in candidates:
            params: dict[str, Any] = {"category": "Crypto", "limit": "500", "offset": "0"}
            if status is not None:
                params["status"] = status
            response = await http.get("https://api.gemini.com/v1/prediction-markets/events", params=params)
            key = "events_any" if status is None else f"events_{status}"
            out["queries"][key] = {"http": response.status_code}
            if response.status_code != 200:
                continue
            payload = response.json()
            rows = payload.get("data") or [] if isinstance(payload, Mapping) else []
            for row in rows:
                if isinstance(row, Mapping) and str(row.get("ticker") or "").upper() == TICKER:
                    item = safe_event(row)
                    item["queried_status"] = status
                    out["event_matches"].append(item)
    print(json.dumps(out, sort_keys=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
