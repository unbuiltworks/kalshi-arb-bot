"""Continuous fail-closed sports combo monitor with one bounded q=1 live canary.

This supervisor keeps live Polymarket-US inventory visible, repeatedly scans
Kalshi's authoritative 2-3 leg MVE definitions for the scalar-safe synthetic
coverage identity, and can hand exactly one prequalified ticker to the existing
q=1 live runner.  The live runner still performs its own fresh RFQ, depth,
fee, balance, position, FOK, quote-refresh, and flatten/hard-halt checks.

The supervisor never treats a candidate or fill as realized profit.  It sends
Pushover notifications when configured, and deduplicates candidate alerts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any

import httpx

from src.sports_live_poly_inventory import run_once as poly_live_inventory
from src.sports_kp_mve_same_venue_scan import main_async as scan_mves
from src.sports_kp_mve_same_venue_live_q1 import main_async as run_q1

logger = logging.getLogger("kalshi_arb.sports_kp_live_combo_supervisor")


def _yes(name: str, default: str = "NO") -> bool:
    return os.getenv(name, default).strip().upper() == "YES"


def _interval() -> float:
    try:
        return max(10.0, float(os.getenv("SPORTS_COMBO_MONITOR_INTERVAL_SECONDS", "20")))
    except Exception:
        return 20.0


async def _notify(title: str, message: str, *, priority: int = 0) -> bool:
    token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
    user = os.getenv("PUSHOVER_USER_KEY", "").strip()
    if not token or not user:
        logger.warning("SPORTS_COMBO_ALERT_SKIPPED missing_pushover_credentials title=%s", title)
        return False
    payload = {
        "token": token,
        "user": user,
        "title": title[:250],
        "message": message[:1000],
        "priority": str(priority),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post("https://api.pushover.net/1/messages.json", data=payload)
        ok = 200 <= r.status_code < 300
        if not ok:
            logger.warning("SPORTS_COMBO_ALERT_HTTP status=%s", r.status_code)
        return ok
    except Exception as exc:
        logger.warning("SPORTS_COMBO_ALERT_ERROR %s: %s", type(exc).__name__, str(exc)[:200])
        return False


def _candidate_key(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or "")


def _candidate_message(row: dict[str, Any]) -> str:
    legs = row.get("legs") or []
    return (
        f"{row.get('ticker')}\n"
        f"Locked q=1 profit (conservative): ${row.get('locked_profit_conservative')}\n"
        f"All-in cost: ${row.get('all_in_cost_conservative')}\n"
        f"Legs: {len(legs)}\n"
        f"Closes: {row.get('close_time')}\n"
        "Status: executable candidate; live runner will revalidate before any order."
    )


async def run() -> None:
    auto_q1 = _yes("ENABLE_SPORTS_COMBO_AUTO_Q1")
    live_gate = _yes("ENABLE_KALSHI_MVE_LIVE")
    one_attempt_consumed = False
    candidate_alerted: set[str] = set()
    cycle = 0

    start = {
        "status": "SPORTS_KP_LIVE_COMBO_SUPERVISOR_START",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "continuous_monitor": True,
        "polymarket_inventory": True,
        "kalshi_mve_scan": True,
        "auto_q1_requested": auto_q1,
        "live_gate": live_gate,
        "max_live_attempts_per_process": 1,
        "candidate_is_realized": False,
        "fill_is_realized": False,
    }
    logger.info("SPORTS_KP_LIVE_COMBO_SUPERVISOR_START %s", json.dumps(start, separators=(",", ":")))
    await _notify(
        "Sports arb monitor online",
        f"Continuous live sports monitor started. q=1 auto canary={'armed' if auto_q1 and live_gate else 'disarmed'}.",
    )

    while True:
        cycle += 1
        try:
            # Keep current Polymarket-US live breadth visible.  This remains
            # inventory-only until an exact counterpart certification exists.
            poly = await poly_live_inventory()
            logger.info("SPORTS_POLY_LIVE_HEARTBEAT %s", json.dumps({
                "cycle": cycle,
                "observed_at": poly.get("observed_at"),
                "live_event_count": poly.get("live_event_count"),
                "events_with_accepting_markets": poly.get("events_with_accepting_markets"),
                "series_counts": poly.get("series_counts"),
                "order_entry": False,
            }, separators=(",", ":"), default=str))

            scan = await scan_mves()
            ranked = [x for x in (scan.get("ranked") or []) if isinstance(x, dict)]
            executable = [x for x in ranked if x.get("classification") == "EXECUTABLE_CANDIDATE"]
            logger.info("SPORTS_KALSHI_MVE_HEARTBEAT %s", json.dumps({
                "cycle": cycle,
                "observed_at": scan.get("observed_at"),
                "active_short_duration_mves": scan.get("active_short_duration_mves"),
                "prequalified_for_rfq": scan.get("prequalified_for_rfq"),
                "rfqs_requested": scan.get("rfqs_requested"),
                "executable_candidates": len(executable),
                "best": executable[0] if executable else (ranked[0] if ranked else None),
                "order_entry": False,
            }, separators=(",", ":"), default=str))

            for row in executable:
                key = _candidate_key(row)
                if key and key not in candidate_alerted:
                    candidate_alerted.add(key)
                    await _notify("Sports arb executable candidate", _candidate_message(row))

            if executable and auto_q1 and live_gate and not one_attempt_consumed:
                chosen = executable[0]
                ticker = str(chosen.get("ticker") or "").strip()
                if ticker:
                    # The existing runner reads these gates itself and then
                    # independently rebuilds all executable inputs.  Passing a
                    # ticker from the shadow score does not bypass any live gate.
                    old_ticker = os.environ.get("KALSHI_MVE_LIVE_TICKER")
                    os.environ["KALSHI_MVE_LIVE_TICKER"] = ticker
                    try:
                        result = await run_q1()
                    finally:
                        if old_ticker is None:
                            os.environ.pop("KALSHI_MVE_LIVE_TICKER", None)
                        else:
                            os.environ["KALSHI_MVE_LIVE_TICKER"] = old_ticker

                    status = str(result.get("status") or "UNKNOWN")
                    logger.warning("SPORTS_COMBO_Q1_RESULT %s", json.dumps({
                        "ticker": ticker,
                        "status": status,
                        "result": result,
                        "realized_profit": None,
                    }, separators=(",", ":"), default=str))

                    # NO_TRADE is a pure revalidation rejection; it did not
                    # consume the single allowed mutation attempt. Any other
                    # result may have touched the account, so fail closed and
                    # refuse another attempt in this process.
                    if status not in {"NO_TRADE", "DISARMED"}:
                        one_attempt_consumed = True

                    if status == "FULLY_HEDGED_FILLED":
                        await _notify(
                            "Sports q=1 FILLED",
                            f"{ticker}\nAll legs confirmed filled and fully hedged. Classification: FILLED_NOT_SETTLED. Realized profit: not yet counted.",
                            priority=1,
                        )
                    elif status not in {"NO_TRADE", "DISARMED"}:
                        await _notify(
                            "Sports q=1 execution result",
                            f"{ticker}\nStatus: {status}\nLive attempt is now fail-closed for this process. Check reconciliation before any further live attempt.",
                            priority=1,
                        )
        except Exception as exc:
            logger.exception("SPORTS_COMBO_MONITOR_ERROR")
            await _notify(
                "Sports arb monitor error",
                f"{type(exc).__name__}: {str(exc)[:700]}",
                priority=1,
            )

        await asyncio.sleep(_interval())


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
