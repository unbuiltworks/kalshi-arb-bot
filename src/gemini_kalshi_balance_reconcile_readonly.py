"""Read-only Gemini/Kalshi USD balance reconciliation.

No order transport is called. This module only authenticates and reads the two venue
USD balances so a completed live canary can be reconciled against a known pre-trade
snapshot without exposing credentials.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json

from src.gemini_kalshi_probe_transport import KalshiV2Executor
from src.gemini_prediction_auth import private_rest_request

D = Decimal


async def main_async() -> None:
    gemini_resp = await private_rest_request("POST", "/v1/balances", timeout=5.0)
    gemini_resp.raise_for_status()
    gemini_rows = gemini_resp.json()
    gemini_usd = D("0")
    for row in gemini_rows if isinstance(gemini_rows, list) else []:
        if str(row.get("currency") or "").upper() == "USD":
            gemini_usd = D(str(row.get("available") or "0"))
            break

    kalshi = KalshiV2Executor()
    try:
        kalshi_resp = await kalshi.client.get("/portfolio/balance")
        kalshi_resp.raise_for_status()
        kalshi_usd = D(str(kalshi_resp.json().get("balance") or "0")) / D("100")
    finally:
        await kalshi.close()

    print("CRYPTO_LIVE_BALANCE_RECONCILE " + json.dumps({
        "gemini_usd_available": str(gemini_usd),
        "kalshi_usd_available": str(kalshi_usd),
        "combined_usd_available": str(gemini_usd + kalshi_usd),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    asyncio.run(main_async())
