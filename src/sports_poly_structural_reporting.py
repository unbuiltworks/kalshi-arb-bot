"""Best-effort authoritative ledger reporting for same-Polymarket structural q1.

Reporting never authorizes or submits an order. It mirrors already-completed
campaign results into ARB Command Center using exact order details. Positive
locked economics are NOT emitted as TRADE_REALIZED; a two-leg fill remains
AWAITING_SETTLEMENT until a settlement reconciler observes definitive outcomes.

Confirmed zero-fill maker outcomes are not re-read from the authenticated order
API. That keeps ledger observability from consuming the rate-limit budget needed
for execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
from typing import Any, Mapping

import httpx

D = Decimal
LEDGER_URL = os.getenv(
    "ARB_LEDGER_URL",
    "https://arb-command-center-production.up.railway.app/api/events",
).strip()
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
ZERO_FILL_STATUSES = {
    "MAKER_NOT_FILLED_CANCELED",
    "MAKER_CANCELED_EDGE_GONE",
    "SKIP_BOOK_MISSING",
    "SKIP_HEDGE_DEPTH",
    "SKIP_FLOOR",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def _d(value: Any, default: str = "0") -> D:
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        return D(str(value))
    except Exception:
        return D(default)


def _order(resp: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(resp, Mapping):
        return {}
    row = resp.get("order")
    return row if isinstance(row, Mapping) else resp


def _native_price(order: Mapping[str, Any], side: str, fallback: Any) -> D:
    yes_avg = _d(order.get("avgPx"), str(fallback or "0"))
    return yes_avg if side == "YES" else D("1") - yes_avg


async def _post(event: dict[str, Any]) -> bool:
    if not LEDGER_TOKEN:
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            response = await http.post(
                LEDGER_URL,
                headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
                json=event,
            )
            response.raise_for_status()
        return True
    except Exception:
        return False


async def report_attempt(
    *,
    client: Any,
    row: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Mirror exact known fills and execution state; never changes trading state."""
    campaign_status = str(result.get("status") or "UNKNOWN")
    if campaign_status in ZERO_FILL_STATUSES:
        return {"ledger_reported": False, "reason": "confirmed_zero_fill"}

    maker_id = str(result.get("maker_order_id") or "")
    hedge_id = str(result.get("hedge_order_id") or "")
    if not maker_id and not hedge_id:
        return {"ledger_reported": False, "reason": "no_order_ids"}

    maker_order: Mapping[str, Any] = {}
    hedge_order: Mapping[str, Any] = {}
    if maker_id:
        try:
            maker_order = _order(await __import__("asyncio").to_thread(client.orders.retrieve, maker_id))
        except Exception:
            maker_order = {}
    if hedge_id:
        try:
            hedge_order = _order(await __import__("asyncio").to_thread(client.orders.retrieve, hedge_id))
        except Exception:
            hedge_order = {}

    trade_id = _safe_id(f"sports-poly:{maker_id or hedge_id}")
    market_key = f"{row.get('low_slug')}|{row.get('high_slug')}"
    family = str(row.get("family") or "structural_total")
    classification = "LIVE_SAME_VENUE_STRUCTURAL_LOCK"
    base_meta = {
        "classification": classification,
        "family": family,
        "low_slug": row.get("low_slug"),
        "high_slug": row.get("high_slug"),
        "low_threshold": str(row.get("low_threshold") or ""),
        "high_threshold": str(row.get("high_threshold") or ""),
        "maker_slug": row.get("maker_slug"),
        "maker_side": row.get("maker_side"),
        "hedge_slug": row.get("hedge_slug"),
        "hedge_side": row.get("hedge_side"),
        "maker_order_id": maker_id,
        "hedge_order_id": hedge_id,
        "campaign_status": campaign_status,
        "actual_net_locked_profit": result.get("actual_net_locked_profit"),
    }

    fills = 0
    total_fee = D("0")
    total_amount = D("0")
    for leg, oid, order, slug, side, fallback in (
        ("maker", maker_id, maker_order, str(row.get("maker_slug") or ""), str(row.get("maker_side") or ""), row.get("executable_maker_quote") or row.get("maker_price")),
        ("hedge", hedge_id, hedge_order, str(row.get("hedge_slug") or ""), str(row.get("hedge_side") or ""), row.get("hedge_ask")),
    ):
        if not oid or not order:
            continue
        qty = _d(order.get("cumQuantity"))
        if qty <= 0:
            continue
        price = _native_price(order, side, fallback)
        amount = qty * price
        fee = _d(order.get("commissionNotionalTotalCollected"))
        fills += 1
        total_fee += fee
        total_amount += amount
        await _post({
            "event_id": _safe_id(f"{trade_id}:fill:{leg}"),
            "trade_id": trade_id,
            "event_type": "FILL",
            "event_ts": _now(),
            "strategy": "sports_poly_structural_q1",
            "mode": "LIVE_PROBE",
            "market": market_key,
            "asset": family,
            "venues": ["polymarket"],
            "side": f"BUY {side}",
            "status": str(order.get("state") or campaign_status or "FILLED"),
            "quantity": str(qty),
            "price": str(price),
            "amount": str(amount),
            "fee": str(fee),
            "legs_required": 2,
            "legs_filled": fills,
            "metadata": {**base_meta, "leg": leg, "slug": slug, "order_state": order.get("state")},
        })

    awaiting = campaign_status == "BOTH_FILLED_RECONCILED" and fills == 2
    execution_posted = await _post({
        "event_id": _safe_id(f"{trade_id}:execution-result"),
        "trade_id": trade_id,
        "event_type": "EXECUTION_RESULT",
        "event_ts": _now(),
        "strategy": "sports_poly_structural_q1",
        "mode": "LIVE_PROBE",
        "market": market_key,
        "asset": family,
        "venues": ["polymarket"],
        "side": "COMPLEMENTARY_STRUCTURAL",
        "status": "AWAITING_SETTLEMENT" if awaiting else campaign_status,
        "quantity": "1",
        "legs_required": 2,
        "legs_filled": fills,
        "fee": str(total_fee),
        "capital_wagered": str(total_amount),
        "opportunity_net_profit": str(result.get("actual_net_locked_profit") or row.get("quote_locked_profit") or "0"),
        "metadata": {
            **base_meta,
            "exact_fill_capital": str(total_amount),
            "exact_fill_fees": str(total_fee),
            "settlement_state": "AWAITING_SETTLEMENT" if awaiting else "NOT_FULLY_HEDGED_OR_RECONCILED",
        },
    })
    return {
        "ledger_reported": bool(execution_posted),
        "trade_id": trade_id,
        "fills_reported": fills,
        "awaiting_settlement": awaiting,
    }
