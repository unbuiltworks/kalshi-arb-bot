"""Continuous preview-only FanDuel <-> Polymarket MLB actionable monitor.

Runs the exact authenticated verifier frequently during active MLB hours and
backs off outside that window. The Odds API remaining-credit header is treated as
a hard resource guard; polling pauses before the configured floor. FanDuel stays
manual-only and this monitor contains no order-creation path.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.fanduel_poly_actionable_preview import actionable_rows
from src.fanduel_poly_exact_preview import main_async as run_exact_preview

ET = ZoneInfo("America/New_York")
D = Decimal


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _remaining(preview: Mapping[str, Any]) -> int | None:
    quota = preview.get("quota") if isinstance(preview.get("quota"), Mapping) else {}
    return _int(quota.get("remaining"))


def _active_mlb_hours() -> bool:
    # Most MLB full-game liquidity worth chasing occurs from late afternoon
    # through the final West Coast games. 03:00 ET is treated as the prior
    # evening's active window.
    hour = datetime.now(ET).hour
    return hour >= 17 or hour < 3


def _actionable_result(preview: Mapping[str, Any]) -> dict[str, Any]:
    trades = actionable_rows(preview)
    return {
        "status": "FANDUEL_POLY_ACTIONABLE_MONITOR",
        "actual_trade_count": len(trades),
        "trades": trades,
        "rule_certified": False,
        "guaranteed_arbitrage": False,
        "fanduel_transport": "MANUAL_ONLY",
        "capital_reserved": False,
        "order_entry": False,
        "realized_pnl_usd": "0",
        "quota": preview.get("quota"),
    }


async def supervise() -> None:
    active_interval = max(10.0, float(os.getenv("FD_POLY_ACTIVE_INTERVAL_SECONDS", "30")))
    idle_interval = max(60.0, float(os.getenv("FD_POLY_IDLE_INTERVAL_SECONDS", "300")))
    quota_floor = max(0, int(os.getenv("FD_POLY_ODDS_QUOTA_FLOOR", "5000")))
    scan = 0

    while True:
        started = time.monotonic()
        try:
            preview = await run_exact_preview()
            result = _actionable_result(preview)
            scan += 1
            print("FANDUEL_POLY_ACTIONABLE_MONITOR " + json.dumps({
                **result,
                "scan": scan,
                "active_mlb_hours": _active_mlb_hours(),
            }, separators=(",", ":"), default=str), flush=True)

            remaining = _remaining(preview)
            if remaining is not None and remaining <= quota_floor:
                print("FANDUEL_POLY_ODDS_QUOTA_GUARD " + json.dumps({
                    "remaining": remaining,
                    "floor": quota_floor,
                    "polling_paused": True,
                    "order_entry": False,
                }, separators=(",", ":")), flush=True)
                # Preserve the service and its state without burning more credits.
                while True:
                    await asyncio.sleep(3600.0)
        except Exception as exc:
            print("FANDUEL_POLY_ACTIONABLE_MONITOR_ERROR " + json.dumps({
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
                "order_entry": False,
            }, separators=(",", ":")), flush=True)

        interval = active_interval if _active_mlb_hours() else idle_interval
        await asyncio.sleep(max(1.0, interval - (time.monotonic() - started)))


def main() -> None:
    asyncio.run(supervise())


if __name__ == "__main__":
    main()
