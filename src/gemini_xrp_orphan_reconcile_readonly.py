"""Read-only Gemini prediction-order reconciliation for one orphaned client id.

Used after a process interruption to prove whether an old maker order is still active,
was cancelled zero-fill, or actually executed. This module never places or cancels an
order and never changes the Command Center ledger.
"""
from __future__ import annotations

from decimal import Decimal
import asyncio
import json
import os
import time
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request

D = Decimal
CLIENT_ID = os.getenv("GEMINI_ORPHAN_CLIENT_ID", "").strip()
SYMBOL = os.getenv("GEMINI_ORPHAN_SYMBOL", "").strip()
LOOKBACK_MS = 4 * 60 * 60 * 1000

SAFE_FIELDS = (
    "orderId", "clientOrderId", "symbol", "status", "side", "eventOutcome",
    "price", "quantity", "filledQuantity", "avgExecutionPrice", "createdAt",
    "updatedAt", "timestamp",
)


def safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in SAFE_FIELDS if key in row}


def _filled(row: Mapping[str, Any]) -> D:
    try:
        return D(str(row.get("filledQuantity") or "0"))
    except Exception:
        return D("0")


async def _rows(path: str, params: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    response = await private_rest_request("POST", path, params=params, timeout=6.0)
    if response.status_code != 200:
        return response.status_code, []
    body = response.json()
    rows = body.get("orders", []) if isinstance(body, Mapping) else []
    return response.status_code, [dict(row) for row in rows if isinstance(row, Mapping)]


async def run() -> dict[str, Any]:
    if not CLIENT_ID or not SYMBOL:
        raise RuntimeError("GEMINI_ORPHAN_CLIENT_ID and GEMINI_ORPHAN_SYMBOL are required")
    now_ms = int(time.time() * 1000)
    active_http, active = await _rows(
        "/v1/prediction-markets/orders/active",
        {"symbol": SYMBOL, "limit": 100, "offset": 0},
    )
    history: dict[str, list[dict[str, Any]]] = {}
    http_status: dict[str, int] = {"active": active_http}
    for status in ("filled", "cancelled"):
        code, rows = await _rows(
            "/v1/prediction-markets/orders/history",
            {
                "status": status,
                "symbol": SYMBOL,
                "limit": 100,
                "from": now_ms - LOOKBACK_MS,
                "to": now_ms + 1000,
            },
        )
        history[status] = rows
        http_status[status] = code

    matching_active = [row for row in active if str(row.get("clientOrderId") or "") == CLIENT_ID]
    matching_filled = [row for row in history["filled"] if str(row.get("clientOrderId") or "") == CLIENT_ID]
    matching_cancelled = [row for row in history["cancelled"] if str(row.get("clientOrderId") or "") == CLIENT_ID]

    # Deduplicate by venue order id before aggregating fills. A venue order should
    # normally appear in only one terminal history set, but fail safe if it does not.
    by_order: dict[str, dict[str, Any]] = {}
    for row in [*matching_active, *matching_filled, *matching_cancelled]:
        oid = str(row.get("orderId") or row.get("id") or "")
        key = oid or json.dumps(safe_row(row), sort_keys=True, default=str)
        previous = by_order.get(key)
        if previous is None or _filled(row) > _filled(previous):
            by_order[key] = row
    total_filled = sum((_filled(row) for row in by_order.values()), D("0"))

    out = {
        "mode": "READ_ONLY_ORPHAN_RECONCILE",
        "order_entry": False,
        "mutation": False,
        "client_order_id": CLIENT_ID,
        "symbol": SYMBOL,
        "http_status": http_status,
        "active_count": len(matching_active),
        "filled_history_count": len(matching_filled),
        "cancelled_history_count": len(matching_cancelled),
        "unique_matching_orders": len(by_order),
        "total_filled_quantity": str(total_filled),
        "safe_to_release_zero_fill_reservation": bool(not matching_active and total_filled == 0 and len(by_order) > 0),
        "orders": [safe_row(row) for row in by_order.values()],
    }
    print("GEMINI_XRP_ORPHAN_RECONCILE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run())
