"""Read-only reconciliation for the single authorized ETH5 V3 q=1 probe."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request

SYMBOL = "GEMI-ETH05M2608290140-UP"
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


def safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in SAFE_FIELDS if key in row}


async def main() -> None:
    now_ms = int(time.time() * 1000)
    out: dict[str, Any] = {"symbol": SYMBOL, "orders": {}, "queries": {}}
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
        out["queries"][status] = {"http": response.status_code}
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
    print(json.dumps(out, sort_keys=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
