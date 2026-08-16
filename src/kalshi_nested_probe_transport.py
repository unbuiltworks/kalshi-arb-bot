"""Kalshi-only V2 transport primitives for the disarmed nested live probe.

The module does not select trades and does not auto-run.  Callers must pass the
live-probe guard before using these methods.  The implementation is intentionally
small: create, get/reconcile, cancel, and pure parsers for private fill/user-order
WebSocket messages.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
import asyncio
from typing import Any

import httpx

from src.auth import KalshiClient
from src.kalshi_nested_live_probe_guard import D

KALSHI_PROD_REST = "https://external-api.kalshi.com/trade-api/v2"


class AmbiguousOrderSubmission(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderAck:
    order_id: str
    client_order_id: str
    fill_count: Decimal
    remaining_count: Decimal
    ts_ms: int | None
    reconciled: bool = False

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["fill_count"] = str(self.fill_count)
        row["remaining_count"] = str(self.remaining_count)
        return row


@dataclass(frozen=True)
class OrderState:
    order_id: str
    client_order_id: str
    ticker: str
    fill_count: Decimal
    remaining_count: Decimal
    taker_fill_cost: Decimal = D("0")
    maker_fill_cost: Decimal = D("0")
    taker_fees: Decimal = D("0")
    maker_fees: Decimal = D("0")

    @property
    def fully_filled(self) -> bool:
        return self.fill_count >= D("1") and self.remaining_count <= D("0")

    @property
    def total_cash_cost(self) -> Decimal:
        return self.taker_fill_cost + self.maker_fill_cost + self.taker_fees + self.maker_fees

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "ticker": self.ticker,
            "fill_count": str(self.fill_count),
            "remaining_count": str(self.remaining_count),
            "taker_fill_cost": str(self.taker_fill_cost),
            "maker_fill_cost": str(self.maker_fill_cost),
            "taker_fees": str(self.taker_fees),
            "maker_fees": str(self.maker_fees),
            "total_cash_cost": str(self.total_cash_cost),
        }


def parse_create_order_response(body: dict[str, Any], *, reconciled: bool = False) -> OrderAck:
    order_id = str(body.get("order_id") or "")
    client_id = str(body.get("client_order_id") or "")
    if not order_id or not client_id:
        raise ValueError("create-order response missing order identifiers")
    return OrderAck(
        order_id=order_id,
        client_order_id=client_id,
        fill_count=D(str(body.get("fill_count") or "0")),
        remaining_count=D(str(body.get("remaining_count") or "0")),
        ts_ms=int(body["ts_ms"]) if body.get("ts_ms") is not None else None,
        reconciled=reconciled,
    )


def parse_order_state(row: dict[str, Any]) -> OrderState:
    order_id = str(row.get("order_id") or "")
    client_id = str(row.get("client_order_id") or "")
    ticker = str(row.get("ticker") or "")
    if not order_id or not client_id or not ticker:
        raise ValueError("order row missing identifiers")
    return OrderState(
        order_id=order_id,
        client_order_id=client_id,
        ticker=ticker,
        fill_count=D(str(row.get("fill_count_fp") or row.get("fill_count") or "0")),
        remaining_count=D(str(row.get("remaining_count_fp") or row.get("remaining_count") or "0")),
        taker_fill_cost=D(str(row.get("taker_fill_cost_dollars") or "0")),
        maker_fill_cost=D(str(row.get("maker_fill_cost_dollars") or "0")),
        taker_fees=D(str(row.get("taker_fees_dollars") or "0")),
        maker_fees=D(str(row.get("maker_fees_dollars") or "0")),
    )


def private_event_progress(
    payload: dict[str, Any],
    *,
    order_id: str,
    client_order_id: str,
) -> tuple[str, Decimal] | None:
    """Normalize one matching private WS message to (state, cumulative/known fill)."""
    msg_type = str(payload.get("type") or "")
    msg = payload.get("msg") or {}
    if msg_type == "fill":
        if str(msg.get("order_id") or "") != order_id:
            return None
        count = D(str(msg.get("count_fp") or "0"))
        return ("FILL", count)
    if msg_type == "user_order":
        if str(msg.get("order_id") or "") != order_id and str(msg.get("client_order_id") or "") != client_order_id:
            return None
        fill = D(str(msg.get("fill_count_fp") or "0"))
        remaining = D(str(msg.get("remaining_count_fp") or "0"))
        status = str(msg.get("status") or "").lower()
        if fill >= D("1") or (status == "executed" and remaining <= 0):
            return ("FILLED", fill)
        if status in {"canceled", "cancelled"} and fill <= 0:
            return ("CANCELED", fill)
        return ("OPEN", fill)
    return None


class KalshiV2RestTransport:
    def __init__(self, client: KalshiClient | None = None) -> None:
        self.client = client or KalshiClient(base_url=KALSHI_PROD_REST)
        self._owns = client is None

    async def close(self) -> None:
        if self._owns:
            await self.client.close()

    async def reconcile_by_client_id(self, *, ticker: str, client_order_id: str) -> OrderState | None:
        response = await self.client.get("/portfolio/orders", params={"ticker": ticker, "limit": 100})
        if response.status_code != 200:
            return None
        for row in response.json().get("orders", []):
            if str(row.get("client_order_id") or "") == client_order_id:
                return parse_order_state(row)
        return None

    async def create(self, payload: dict[str, Any]) -> OrderAck:
        try:
            response = await self.client.post("/portfolio/events/orders", json=payload)
        except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError) as exc:
            recovered = await self.reconcile_by_client_id(
                ticker=str(payload["ticker"]),
                client_order_id=str(payload["client_order_id"]),
            )
            if recovered is None:
                raise AmbiguousOrderSubmission(str(payload["client_order_id"])) from exc
            return OrderAck(
                order_id=recovered.order_id,
                client_order_id=recovered.client_order_id,
                fill_count=recovered.fill_count,
                remaining_count=recovered.remaining_count,
                ts_ms=None,
                reconciled=True,
            )
        if response.status_code != 201:
            detail = ""
            try:
                detail = str(response.json())[:500]
            except Exception:
                detail = response.text[:500]
            raise RuntimeError(f"Kalshi create order HTTP {response.status_code}: {detail}")
        return parse_create_order_response(response.json())

    async def get(self, order_id: str) -> OrderState:
        response = await self.client.get(f"/portfolio/orders/{order_id}")
        response.raise_for_status()
        return parse_order_state(response.json().get("order") or {})

    async def cancel(self, order_id: str) -> dict[str, Any]:
        response = await self.client.delete(f"/portfolio/events/orders/{order_id}")
        if response.status_code not in {200, 204}:
            # A fill/cancel can race the cancel request; inspect exact state before
            # deciding it is an error.
            try:
                state = await self.get(order_id)
                if state.fully_filled or state.remaining_count <= 0:
                    return {"order_id": order_id, "race_resolved": True, "state": state.as_dict()}
            except Exception:
                pass
            raise RuntimeError(f"Kalshi cancel order HTTP {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except Exception:
            return {"order_id": order_id, "canceled": True}
