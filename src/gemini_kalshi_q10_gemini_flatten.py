"""Exact-known-quantity Gemini emergency exit for future q=10 scale work.

No quantity is inferred.  The caller supplies the exact known unhedged position, which
may be any positive amount up to ten contracts. Prediction markets do not accept a
MARKET exit, so the emergency order is a SELL LIMIT IOC at the minimum valid 0.01
price, accepting any better resting bid while bounding the requested exit price.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
import uuid

from src.gemini_kalshi_probe_transport import GeminiTradingSession

D = Decimal
MAX_Q10_FLATTEN = D("10")
EMERGENCY_MIN_PRICE = "0.01"


def _plain_quantity(q: Decimal) -> str:
    value = D(q)
    if value == value.to_integral_value():
        return format(value.quantize(D("1")), "f")
    return format(value.normalize(), "f")


def build_q10_emergency_flatten_payload(
    original_params: dict[str, Any],
    *,
    quantity: Decimal,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    q = D(quantity)
    if q <= 0 or q > MAX_Q10_FLATTEN:
        raise ValueError(f"q10 emergency flatten quantity must be >0 and <= {MAX_Q10_FLATTEN}")
    symbol = str(original_params.get("symbol") or "").strip()
    outcome = str(original_params.get("eventOutcome") or "").strip().upper()
    if not symbol:
        raise ValueError("q10 emergency flatten requires Gemini symbol")
    if outcome not in {"YES", "NO"}:
        raise ValueError("q10 emergency flatten requires YES/NO eventOutcome")
    return {
        "symbol": symbol,
        "side": "SELL",
        "type": "LIMIT",
        "timeInForce": "IOC",
        "price": EMERGENCY_MIN_PRICE,
        "quantity": _plain_quantity(q),
        "eventOutcome": outcome,
        "clientOrderId": client_order_id or f"flatten-q10-g-{uuid.uuid4().hex[:16]}",
    }


async def emergency_flatten_q10_exact_quantity(
    session: GeminiTradingSession,
    original_params: dict[str, Any],
    *,
    quantity: Decimal,
    timeout: float = 2.0,
):
    payload = build_q10_emergency_flatten_payload(
        original_params,
        quantity=D(quantity),
    )
    return await session.place_fok(payload, terminal_timeout=timeout)
