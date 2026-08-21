"""Read-only reconciliation for one weather Kalshi ticker.

This module intentionally performs GET requests only. It is suitable for a
one-shot Railway job used to establish authoritative account/order state before
arming weather execution.
"""

import asyncio
import json
import os

from src.auth import KalshiClient


TICKER = os.getenv("WEATHER_RECONCILE_TICKER", "KXHIGHCHI-26AUG21-B80.5").strip()
KALSHI_PROD_REST = "https://external-api.kalshi.com/trade-api/v2"


def _pick(row: dict, keys: tuple[str, ...]) -> dict:
    return {key: row.get(key) for key in keys if key in row}


async def run() -> None:
    # Pin to the current production Trade API host used by the working live
    # crypto balance reconciler and weather live probe. The legacy settings
    # module still contains an older production hostname.
    client = KalshiClient(base_url=KALSHI_PROD_REST)
    result: dict = {"ticker": TICKER}
    try:
        requests = {
            "orders": ("/portfolio/orders", "orders"),
            "fills": ("/portfolio/fills", "fills"),
            "positions": ("/portfolio/positions", "market_positions"),
            "settlements": ("/portfolio/settlements", "settlements"),
        }
        wanted = {
            "orders": (
                "order_id", "client_order_id", "ticker", "side", "action", "status",
                "yes_price_dollars", "no_price_dollars", "fill_count_fp",
                "remaining_count_fp", "initial_count_fp", "taker_fill_cost_dollars",
                "maker_fill_cost_dollars", "taker_fees_dollars", "maker_fees_dollars",
                "created_time", "last_update_time",
            ),
            "fills": (
                "fill_id", "trade_id", "order_id", "ticker", "market_ticker", "side",
                "action", "count_fp", "yes_price_dollars", "no_price_dollars",
                "is_taker", "fee_cost", "created_time", "ts",
            ),
            "positions": (
                "ticker", "position_fp", "total_traded_dollars", "market_exposure_dollars",
                "realized_pnl_dollars", "resting_orders_count", "fees_paid_dollars",
                "last_updated_ts",
            ),
            "settlements": (
                "ticker", "event_ticker", "market_result", "yes_count_fp",
                "yes_total_cost_dollars", "no_count_fp", "no_total_cost_dollars",
                "revenue", "fee_cost", "value", "settled_time",
            ),
        }
        for name, (path, list_key) in requests.items():
            try:
                response = await client.get(path, params={"ticker": TICKER, "limit": 1000})
                body = response.json() if response.content else {}
                rows = body.get(list_key, []) if isinstance(body, dict) else []
                result[name] = {
                    "status_code": response.status_code,
                    "rows": [_pick(row, wanted[name]) for row in rows if isinstance(row, dict)],
                }
            except Exception as exc:
                result[name] = {"error": f"{type(exc).__name__}: {exc}"}

        try:
            response = await client.get("/portfolio/balance")
            body = response.json() if response.content else {}
            result["balance"] = {
                "status_code": response.status_code,
                "balance": body.get("balance") if isinstance(body, dict) else None,
                "portfolio_value": body.get("portfolio_value") if isinstance(body, dict) else None,
                "updated_ts": body.get("updated_ts") if isinstance(body, dict) else None,
            }
        except Exception as exc:
            result["balance"] = {"error": f"{type(exc).__name__}: {exc}"}

        print(json.dumps({"event": "WEATHER_RECONCILE_READONLY", "result": result}, sort_keys=True, default=str), flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
