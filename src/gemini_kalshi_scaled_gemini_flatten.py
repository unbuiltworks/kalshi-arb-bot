"""Exact-quantity emergency flatten for bounded q1-q5 Gemini prediction positions.

Prediction markets do not accept MARKET exits.  Cross the book with a SELL LIMIT IOC
at the minimum valid price, which receives any better resting bid while making 0.01
the explicit worst permitted exit price.  The caller supplies the exact unhedged
quantity; this helper never guesses from the original order size.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any
import uuid

from src.gemini_kalshi_probe_transport import GeminiTradingSession

D = Decimal
EMERGENCY_MIN_PRICE = "0.01"
MAX_SCALE_QUANTITY = D("5")


def build_scaled_emergency_flatten_payload(
    original_params: dict[str, Any],
    *,
    quantity: Decimal,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    q = D(quantity)
    if q <= 0 or q > MAX_SCALE_QUANTITY:
        raise ValueError(f"emergency flatten quantity must be >0 and <= {MAX_SCALE_QUANTITY}")
    symbol = str(original_params.get("symbol") or "").strip()
    outcome = str(original_params.get("eventOutcome") or "").strip().upper()
    if not symbol:
        raise ValueError("emergency flatten requires Gemini symbol")
    if outcome not in {"YES", "NO"}:
        raise ValueError("emergency flatten requires YES/NO eventOutcome")
    return {
        "symbol": symbol,
        "side": "SELL",
        "type": "LIMIT",
        "timeInForce": "IOC",
        "price": EMERGENCY_MIN_PRICE,
        "quantity": str(q.normalize()),
        "eventOutcome": outcome,
        "clientOrderId": client_order_id or f"flatten-g-{uuid.uuid4().hex[:20]}",
    }


async def emergency_flatten_exact_quantity(
    session: GeminiTradingSession,
    original_params: dict[str, Any],
    *,
    quantity: Decimal,
    timeout: float = 2.0,
):
    payload = build_scaled_emergency_flatten_payload(original_params, quantity=D(quantity))
    return await session.place_fok(payload, terminal_timeout=timeout)
