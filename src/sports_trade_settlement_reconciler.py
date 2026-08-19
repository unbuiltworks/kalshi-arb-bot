"""Authoritative settlement reconciler for live Gemini/Kalshi sports probes.

Reads open BOTH_FILLED sports trades from ARB Command Center, waits until both venue
contracts expose definitive YES/NO results, computes payout from the outcomes that
were actually bought, and only then emits TRADE_REALIZED.

Fail-closed invariants:
- never realizes modeled/shadow P&L;
- requires two recorded fills and AWAITING_SETTLEMENT;
- requires exact Gemini instrumentSymbol and Kalshi ticker from execution metadata;
- requires both venue results to be definitive YES or NO;
- unknown/LFMP/ambiguous results remain open;
- realized net P&L = actual settlement payout - actual fill capital - recorded fees.

This service is read-only to Gemini/Kalshi and has no order-entry path.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("kalshi_arb.sports_trade_settlement_reconciler")
D = Decimal

COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
PUSHOVER_USER = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
GEMINI_EVENTS = "https://api.gemini.com/v1/prediction-markets/events"
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"


def _d(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def normalize_result(value: Any) -> str | None:
    result = str(value or "").strip().lower()
    return result if result in {"yes", "no"} else None


def bought_outcomes(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    gp = metadata.get("gemini_payload") or {}
    kp = metadata.get("kalshi_payload") or {}
    gemini_bought = normalize_result(gp.get("eventOutcome"))
    side = str(kp.get("side") or "").strip().lower()
    kalshi_bought = "yes" if side == "bid" else "no" if side == "ask" else None
    return gemini_bought, kalshi_bought


def settlement_payouts(
    *,
    gemini_bought: str | None,
    kalshi_bought: str | None,
    gemini_result: str | None,
    kalshi_result: str | None,
) -> tuple[D, D] | None:
    if None in {gemini_bought, kalshi_bought, gemini_result, kalshi_result}:
        return None
    if gemini_bought not in {"yes", "no"} or kalshi_bought not in {"yes", "no"}:
        return None
    if gemini_result not in {"yes", "no"} or kalshi_result not in {"yes", "no"}:
        return None
    return (
        D("1") if gemini_bought == gemini_result else D("0"),
        D("1") if kalshi_bought == kalshi_result else D("0"),
    )


def realized_pnl(*, capital_filled: Any, fees: Any, payouts: tuple[D, D]) -> tuple[D, D]:
    gross = sum(payouts, D("0")) - _d(capital_filled)
    net = gross - _d(fees)
    return gross, net


def settlement_compatible(gemini_result: str, kalshi_result: str) -> bool:
    """Same-proposition venues are compatible iff their YES/NO resolutions agree."""
    return gemini_result == kalshi_result


async def _push(title: str, message: str) -> None:
    if not PUSHOVER_USER or not PUSHOVER_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            response = await http.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": PUSHOVER_TOKEN,
                    "user": PUSHOVER_USER,
                    "title": title[:250],
                    "message": message[:1024],
                    "priority": "0",
                },
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning("SPORTS_SETTLEMENT_PUSHOVER_FAILED error=%s", type(exc).__name__)


async def _post_event(event: dict[str, Any]) -> bool:
    if not LEDGER_TOKEN:
        logger.error("SPORTS_SETTLEMENT_LEDGER_DISABLED missing=LEDGER_INGEST_TOKEN")
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            response = await http.post(
                f"{COMMAND_CENTER}/api/events",
                headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
                json=event,
            )
            response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("SPORTS_SETTLEMENT_LEDGER_POST_FAILED error=%s", type(exc).__name__)
        return False


async def fetch_open_trades(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
    response.raise_for_status()
    rows = response.json()
    return [
        row for row in rows
        if isinstance(row, dict)
        and str(row.get("mode") or "") in {"LIVE", "LIVE_PROBE"}
        and str(row.get("strategy") or "").startswith("sports_gk_")
        and not bool(row.get("realized"))
        and int(row.get("legs_filled") or 0) == 2
        and str(row.get("status") or "") == "AWAITING_SETTLEMENT"
    ]


async def find_gemini_resolution(
    http: httpx.AsyncClient,
    instrument_symbol: str,
    *,
    max_events: int = 3000,
) -> str | None:
    """Find the exact settled baseball contract and return resolutionSide."""
    offset = 0
    while offset < max_events:
        limit = min(500, max_events - offset)
        response = await http.get(
            GEMINI_EVENTS,
            params={"status": "settled", "sport": "baseball", "limit": limit, "offset": offset},
        )
        response.raise_for_status()
        payload = response.json()
        events = payload.get("data") or payload.get("events") or []
        for event in events:
            for contract in (event.get("contracts") or []):
                if str(contract.get("instrumentSymbol") or "") != instrument_symbol:
                    continue
                return normalize_result(contract.get("resolutionSide"))
        if len(events) < limit:
            return None
        offset += limit
    return None


async def find_kalshi_resolution(http: httpx.AsyncClient, ticker: str) -> str | None:
    response = await http.get(f"{KALSHI_REST}/markets/{quote(ticker, safe='')}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    market = payload.get("market") if isinstance(payload, dict) else None
    if not isinstance(market, dict):
        market = payload if isinstance(payload, dict) else {}
    return normalize_result(market.get("result"))


async def reconcile_trade(http: httpx.AsyncClient, trade: dict[str, Any]) -> bool:
    metadata = trade.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    gp = metadata.get("gemini_payload") or {}
    kp = metadata.get("kalshi_payload") or {}
    symbol = str(gp.get("symbol") or "")
    ticker = str(kp.get("ticker") or "")
    if not symbol or not ticker:
        logger.warning("SPORTS_SETTLEMENT_BLOCKED trade_id=%s reason=missing_exact_market_ids", trade.get("trade_id"))
        return False

    try:
        gemini_result, kalshi_result = await asyncio.gather(
            find_gemini_resolution(http, symbol),
            find_kalshi_resolution(http, ticker),
        )
    except Exception as exc:
        logger.warning("SPORTS_SETTLEMENT_QUERY_FAILED trade_id=%s error=%s", trade.get("trade_id"), type(exc).__name__)
        return False

    gemini_bought, kalshi_bought = bought_outcomes(metadata)
    payouts = settlement_payouts(
        gemini_bought=gemini_bought,
        kalshi_bought=kalshi_bought,
        gemini_result=gemini_result,
        kalshi_result=kalshi_result,
    )
    if payouts is None:
        logger.info(
            "SPORTS_SETTLEMENT_PENDING trade_id=%s gemini=%s kalshi=%s",
            trade.get("trade_id"), gemini_result, kalshi_result,
        )
        return False

    gross, net = realized_pnl(
        capital_filled=trade.get("capital_filled"),
        fees=trade.get("fees"),
        payouts=payouts,
    )
    compatible = settlement_compatible(str(gemini_result), str(kalshi_result))
    status = "SETTLED_COMPATIBLE" if compatible else "SETTLED_DIVERGENT"
    trade_id = str(trade.get("trade_id") or "")
    event = {
        "event_id": _safe_id(f"{trade_id}:realized"),
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": _now(),
        "strategy": str(trade.get("strategy") or "sports_gk_live_probe"),
        "mode": str(trade.get("mode") or "LIVE_PROBE"),
        "market": trade.get("market"),
        "asset": trade.get("asset"),
        "venues": ["gemini", "kalshi"],
        "side": trade.get("side"),
        "status": status,
        "quantity": "1",
        "fee": "0",
        "gross_pnl": str(gross),
        "net_pnl": str(net),
        "legs_required": 2,
        "legs_filled": 2,
        "metadata": {
            **metadata,
            "settlement": {
                "gemini_instrument_symbol": symbol,
                "kalshi_ticker": ticker,
                "gemini_bought": gemini_bought,
                "kalshi_bought": kalshi_bought,
                "gemini_result": gemini_result,
                "kalshi_result": kalshi_result,
                "gemini_payout": str(payouts[0]),
                "kalshi_payout": str(payouts[1]),
                "total_payout": str(sum(payouts, D("0"))),
                "capital_filled": str(trade.get("capital_filled") or "0"),
                "recorded_fees": str(trade.get("fees") or "0"),
                "settlement_compatible": compatible,
                "realized_from_actual_settlement": True,
            },
        },
    }
    if not await _post_event(event):
        return False

    player = str(trade.get("asset") or metadata.get("player") or "MLB prop")
    sign = "+" if net >= 0 else ""
    await _push(
        f"MLB SETTLED — {sign}${net}",
        "\n".join((
            player,
            f"Gemini: {str(gemini_result).upper()} | Kalshi: {str(kalshi_result).upper()}",
            f"Settlement: {'compatible' if compatible else 'DIVERGENT'}",
            f"Actual payout: ${sum(payouts, D('0'))}",
            f"Actual net P&L: {sign}${net}",
            "Recorded as REALIZED in ARB Command Center.",
        )),
    )
    logger.warning(
        "SPORTS_TRADE_REALIZED trade_id=%s status=%s gross=%s net=%s",
        trade_id, status, gross, net,
    )
    return True


async def run_forever(interval_seconds: float = 20.0) -> None:
    logger.info(
        "SPORTS_SETTLEMENT_RECONCILER_READY interval_seconds=%s read_only_venues=true order_entry=false",
        interval_seconds,
    )
    async with httpx.AsyncClient(timeout=15.0) as http:
        while True:
            try:
                trades = await fetch_open_trades(http)
                realized = 0
                for trade in trades:
                    realized += int(await reconcile_trade(http, trade))
                logger.info(
                    "SPORTS_SETTLEMENT_HEARTBEAT open_awaiting=%s realized_this_cycle=%s",
                    len(trades), realized,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("SPORTS_SETTLEMENT_LOOP_ERROR type=%s", type(exc).__name__)
            await asyncio.sleep(max(5.0, interval_seconds))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=20.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_forever(args.interval_seconds))


if __name__ == "__main__":
    main()
