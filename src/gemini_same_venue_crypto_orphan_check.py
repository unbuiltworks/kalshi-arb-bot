"""Read-only authoritative Gemini reconciliation for one same-venue maker client ID."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request

CLIENT_ID = os.getenv("GEMINI_SAME_VENUE_ORPHAN_CLIENT_ID", "").strip()
SYMBOL = os.getenv("GEMINI_SAME_VENUE_ORPHAN_SYMBOL", "").strip()


async def run() -> dict[str, Any]:
    if not CLIENT_ID or not SYMBOL:
        raise RuntimeError("orphan client id and symbol required")
    now_ms = int(time.time() * 1000)
    found: list[dict[str, Any]] = []
    queries = [
        ("/v1/prediction-markets/orders/active", {"symbol": SYMBOL, "limit": 100, "offset": 0}, "active"),
        ("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": SYMBOL, "limit": 100, "from": now_ms-4*3600*1000, "to": now_ms+1000}, "filled"),
        ("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": SYMBOL, "limit": 100, "from": now_ms-4*3600*1000, "to": now_ms+1000}, "cancelled"),
    ]
    for path, params, source in queries:
        r = await private_rest_request("POST", path, params=params, timeout=6.0)
        row: dict[str, Any] = {"source": source, "http": r.status_code}
        if r.status_code == 200:
            body = r.json()
            for order in body.get("orders", []) if isinstance(body, Mapping) else []:
                if not isinstance(order, Mapping) or str(order.get("clientOrderId") or "") != CLIENT_ID:
                    continue
                found.append({
                    "source": source,
                    "orderId": order.get("orderId"),
                    "clientOrderId": order.get("clientOrderId"),
                    "status": order.get("status"),
                    "quantity": order.get("quantity"),
                    "filledQuantity": order.get("filledQuantity"),
                    "remainingQuantity": order.get("remainingQuantity"),
                    "avgExecutionPrice": order.get("avgExecutionPrice"),
                    "price": order.get("price"),
                    "eventOutcome": order.get("eventOutcome"),
                    "side": order.get("side"),
                })
        else:
            found.append(row)
    out = {
        "client_id": CLIENT_ID,
        "symbol": SYMBOL,
        "matches": found,
        "active_matches": sum(1 for x in found if x.get("source") == "active" and x.get("orderId")),
        "filled_quantity_total": str(sum(float(x.get("filledQuantity") or 0) for x in found if x.get("orderId"))),
        "read_only": True,
        "order_entry": False,
    }
    print("GEMINI_SAME_VENUE_ORPHAN_CHECK " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run())
