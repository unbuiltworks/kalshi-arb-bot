"""Read-only authoritative settlement lookup for the funded weather probe."""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from typing import Any

from src.auth import KalshiClient

TICKER = os.getenv("WEATHER_RECONCILE_TICKER", "KXHIGHCHI-26AUG21-B80.5").strip()
D = Decimal


def _d(value: Any) -> D:
    try:
        return D(str(value or "0"))
    except Exception:
        return D("0")


async def main_async() -> None:
    client = KalshiClient(base_url="https://external-api.kalshi.com/trade-api/v2")
    try:
        settlement_resp, fills_resp = await asyncio.gather(
            client.get("/portfolio/settlements", params={"ticker": TICKER, "limit": 100}),
            client.get("/portfolio/fills", params={"ticker": TICKER, "limit": 100}),
        )
        settlement_resp.raise_for_status()
        fills_resp.raise_for_status()
        settlements = settlement_resp.json().get("settlements") or []
        fills = fills_resp.json().get("fills") or []
        # Never log account credentials; only the funded ticker's trade economics.
        compact_fills = []
        total_fees = D("0")
        for row in fills:
            fee = _d(row.get("fee_cost"))
            total_fees += fee
            compact_fills.append({
                "ticker": row.get("ticker") or row.get("market_ticker"),
                "action": row.get("action"),
                "side": row.get("side"),
                "count_fp": row.get("count_fp"),
                "yes_price_dollars": row.get("yes_price_dollars"),
                "no_price_dollars": row.get("no_price_dollars"),
                "fee_cost": row.get("fee_cost"),
                "created_time": row.get("created_time"),
            })
        compact_settlements = []
        for row in settlements:
            compact_settlements.append({
                "ticker": row.get("ticker"),
                "event_ticker": row.get("event_ticker"),
                "market_result": row.get("market_result"),
                "yes_count_fp": row.get("yes_count_fp"),
                "yes_total_cost_dollars": row.get("yes_total_cost_dollars"),
                "no_count_fp": row.get("no_count_fp"),
                "no_total_cost_dollars": row.get("no_total_cost_dollars"),
                "revenue": row.get("revenue"),
                "value": row.get("value"),
                "fee_cost": row.get("fee_cost"),
                "settled_time": row.get("settled_time"),
            })
        print("WEATHER_SETTLEMENT_AUTHORITATIVE " + json.dumps({
            "ticker": TICKER,
            "settlement_count": len(compact_settlements),
            "settlements": compact_settlements,
            "fills": compact_fills,
            "fill_fee_total_dollars": str(total_fees),
            "order_entry": False,
        }, separators=(",", ":"), default=str), flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main_async())
