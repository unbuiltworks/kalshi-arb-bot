"""Read-only KXHIGH settlement audit for the directional weather scorecard.

Fetches the member settlement track from 2026-03-01 onward, filters to KXHIGH
markets before logging, and computes net settlement P&L from payout, cost basis and
settlement fee fields. This audits account weather activity; if manual KXHIGH trades
exist, strategy attribution must be checked separately before calling every row a
bot trade.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any

from src.auth import KalshiClient

D = Decimal
START_TS = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp())
PREFIX = "KXHIGH"


def _d(value: Any) -> D:
    try:
        return D(str(value or "0"))
    except Exception:
        return D("0")


def _row_pnl(row: dict[str, Any]) -> D:
    result = str(row.get("market_result") or "").lower()
    yes = _d(row.get("yes_count_fp"))
    no = _d(row.get("no_count_fp"))
    cost = _d(row.get("yes_total_cost_dollars")) + _d(row.get("no_total_cost_dollars"))
    fee = _d(row.get("fee_cost"))
    if result == "yes":
        payout = yes
    elif result == "no":
        payout = no
    else:
        # Void/scalar rows are not inferred from binary counts. Use the API revenue
        # field as cents only for audit display and exclude from binary-net total.
        return D("NaN")
    return payout - cost - fee


async def main_async() -> None:
    client = KalshiClient(base_url="https://external-api.kalshi.com/trade-api/v2")
    cursor = ""
    pages = 0
    seen = 0
    weather: list[dict[str, Any]] = []
    try:
        while True:
            params: dict[str, Any] = {"limit": 1000, "min_ts": START_TS}
            if cursor:
                params["cursor"] = cursor
            response = await client.get("/portfolio/settlements", params=params)
            response.raise_for_status()
            body = response.json()
            pages += 1
            rows = body.get("settlements") or []
            seen += len(rows)
            for raw in rows:
                ticker = str(raw.get("ticker") or "")
                if not ticker.startswith(PREFIX):
                    continue
                pnl = _row_pnl(raw)
                weather.append({
                    "ticker": ticker,
                    "event_ticker": raw.get("event_ticker"),
                    "market_result": raw.get("market_result"),
                    "yes_count_fp": raw.get("yes_count_fp"),
                    "yes_total_cost_dollars": raw.get("yes_total_cost_dollars"),
                    "no_count_fp": raw.get("no_count_fp"),
                    "no_total_cost_dollars": raw.get("no_total_cost_dollars"),
                    "fee_cost": raw.get("fee_cost"),
                    "settled_time": raw.get("settled_time"),
                    "net_pnl_dollars": None if pnl.is_nan() else str(pnl),
                    "nonbinary_revenue_raw": raw.get("revenue") if pnl.is_nan() else None,
                })
            cursor = str(body.get("cursor") or "")
            if not cursor:
                break
            if pages >= 100:
                raise RuntimeError("settlement pagination safety cap reached")
        binary = [D(str(r["net_pnl_dollars"])) for r in weather if r["net_pnl_dollars"] is not None]
        total = sum(binary, D("0"))
        settled_times = [str(r.get("settled_time") or "") for r in weather if r.get("settled_time")]
        print("WEATHER_SETTLEMENT_HISTORY_AUDIT " + json.dumps({
            "scope": "ACCOUNT_KXHIGH_SETTLEMENTS_FROM_2026-03-01_NOT_YET_STRATEGY_ATTRIBUTED",
            "pages": pages,
            "all_account_settlements_scanned": seen,
            "weather_settlements": len(weather),
            "binary_weather_settlements": len(binary),
            "net_binary_weather_pnl_dollars": str(total),
            "first_weather_settlement": min(settled_times) if settled_times else None,
            "last_weather_settlement": max(settled_times) if settled_times else None,
            "rows": weather,
            "strategy_attribution_caveat": "manual KXHIGH activity, if any, must be separated before calling all rows bot-realized",
            "order_entry": False,
        }, separators=(",", ":"), default=str), flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main_async())
