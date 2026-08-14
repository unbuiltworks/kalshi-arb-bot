"""Venue adapters for the hard-gated cross-market executor.

No code in this module runs on import. Callers must still pass the independent
LiveGate in cross_market_live_executor before any submit method is reached.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
import os
import uuid
from typing import Any

from polymarket_us import PolymarketUS

from src.auth import KalshiClient
from src.cross_market_live_executor import VenueFill


def _d(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return Decimal(str(value))
    except Exception:
        return None


class PolymarketUSAdapter:
    def __init__(self, client: PolymarketUS) -> None:
        self.client = client

    @staticmethod
    def _intent(side: str, *, closing: bool = False) -> str:
        if side == "OVER":
            return "ORDER_INTENT_SELL_LONG" if closing else "ORDER_INTENT_BUY_LONG"
        if side == "UNDER":
            return "ORDER_INTENT_SELL_SHORT" if closing else "ORDER_INTENT_BUY_SHORT"
        raise ValueError(f"Unsupported Polymarket side {side!r}")

    @classmethod
    def _payload(cls, *, market: str, side: str, price: Decimal, quantity: int) -> dict[str, Any]:
        return {
            "marketSlug": market,
            "intent": cls._intent(side),
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(price), "currency": "USD"},
            "quantity": quantity,
            "tif": "TIME_IN_FORCE_FILL_OR_KILL",
            "participateDontInitiate": False,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": True,
            "maxBlockTime": "2",
        }

    async def preview(self, *, market: str, side: str, price: Decimal, quantity: int) -> Any:
        payload = self._payload(market=market, side=side, price=price, quantity=quantity)
        # Preview validates the exact order without inserting it.
        return await asyncio.to_thread(self.client.orders.preview, payload)

    async def submit_fok(self, *, market: str, side: str, price: Decimal, quantity: int) -> VenueFill:
        payload = self._payload(market=market, side=side, price=price, quantity=quantity)
        raw = await asyncio.to_thread(self.client.orders.create, payload)
        executions = raw.get("executions", []) if isinstance(raw, dict) else []
        filled = Decimal("0")
        notional = Decimal("0")
        fee = Decimal("0")
        for execution in executions:
            qty = _d(execution.get("lastShares")) or Decimal("0")
            px = _d(execution.get("lastPx"))
            commission = _d(execution.get("commissionNotionalCollected")) or Decimal("0")
            filled += qty
            if px is not None:
                notional += qty * px
            fee += commission
        avg = notional / filled if filled > 0 else None
        return VenueFill(
            venue="polymarket",
            requested=quantity,
            filled=int(filled),
            average_price=avg,
            fee=fee,
            order_id=str(raw.get("id") or "") if isinstance(raw, dict) else None,
            raw=raw,
        )

    async def flatten_fok(
        self, *, market: str, side: str, price: Decimal, quantity: int
    ) -> VenueFill:
        payload = self._payload(market=market, side=side, price=price, quantity=quantity)
        payload["intent"] = self._intent(side, closing=True)
        raw = await asyncio.to_thread(self.client.orders.create, payload)
        executions = raw.get("executions", []) if isinstance(raw, dict) else []
        filled = sum((_d(x.get("lastShares")) or Decimal("0")) for x in executions)
        return VenueFill(
            venue="polymarket",
            requested=quantity,
            filled=int(filled),
            order_id=str(raw.get("id") or "") if isinstance(raw, dict) else None,
            raw=raw,
        )


class KalshiV2Adapter:
    """Kalshi event-market V2 adapter using one YES-side book.

    V2 `bid` buys YES. V2 `ask` sells YES, which is economically equivalent to
    buying NO at 1 - YES price.
    """

    def __init__(self, client: KalshiClient) -> None:
        self.client = client

    @staticmethod
    def _book_order(side: str, outcome_price: Decimal) -> tuple[str, Decimal]:
        if side == "OVER":
            return "bid", outcome_price
        if side == "UNDER":
            return "ask", Decimal("1") - outcome_price
        raise ValueError(f"Unsupported Kalshi side {side!r}")

    async def preview(self, *, market: str, side: str, price: Decimal, quantity: int) -> Any:
        # Kalshi has no order-preview equivalent in this API. Fail closed unless the
        # exact market is still retrievable and active immediately before submission.
        response = await self.client.get(f"/markets/{market}")
        response.raise_for_status()
        raw = response.json()
        m = raw.get("market", {})
        status = str(m.get("status") or "").lower()
        if status not in {"active", "open"}:
            raise RuntimeError(f"Kalshi market is not active: {market} status={status}")
        book_side, yes_price = self._book_order(side, price)
        return {
            "ticker": market,
            "book_side": book_side,
            "yes_price": str(yes_price),
            "quantity": quantity,
            "status": status,
        }

    async def submit_fok(self, *, market: str, side: str, price: Decimal, quantity: int) -> VenueFill:
        book_side, yes_price = self._book_order(side, price)
        body = {
            "ticker": market,
            "client_order_id": str(uuid.uuid4()),
            "side": book_side,
            "count": f"{Decimal(quantity):.2f}",
            "price": f"{yes_price:.4f}",
            "time_in_force": "fill_or_kill",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": False,
            "subaccount": 0,
            "exchange_index": 0,
        }
        response = await self.client.post("/portfolio/events/orders", json=body)
        response.raise_for_status()
        raw = response.json()
        filled = _d(raw.get("fill_count")) or Decimal("0")
        avg = _d(raw.get("average_fill_price"))
        avg_fee = _d(raw.get("average_fee_paid"))
        fee = avg_fee * filled if avg_fee is not None else None
        return VenueFill(
            venue="kalshi",
            requested=quantity,
            filled=int(filled),
            average_price=avg,
            fee=fee,
            order_id=str(raw.get("order_id") or ""),
            raw=raw,
        )

    async def flatten_fok(
        self, *, market: str, side: str, price: Decimal, quantity: int
    ) -> VenueFill:
        # Closing an OVER position means ask YES. Closing UNDER means bid YES.
        if side == "OVER":
            book_side, yes_price = "ask", price
        elif side == "UNDER":
            book_side, yes_price = "bid", Decimal("1") - price
        else:
            raise ValueError(f"Unsupported Kalshi side {side!r}")
        body = {
            "ticker": market,
            "client_order_id": str(uuid.uuid4()),
            "side": book_side,
            "count": f"{Decimal(quantity):.2f}",
            "price": f"{yes_price:.4f}",
            "time_in_force": "fill_or_kill",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": True,
            "subaccount": 0,
            "exchange_index": 0,
        }
        response = await self.client.post("/portfolio/events/orders", json=body)
        response.raise_for_status()
        raw = response.json()
        filled = _d(raw.get("fill_count")) or Decimal("0")
        return VenueFill(
            venue="kalshi",
            requested=quantity,
            filled=int(filled),
            order_id=str(raw.get("order_id") or ""),
            raw=raw,
        )


def build_poly_client_from_env() -> PolymarketUS:
    key = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key or not secret:
        raise RuntimeError("Polymarket credentials missing")
    return PolymarketUS(key_id=key, secret_key=secret, timeout=10.0)
