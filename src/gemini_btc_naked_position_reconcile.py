"""Read-only reconciliation for the current BTC 15m Gemini one-leg fill."""
from __future__ import annotations

import asyncio
import json

from src.gemini_prediction_auth import private_rest_request

SYMBOL = "GEMI-BTC15M2608200300-UP"
EVENT = "BTC15M2608200300"
CLIENT_ID = "solc2-gkr-260819-1705-05-g"
FLATTEN_ID = "flatten-g-4a34b9ee38ab4214888d"


async def call(path: str, params: dict) -> tuple[int, dict]:
    r = await private_rest_request("POST", path, params=params, timeout=5.0)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}
    return r.status_code, data


async def main() -> None:
    pos_status, pos = await call("/v1/prediction-markets/positions", {"eventTicker": EVENT, "limit": 100, "offset": 0})
    settled_status, settled = await call("/v1/prediction-markets/positions/settled", {"eventTicker": EVENT, "limit": 100, "offset": 0})
    filled_status, filled = await call("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": SYMBOL, "limit": 100})
    cancelled_status, cancelled = await call("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": SYMBOL, "limit": 100})
    matches = []
    for label, data in (("filled", filled), ("cancelled", cancelled)):
        for row in data.get("orders", []) if isinstance(data, dict) else []:
            cid = str(row.get("clientOrderId") or "")
            if cid in {CLIENT_ID, FLATTEN_ID}:
                matches.append({"history": label, "row": row})
    positions = [
        row for row in (pos.get("positions", []) if isinstance(pos, dict) else [])
        if str(row.get("symbol") or row.get("instrumentSymbol") or "") == SYMBOL
    ]
    settled_positions = [
        row for row in (settled.get("positions", []) if isinstance(settled, dict) else [])
        if str(row.get("symbol") or row.get("instrumentSymbol") or "") == SYMBOL
    ]
    print("GEMINI_NAKED_POSITION_RECONCILE " + json.dumps({
        "mode": "READ_ONLY_RECONCILIATION",
        "symbol": SYMBOL,
        "event": EVENT,
        "positions_http": pos_status,
        "matching_positions": positions,
        "settled_http": settled_status,
        "matching_settled_positions": settled_positions,
        "filled_history_http": filled_status,
        "cancelled_history_http": cancelled_status,
        "matching_orders": matches,
        "order_entry": False,
    }, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
