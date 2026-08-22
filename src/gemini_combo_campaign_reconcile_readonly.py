"""Read-only reconciliation for the monitored same-Gemini sports combo canary.

Authenticates only to read Gemini prediction-market active orders, recent order history,
and positions for the exact combo/hedge symbols. It has no order-entry imports or calls.
Use this to certify a campaign is flat before any redeploy/re-arm.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request

COMBO = "GEMI-CMB-260823-8EC8FBCF7988E34C-CMB"
HEDGES = (
    "GEMI-MLS-2608222330-TOR-MIA-REGT-O0",
    "GEMI-MLS-2608230030-CLB-NSH-REGS-CLB2",
)
SYMBOLS = {COMBO, *HEDGES}


def _rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"{key} response must be an object")
    raw = payload.get(key, [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuntimeError(f"{key} field must be a list")
    return [dict(row) for row in raw if isinstance(row, Mapping)]


def _symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol") or row.get("instrumentSymbol") or "")


def _client_id(row: Mapping[str, Any]) -> str:
    return str(row.get("clientOrderId") or row.get("client_order_id") or "")


def _filter(rows: list[dict[str, Any]], campaign: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if _symbol(row) in SYMBOLS
        and (not campaign or _client_id(row).startswith(campaign[:32]))
    ]


async def _post(path: str, params: dict[str, Any]) -> Any:
    response = await private_rest_request("POST", path, params=params, timeout=8.0)
    response.raise_for_status()
    return response.json()


async def main_async() -> None:
    campaign = os.getenv("GEMINI_COMBO_CAMPAIGN_ID", "gcombo-2608221328-torclb-rest1").strip()
    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(hours=24)).timestamp() * 1000)
    end_ms = int((now + timedelta(minutes=1)).timestamp() * 1000)

    active_payload, positions_payload, filled_payload, cancelled_payload = await asyncio.gather(
        _post("/v1/prediction-markets/orders/active", {"limit": 100, "offset": 0}),
        _post("/v1/prediction-markets/positions", {}),
        _post(
            "/v1/prediction-markets/orders/history",
            {"status": "filled", "limit": 100, "from": start_ms, "to": end_ms},
        ),
        _post(
            "/v1/prediction-markets/orders/history",
            {"status": "cancelled", "limit": 100, "from": start_ms, "to": end_ms},
        ),
    )

    active = _filter(_rows(active_payload, "orders"), campaign)
    positions = [row for row in _rows(positions_payload, "positions") if _symbol(row) in SYMBOLS]
    filled = _filter(_rows(filled_payload, "orders"), campaign)
    cancelled = _filter(_rows(cancelled_payload, "orders"), campaign)

    flat = not active and not positions
    unresolved = bool(active or positions)
    output = {
        "campaign": campaign,
        "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "symbols": sorted(SYMBOLS),
        "active_orders": active,
        "positions": positions,
        "filled_history": filled,
        "cancelled_history": cancelled,
        "flat_now": flat,
        "unresolved_exposure": unresolved,
        "read_only": True,
        "order_entry": False,
        "safe_to_consider_new_campaign": bool(flat),
    }
    print("GEMINI_COMBO_CAMPAIGN_RECONCILE " + json.dumps(output, separators=(",", ":"), default=str), flush=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
