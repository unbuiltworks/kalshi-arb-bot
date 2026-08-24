"""Authoritative settlement reconciler for same-Polymarket structural q1 locks.

Reads AWAITING_SETTLEMENT structural trades from ARB Command Center, obtains the
exchange's official YES settlement price for both exact market slugs, calculates
the payout of the two outcomes actually bought, and only then emits TRADE_REALIZED.

This module has no order-entry or Polymarket credential path. Settlement data is
read from the public Polymarket US settlement endpoint and all unresolved or
non-binary values fail closed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
import os
from typing import Any, Mapping
from urllib.parse import quote

import httpx

logger = logging.getLogger("kalshi_arb.sports_poly_structural_settlement_reconciler")
D = Decimal
ONE = D("1")
COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
PUSHOVER_USER = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
POLY_GATEWAY = "https://gateway.polymarket.us"
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def _d(value: Any, default: str = "0") -> D:
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        return D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return D(default)


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
        logger.warning("SPORTS_POLY_SETTLEMENT_PUSHOVER_FAILED error=%s", type(exc).__name__)


async def _post_event(event: dict[str, Any]) -> bool:
    if not LEDGER_TOKEN:
        logger.error("SPORTS_POLY_SETTLEMENT_LEDGER_DISABLED missing=LEDGER_INGEST_TOKEN")
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
        logger.warning("SPORTS_POLY_SETTLEMENT_LEDGER_POST_FAILED error=%s", type(exc).__name__)
        return False


async def fetch_open_trades(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
    response.raise_for_status()
    rows = response.json()
    return [
        row for row in rows
        if isinstance(row, dict)
        and str(row.get("mode") or "") in {"LIVE", "LIVE_PROBE"}
        and str(row.get("strategy") or "") == "sports_poly_structural_q1"
        and not bool(row.get("realized"))
        and int(row.get("legs_filled") or 0) == 2
        and str(row.get("status") or "") == "AWAITING_SETTLEMENT"
    ]


def _metadata(trade: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = trade.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def exact_slugs(trade: Mapping[str, Any]) -> tuple[str, str]:
    metadata = _metadata(trade)
    return str(metadata.get("low_slug") or ""), str(metadata.get("high_slug") or "")


def _valid_structural_metadata(trade: Mapping[str, Any]) -> bool:
    metadata = _metadata(trade)
    low_slug, high_slug = exact_slugs(trade)
    maker_slug = str(metadata.get("maker_slug") or "")
    hedge_slug = str(metadata.get("hedge_slug") or "")
    maker_side = str(metadata.get("maker_side") or "").upper()
    hedge_side = str(metadata.get("hedge_side") or "").upper()
    try:
        low_threshold = D(str(metadata.get("low_threshold")))
        high_threshold = D(str(metadata.get("high_threshold")))
    except Exception:
        return False
    return bool(
        metadata.get("classification") == "LIVE_SAME_VENUE_STRUCTURAL_LOCK"
        and str(metadata.get("family") or "").startswith("TOTAL::")
        and str(metadata.get("campaign_status") or "") == "BOTH_FILLED_RECONCILED"
        and low_slug and high_slug and low_slug != high_slug
        and {maker_slug, hedge_slug} == {low_slug, high_slug}
        and {maker_side, hedge_side} == {"YES", "NO"}
        and low_threshold < high_threshold
    )


async def _get_settlement(http: httpx.AsyncClient, slug: str) -> D | None:
    url = f"{POLY_GATEWAY}/v1/markets/{quote(slug, safe='')}/settlement"
    response: httpx.Response | None = None
    for attempt in range(4):
        response = await http.get(url)
        if response.status_code in {404, 409, 422}:
            return None
        if response.status_code not in RETRYABLE_STATUS:
            break
        if attempt + 1 >= 4:
            break
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else min(4.0, 0.5 * (2 ** attempt))
        except (TypeError, ValueError):
            delay = min(4.0, 0.5 * (2 ** attempt))
        await asyncio.sleep(delay)
    if response is None or response.status_code >= 400:
        if response is not None and response.status_code not in {404, 409, 422}:
            logger.warning("SPORTS_POLY_SETTLEMENT_HTTP_ERROR slug=%s status=%s", slug, response.status_code)
        return None
    payload = response.json()
    value = _d(payload.get("settlement"), "-1") if isinstance(payload, Mapping) else D("-1")
    return value if value in {D("0"), D("1")} else None


async def fetch_settlements(http: httpx.AsyncClient, slugs: set[str]) -> dict[str, D | None]:
    # Keep settlement traffic deliberately low; events resolve much slower than
    # execution books change and this reconciler never affects trading latency.
    results: dict[str, D | None] = {}
    for index, slug in enumerate(sorted(slugs)):
        results[slug] = await _get_settlement(http, slug)
        if index + 1 < len(slugs):
            await asyncio.sleep(0.08)
    return results


def _leg_payout(side: str, yes_settlement: D) -> D | None:
    side = str(side or "").upper()
    if yes_settlement not in {D("0"), D("1")}:
        return None
    if side == "YES":
        return yes_settlement
    if side == "NO":
        return ONE - yes_settlement
    return None


async def reconcile_trade(trade: dict[str, Any], settlements: Mapping[str, D | None]) -> bool:
    if not _valid_structural_metadata(trade):
        logger.error("SPORTS_POLY_SETTLEMENT_BLOCKED trade_id=%s reason=invalid_structural_metadata", trade.get("trade_id"))
        return False
    metadata = _metadata(trade)
    low_slug, high_slug = exact_slugs(trade)
    low_settlement = settlements.get(low_slug)
    high_settlement = settlements.get(high_slug)
    if low_settlement not in {D("0"), D("1")} or high_settlement not in {D("0"), D("1")}:
        logger.info("SPORTS_POLY_SETTLEMENT_PENDING trade_id=%s low=%s high=%s", trade.get("trade_id"), low_settlement, high_settlement)
        return False

    maker_slug = str(metadata.get("maker_slug"))
    hedge_slug = str(metadata.get("hedge_slug"))
    by_slug = {low_slug: low_settlement, high_slug: high_settlement}
    maker_payout = _leg_payout(str(metadata.get("maker_side")), by_slug[maker_slug])
    hedge_payout = _leg_payout(str(metadata.get("hedge_side")), by_slug[hedge_slug])
    if maker_payout is None or hedge_payout is None:
        return False

    total_payout = maker_payout + hedge_payout
    capital = _d(trade.get("capital_filled"))
    fees = _d(trade.get("fees"))
    gross = total_payout - capital
    net = gross - fees
    monotone_compatible = low_settlement >= high_settlement
    status = "SETTLED_STRUCTURAL_COMPATIBLE" if monotone_compatible else "SETTLED_STRUCTURAL_DIVERGENT"
    trade_id = str(trade.get("trade_id") or "")
    event = {
        "event_id": _safe_id(f"{trade_id}:realized"),
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": _now(),
        "strategy": "sports_poly_structural_q1",
        "mode": str(trade.get("mode") or "LIVE_PROBE"),
        "market": trade.get("market"),
        "asset": trade.get("asset"),
        "venues": ["polymarket"],
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
                "low_slug": low_slug,
                "high_slug": high_slug,
                "low_yes_settlement": str(low_settlement),
                "high_yes_settlement": str(high_settlement),
                "maker_payout": str(maker_payout),
                "hedge_payout": str(hedge_payout),
                "total_payout": str(total_payout),
                "capital_filled": str(capital),
                "recorded_fees": str(fees),
                "monotone_settlement_compatible": monotone_compatible,
                "realized_from_actual_settlement": True,
            },
        },
    }
    if not await _post_event(event):
        return False

    sign = "+" if net >= 0 else ""
    await _push(
        f"STRUCTURAL SETTLED — {sign}${net}",
        "\n".join((
            str(metadata.get("family") or "Polymarket structural total"),
            f"Official settlements: low={low_settlement} high={high_settlement}",
            f"Actual payout: ${total_payout}",
            f"Actual net P&L: {sign}${net}",
            f"Settlement structure: {'compatible' if monotone_compatible else 'DIVERGENT'}",
            "Recorded as REALIZED in ARB Command Center.",
        )),
    )
    logger.warning("SPORTS_POLY_TRADE_REALIZED trade_id=%s status=%s gross=%s net=%s", trade_id, status, gross, net)
    return True


async def run_forever(interval_seconds: float = 20.0) -> None:
    logger.info("SPORTS_POLY_SETTLEMENT_RECONCILER_READY interval_seconds=%s public_settlement_endpoint=true order_entry=false", interval_seconds)
    async with httpx.AsyncClient(timeout=12.0) as http:
        while True:
            try:
                trades = await fetch_open_trades(http)
                slugs = {slug for trade in trades for slug in exact_slugs(trade) if slug}
                settlements = await fetch_settlements(http, slugs) if slugs else {}
                realized = 0
                for trade in trades:
                    realized += int(await reconcile_trade(trade, settlements))
                logger.info("SPORTS_POLY_SETTLEMENT_HEARTBEAT open_awaiting=%s realized_this_cycle=%s", len(trades), realized)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("SPORTS_POLY_SETTLEMENT_LOOP_ERROR type=%s", type(exc).__name__)
            await asyncio.sleep(max(5.0, interval_seconds))
