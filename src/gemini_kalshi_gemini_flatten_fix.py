"""Fail-safe Gemini prediction-market emergency flatten patch.

Gemini Prediction Markets do not support MARKET orders. For a one-contract emergency
exit, use SELL LIMIT IOC at the minimum valid prediction-market price (0.01). The
limit price is a floor, not an expected execution price: any better resting bid is
received. q=1 means IOC cannot leave a partial contract.
"""
from __future__ import annotations

from typing import Any
import uuid

from src import gemini_kalshi_probe_transport as transport

EMERGENCY_MIN_PRICE = "0.01"
_INSTALLED = False


def build_emergency_flatten_payload(original_params: dict[str, Any], *, client_order_id: str | None = None) -> dict[str, Any]:
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
        "quantity": "1",
        "eventOutcome": outcome,
        "clientOrderId": client_order_id or f"flatten-g-{uuid.uuid4().hex[:20]}",
    }


async def _emergency_flatten_limit_ioc(self: transport.GeminiTradingSession, original_params: dict[str, Any], *, timeout: float = 2.0):
    payload = build_emergency_flatten_payload(original_params)
    return await self.place_fok(payload, terminal_timeout=timeout)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    transport.GeminiTradingSession.emergency_flatten_market = _emergency_flatten_limit_ioc
    _INSTALLED = True
