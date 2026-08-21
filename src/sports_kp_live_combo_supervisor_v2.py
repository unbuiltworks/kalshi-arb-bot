"""Continuous generated + discovered 2-5 leg sports combo arb supervisor.

Every fast cycle it inventories live Polymarket sports and scans already-created
Kalshi MVEs.  On a slower cadence it actively manufactures bounded 2-5 leg
Kalshi combos from open multivariate collections, requests q=1 RFQs, and scores
the scalar-safe hedge basket.  A positive candidate may be handed exactly once
per process to the extended fail-closed q=1 executor, which independently
revalidates all execution inputs.

No candidate or fill is counted as realized P&L.  Pushover distinguishes
candidate, fully-hedged fill, and execution/reconciliation outcomes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from typing import Any

import httpx

from src.sports_live_poly_inventory import run_once as poly_live_inventory
from src.sports_kp_mve_same_venue_scan import main_async as scan_existing_mves
from src.sports_kp_mve_combo_generator_v2 import generate_and_score as generate_mves
from src.sports_kp_mve_same_venue_live_q1_2_5 import main_async as run_q1

logger = logging.getLogger("kalshi_arb.sports_kp_live_combo_supervisor_v2")


def _yes(name: str, default: str = "NO") -> bool:
    return os.getenv(name, default).strip().upper() == "YES"


def _interval() -> float:
    try:
        return max(10.0, float(os.getenv("SPORTS_COMBO_MONITOR_INTERVAL_SECONDS", "20")))
    except Exception:
        return 20.0


def _generator_every_cycles() -> int:
    try:
        return max(1, int(os.getenv("MVE_GENERATOR_EVERY_CYCLES", "45")))
    except Exception:
        return 45


async def _notify(title: str, message: str, *, priority: int = 0) -> bool:
    token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
    user = os.getenv("PUSHOVER_USER_KEY", "").strip()
    if not token or not user:
        logger.warning("SPORTS_COMBO_ALERT_SKIPPED missing_pushover_credentials title=%s", title)
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post("https://api.pushover.net/1/messages.json", data={
                "token": token, "user": user, "title": title[:250], "message": message[:1000], "priority": str(priority),
            })
        return 200 <= r.status_code < 300
    except Exception as exc:
        logger.warning("SPORTS_COMBO_ALERT_ERROR %s: %s", type(exc).__name__, str(exc)[:200])
        return False


def _edge(row: dict[str, Any]) -> Decimal:
    try:
        return Decimal(str(row.get("locked_profit_conservative") or "-9"))
    except Exception:
        return Decimal("-9")


def _candidate_key(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or "")


def _candidate_message(row: dict[str, Any]) -> str:
    legs = row.get("legs") or []
    source = "manufactured" if row.get("generated") else "listed"
    combo_px = ((row.get("best_quote") or {}).get("combo_yes_equivalent")) if isinstance(row.get("best_quote"), dict) else None
    payout = None
    try:
        if combo_px and Decimal(str(combo_px)) > 0:
            payout = Decimal("1") / Decimal(str(combo_px))
    except Exception:
        payout = None
    payout_line = f"\nApprox combo payout multiple: {payout:.1f}x" if payout is not None else ""
    return (
        f"{row.get('ticker')}\n"
        f"Source: {source} combo\n"
        f"Legs: {len(legs)}\n"
        f"Locked q=1 profit after conservative fees: ${row.get('locked_profit_conservative')}\n"
        f"All-in hedge cost: ${row.get('all_in_cost_conservative')}"
        f"{payout_line}\n"
        "Status: executable candidate; live q=1 runner revalidates before any order."
    )


async def _attempt_q1(row: dict[str, Any]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "").strip()
    if not ticker:
        return {"status": "NO_TRADE", "reason": "EMPTY_TICKER"}
    old_ticker = os.environ.get("KALSHI_MVE_LIVE_TICKER")
    os.environ["KALSHI_MVE_LIVE_TICKER"] = ticker
    try:
        return await run_q1()
    finally:
        if old_ticker is None:
            os.environ.pop("KALSHI_MVE_LIVE_TICKER", None)
        else:
            os.environ["KALSHI_MVE_LIVE_TICKER"] = old_ticker


async def run() -> None:
    auto_q1 = _yes("ENABLE_SPORTS_COMBO_AUTO_Q1")
    live_gate = _yes("ENABLE_KALSHI_MVE_LIVE")
    generator_enabled = _yes("ENABLE_KALSHI_MVE_GENERATOR")
    one_attempt_consumed = False
    alerted: set[str] = set()
    cycle = 0
    generated_latest: list[dict[str, Any]] = []

    start = {
        "status": "SPORTS_KP_LIVE_COMBO_SUPERVISOR_V2_START",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "continuous_monitor": True,
        "supported_scan_legs": [2, 3, 4, 5],
        "live_canary_legs": [2, 3, 4, 5],
        "automatic_combo_generation": generator_enabled,
        "auto_q1_requested": auto_q1,
        "live_gate": live_gate,
        "max_live_attempts_per_process": 1,
        "candidate_is_realized": False,
        "fill_is_realized": False,
    }
    logger.info("SPORTS_KP_LIVE_COMBO_SUPERVISOR_V2_START %s", json.dumps(start, separators=(",", ":")))
    await _notify(
        "Sports combo engine v2 online",
        f"2-5 leg generated+listed combo search started. Generator={'on' if generator_enabled else 'off'}; q=1 canary={'armed' if auto_q1 and live_gate else 'disarmed'}.",
    )

    while True:
        cycle += 1
        try:
            poly = await poly_live_inventory()
            logger.info("SPORTS_POLY_LIVE_HEARTBEAT %s", json.dumps({
                "cycle": cycle,
                "live_event_count": poly.get("live_event_count"),
                "events_with_accepting_markets": poly.get("events_with_accepting_markets"),
                "series_counts": poly.get("series_counts"),
                "order_entry": False,
            }, separators=(",", ":"), default=str))

            if generator_enabled and (cycle == 1 or cycle % _generator_every_cycles() == 0):
                generated = await generate_mves()
                generated_latest = [x for x in (generated.get("ranked") or []) if isinstance(x, dict)]
                logger.warning("SPORTS_MVE_GENERATOR_HEARTBEAT %s", json.dumps({
                    "cycle": cycle,
                    "status": generated.get("status"),
                    "collections_seen": generated.get("collections_seen"),
                    "candidate_baskets_considered": generated.get("candidate_baskets_considered"),
                    "markets_created_or_resolved": generated.get("markets_created_or_resolved"),
                    "created_today": generated.get("created_today"),
                    "daily_cap": generated.get("daily_cap"),
                    "executable_candidates": generated.get("executable_candidates"),
                    "best": generated_latest[0] if generated_latest else None,
                    "order_entry": False,
                }, separators=(",", ":"), default=str))

            existing = await scan_existing_mves()
            existing_rows = [x for x in (existing.get("ranked") or []) if isinstance(x, dict)]
            all_rows: dict[str, dict[str, Any]] = {}
            for row in generated_latest + existing_rows:
                ticker = str(row.get("ticker") or "")
                if not ticker:
                    continue
                if ticker not in all_rows or _edge(row) > _edge(all_rows[ticker]):
                    all_rows[ticker] = row
            executable = [x for x in all_rows.values() if x.get("classification") == "EXECUTABLE_CANDIDATE"]
            executable.sort(key=_edge, reverse=True)

            logger.info("SPORTS_KALSHI_MVE_HEARTBEAT %s", json.dumps({
                "cycle": cycle,
                "active_by_leg_count": existing.get("active_by_leg_count"),
                "listed_executable": existing.get("executable_candidates"),
                "generated_cached": len(generated_latest),
                "combined_executable": len(executable),
                "best": executable[0] if executable else (existing_rows[0] if existing_rows else None),
                "order_entry": False,
            }, separators=(",", ":"), default=str))

            for row in executable:
                key = _candidate_key(row)
                if key and key not in alerted:
                    alerted.add(key)
                    await _notify("Sports arb executable candidate", _candidate_message(row))

            if executable and auto_q1 and live_gate and not one_attempt_consumed:
                chosen = executable[0]
                result = await _attempt_q1(chosen)
                status = str(result.get("status") or "UNKNOWN")
                logger.warning("SPORTS_COMBO_Q1_RESULT %s", json.dumps({
                    "ticker": chosen.get("ticker"), "leg_count": len(chosen.get("legs") or []),
                    "status": status, "result": result, "realized_profit": None,
                }, separators=(",", ":"), default=str))

                if status not in {"NO_TRADE", "DISARMED"}:
                    one_attempt_consumed = True
                if status == "FULLY_HEDGED_FILLED":
                    await _notify(
                        "Sports q=1 FILLED",
                        f"{chosen.get('ticker')}\n{len(chosen.get('legs') or [])}-leg combo. Every hedge leg and combo fill confirmed. FILLED_NOT_SETTLED; realized profit not yet counted.",
                        priority=1,
                    )
                elif status not in {"NO_TRADE", "DISARMED"}:
                    await _notify(
                        "Sports q=1 execution result",
                        f"{chosen.get('ticker')}\nStatus: {status}\nFurther live attempts are blocked in this process pending reconciliation.",
                        priority=1,
                    )
        except Exception as exc:
            logger.exception("SPORTS_COMBO_MONITOR_V2_ERROR")
            await _notify("Sports combo engine error", f"{type(exc).__name__}: {str(exc)[:700]}", priority=1)

        await asyncio.sleep(_interval())


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
