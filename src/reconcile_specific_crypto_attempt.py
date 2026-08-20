"""Read-only reconciliation for one ambiguous Gemini/Kalshi crypto attempt.

No order-entry methods are imported or called. The script searches exact deterministic
client order IDs on both venues and prints only sanitized status/fill evidence.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import time

from src.auth import KalshiClient
from src.gemini_prediction_auth import private_rest_request

GEMINI_CLIENT_ID = os.getenv("RECON_GEMINI_CLIENT_ID", "").strip()
KALSHI_CLIENT_ID = os.getenv("RECON_KALSHI_CLIENT_ID", "").strip()
GEMINI_SYMBOL = os.getenv("RECON_GEMINI_SYMBOL", "").strip()
KALSHI_TICKER = os.getenv("RECON_KALSHI_TICKER", "").strip()
KALSHI_PROD_REST = "https://external-api.kalshi.com/trade-api/v2"


def _gemini_sanitized(row: dict) -> dict:
    return {
        "clientOrderId": row.get("clientOrderId"),
        "status": row.get("status"),
        "filledQuantity": row.get("filledQuantity"),
        "avgExecutionPrice": row.get("avgExecutionPrice"),
        "eventOutcome": row.get("eventOutcome"),
        "symbol": row.get("symbol"),
    }


def _kalshi_sanitized(row: dict) -> dict:
    return {
        "client_order_id": row.get("client_order_id"),
        "status": row.get("status"),
        "fill_count_fp": row.get("fill_count_fp"),
        "taker_fill_cost_dollars": row.get("taker_fill_cost_dollars"),
        "taker_fees_dollars": row.get("taker_fees_dollars"),
        "ticker": row.get("ticker"),
    }


async def main() -> None:
    required = [GEMINI_CLIENT_ID, KALSHI_CLIENT_ID, GEMINI_SYMBOL, KALSHI_TICKER]
    if not all(required):
        raise RuntimeError("all RECON_* identifiers are required")

    now_ms = int(time.time() * 1000)
    gemini_matches: list[dict] = []
    queries = [
        ("active", "/v1/prediction-markets/orders/active", {"symbol": GEMINI_SYMBOL, "limit": 100, "offset": 0}),
        ("filled", "/v1/prediction-markets/orders/history", {"status": "filled", "symbol": GEMINI_SYMBOL, "limit": 100, "from": now_ms - 24 * 3600 * 1000, "to": now_ms + 1000}),
        ("cancelled", "/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": GEMINI_SYMBOL, "limit": 100, "from": now_ms - 24 * 3600 * 1000, "to": now_ms + 1000}),
    ]
    gemini_http = []
    for label, path, params in queries:
        response = await private_rest_request("POST", path, params=params, timeout=10.0)
        gemini_http.append({"query": label, "status_code": response.status_code})
        if response.status_code != 200:
            continue
        for row in response.json().get("orders", []):
            if str(row.get("clientOrderId") or "") == GEMINI_CLIENT_ID:
                gemini_matches.append(_gemini_sanitized(row))

    kalshi = KalshiClient(base_url=KALSHI_PROD_REST)
    try:
        response = await kalshi.get("/portfolio/orders", params={"ticker": KALSHI_TICKER, "limit": 100})
        kalshi_matches = []
        if response.status_code == 200:
            for row in response.json().get("orders", []):
                if str(row.get("client_order_id") or "") == KALSHI_CLIENT_ID:
                    kalshi_matches.append(_kalshi_sanitized(row))
        output = {
            "mode": "READ_ONLY_RECONCILIATION",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "gemini_client_id": GEMINI_CLIENT_ID,
            "gemini_http": gemini_http,
            "gemini_found": bool(gemini_matches),
            "gemini_matches": gemini_matches,
            "kalshi_client_id": KALSHI_CLIENT_ID,
            "kalshi_http_status": response.status_code,
            "kalshi_found": bool(kalshi_matches),
            "kalshi_matches": kalshi_matches,
            "order_entry": False,
        }
        print("CRYPTO_ATTEMPT_RECONCILIATION " + json.dumps(output, separators=(",", ":")), flush=True)
    finally:
        await kalshi.close()


if __name__ == "__main__":
    asyncio.run(main())
