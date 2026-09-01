"""Read-only sanitized dump of active Gemini short-crypto orders."""
from __future__ import annotations

import asyncio
import json

from src import gemini_short_crypto_local_budget as local


SAFE_KEYS = (
    "symbol",
    "clientOrderId",
    "orderId",
    "side",
    "eventOutcome",
    "type",
    "timeInForce",
    "price",
    "quantity",
    "executedQuantity",
    "filledQuantity",
    "remainingQuantity",
    "status",
    "createdAt",
    "updatedAt",
)


async def run() -> None:
    body = await local._post("/v1/prediction-markets/orders/active", {"limit": 100, "offset": 0})
    rows = body.get("orders") if isinstance(body.get("orders"), list) else []
    out = []
    for row in rows:
        if not isinstance(row, dict) or not local._short_symbol(row.get("symbol")):
            continue
        out.append({key: row.get(key) for key in SAFE_KEYS if key in row})
    print(
        "GEMINI_SHORT_CRYPTO_ACTIVE_ORDERS_READONLY="
        + json.dumps({"count": len(out), "orders": out, "read_only": True, "order_entry": False}, separators=(",", ":"), default=str),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(run())
