"""Continuous exact FanDuel <-> Polymarket preview monitor with explicit Pushover alerts.

Alerts are deliberately labeled LIVE RULE-RISK, never guaranteed arbitrage. They
contain exact manual FanDuel instructions, the Polymarket side/price ceiling, and
an expiry message when the signal disappears. No order entry or capital mutation.
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
from src.sports_pushover_alerts import SportsPushoverAlerts

D = Decimal
ONE = D("1")
ET = ZoneInfo("America/New_York")
_ALERTS = SportsPushoverAlerts()


def _d(value: Any) -> Decimal:
    try:
        return D(str(value))
    except Exception:
        return D("0")


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _remaining(preview: Mapping[str, Any]) -> int | None:
    quota = preview.get("quota") if isinstance(preview.get("quota"), Mapping) else {}
    return _int(quota.get("remaining"))


def _active_mlb_hours() -> bool:
    hour = datetime.now(ET).hour
    return hour >= 17 or hour < 3


def _trade_key(trade: Mapping[str, Any]) -> str:
    poly = trade.get("polymarket") if isinstance(trade.get("polymarket"), Mapping) else {}
    fd = trade.get("fanduel") if isinstance(trade.get("fanduel"), Mapping) else {}
    return "|".join((str(poly.get("slug") or ""), str(poly.get("buy_side") or ""), str(fd.get("selection") or "")))


def _fmt_cents(value: Any) -> str:
    return f"{(_d(value) * D('100')):.1f}¢"


def _alert_text(trade: Mapping[str, Any]) -> tuple[str, str]:
    poly = trade.get("polymarket") if isinstance(trade.get("polymarket"), Mapping) else {}
    fd = trade.get("fanduel") if isinstance(trade.get("fanduel"), Mapping) else {}
    profit = _d(trade.get("modeled_normal_rules_profit_per_1usd_payout"))
    total = _d(trade.get("modeled_normal_rules_total_capital"))
    poly_price = _d(poly.get("max_observed_raw_price"))
    fd_stake = _d(fd.get("stake_for_1usd_gross_payout"))
    fd_odds = _d(fd.get("observed_decimal_odds"))
    title = f"LIVE RULE-RISK +{profit * D('100'):.1f}¢ q1"
    message = "\n".join((
        "NOT GUARANTEED ARB — MLB settlement rules differ.",
        "",
        f"1) Polymarket: BUY {poly.get('buy_side')} {poly.get('slug')} @ <= {poly_price * D('100'):.1f}¢ raw",
        f"2) FanDuel: {fd.get('selection')} @ >= {fd_odds:.3f} decimal; stake ~${fd_stake:.3f}",
        "",
        f"Modeled normal-rules capital: ${total:.3f} -> $1.000 gross payout",
        f"Modeled normal-rules profit: +${profit:.3f} ({profit * D('100'):.1f}¢)",
        f"Observed quote skew: {trade.get('quote_skew_ms')}ms",
        "RECHECK BOTH PRICES BEFORE PLACING. No capital is reserved.",
        f"Rule risk: {fd.get('rule_risk')}",
    ))
    return title[:250], message[:1024]


async def _send_live(trade: Mapping[str, Any]) -> bool:
    if not _ALERTS.configured:
        return False
    title, message = _alert_text(trade)
    request_id = await _ALERTS.send(title=title, message=message)
    print("FANDUEL_POLY_PUSHOVER_SENT " + json.dumps({
        "request_id": request_id,
        "trade_key": _trade_key(trade),
        "profit": str(trade.get("modeled_normal_rules_profit_per_1usd_payout") or ""),
        "order_entry": False,
    }, separators=(",", ":")), flush=True)
    return True


async def _send_gone(key: str) -> bool:
    if not _ALERTS.configured:
        return False
    request_id = await _ALERTS.send(
        title="RULE-RISK signal gone",
        message=f"Do not place the prior FanDuel/Polymarket signal without a fresh alert.\n{key}",
    )
    print("FANDUEL_POLY_PUSHOVER_GONE " + json.dumps({"request_id": request_id, "trade_key": key}, separators=(",", ":")), flush=True)
    return True


async def supervise() -> None:
    active_interval = max(10.0, float(os.getenv("FD_POLY_ACTIVE_INTERVAL_SECONDS", "30")))
    idle_interval = max(60.0, float(os.getenv("FD_POLY_IDLE_INTERVAL_SECONDS", "300")))
    quota_floor = max(0, int(os.getenv("FD_POLY_ODDS_QUOTA_FLOOR", "5000")))
    realert_abs = max(D("0"), _d(os.getenv("FD_POLY_REALERT_PROFIT_ABS", "0.01")))
    prior: dict[str, Decimal] = {}
    scan = 0

    while True:
        started = time.monotonic()
        try:
            preview = await run_exact_preview()
            trades = actionable_rows(preview)
            scan += 1
            current: dict[str, Decimal] = {}
            sent = 0
            alert_errors = 0
            for trade in trades:
                key = _trade_key(trade)
                profit = _d(trade.get("modeled_normal_rules_profit_per_1usd_payout"))
                current[key] = profit
                old = prior.get(key)
                if old is None or abs(profit - old) >= realert_abs:
                    try:
                        if await _send_live(trade):
                            sent += 1
                    except Exception as exc:
                        alert_errors += 1
                        print("FANDUEL_POLY_PUSHOVER_ERROR " + json.dumps({"error_type": type(exc).__name__, "error": str(exc)[:240]}, separators=(",", ":")), flush=True)

            for key in set(prior) - set(current):
                try:
                    await _send_gone(key)
                except Exception as exc:
                    alert_errors += 1
                    print("FANDUEL_POLY_PUSHOVER_ERROR " + json.dumps({"error_type": type(exc).__name__, "error": str(exc)[:240]}, separators=(",", ":")), flush=True)
            prior = current

            print("FANDUEL_POLY_PUSHOVER_MONITOR " + json.dumps({
                "scan": scan,
                "actual_trade_count": len(trades),
                "alerts_sent": sent,
                "alert_errors": alert_errors,
                "pushover_configured": _ALERTS.configured,
                "active_mlb_hours": _active_mlb_hours(),
                "quota": preview.get("quota"),
                "rule_certified": False,
                "guaranteed_arbitrage": False,
                "fanduel_transport": "MANUAL_ONLY",
                "capital_reserved": False,
                "order_entry": False,
                "realized_pnl_usd": "0",
            }, separators=(",", ":"), default=str), flush=True)

            remaining = _remaining(preview)
            if remaining is not None and remaining <= quota_floor:
                print("FANDUEL_POLY_ODDS_QUOTA_GUARD " + json.dumps({
                    "remaining": remaining,
                    "floor": quota_floor,
                    "polling_paused": True,
                    "order_entry": False,
                }, separators=(",", ":")), flush=True)
                while True:
                    await asyncio.sleep(3600.0)
        except Exception as exc:
            print("FANDUEL_POLY_PUSHOVER_MONITOR_ERROR " + json.dumps({
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
