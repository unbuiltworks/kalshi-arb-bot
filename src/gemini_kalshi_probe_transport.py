"""Disabled transport primitives for a future one-contract Gemini/Kalshi probe.

This module contains the network mechanics required to measure real execution:
- one persistent authenticated Gemini WebSocket with order events,
- Kalshi V2 FOK order submission,
- simultaneous two-venue sends,
- exact local/exchange timing and fill accounting,
- one-contract emergency flatten helpers for a one-leg fill.

It is intentionally NOT a runnable trading bot and is not exposed by any remote
control workflow. The dual-submit entry point itself also enforces the two-factor
live opt-in plus one-contract/edge/quote-age guardrails.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from decimal import Decimal
import json
import time
from typing import Any
import uuid

from src.auth import KalshiClient
from src.gemini_kalshi_live_probe_guard import live_probe_authorized, validate_probe_candidate
from src.gemini_prediction_auth import authenticated_ws_connect

D = Decimal


@dataclass(frozen=True)
class ExecutionResult:
    venue: str
    client_order_id: str
    filled_quantity: str
    average_fill_price: str | None
    fee_paid: str | None
    status: str
    reason: str | None
    local_send_ns: int
    local_done_ns: int
    exchange_ts_ns: int | None
    request_latency_ms: float
    raw_request_status: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class GeminiTradingSession:
    """Persistent authenticated Gemini WS with request/order-event demux."""

    TERMINAL = {"FILLED", "CANCELED", "REJECTED"}

    def __init__(self) -> None:
        self.ws = None
        self._reader: asyncio.Task | None = None
        self._responses: dict[str, asyncio.Future] = {}
        self._terminal: dict[str, asyncio.Future] = {}
        self._fill_qty: dict[str, D] = {}
        self._fill_notional: dict[str, D] = {}
        self._fees: dict[str, D] = {}
        self._send_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.ws is not None:
            return
        self.ws = await authenticated_ws_connect(timeout=5.0)
        self._reader = asyncio.create_task(self._read_loop())
        ack = await self.request("SUBSCRIBE", ["orders@account"], timeout=5.0)
        if int(ack.get("status", 0)) != 200:
            await self.close()
            raise RuntimeError("Gemini orders@account subscription failed")

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None
        for future in list(self._responses.values()) + list(self._terminal.values()):
            if not future.done():
                future.cancel()
        self._responses.clear()
        self._terminal.clear()

    async def request(self, method: str, params: Any, *, timeout: float = 2.0) -> dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("Gemini trading session is not connected")
        request_id = f"req-{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self._responses[request_id] = future
        payload = {"id": request_id, "method": method, "params": params}
        try:
            async with self._send_lock:
                await self.ws.send(json.dumps(payload, separators=(",", ":")))
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._responses.pop(request_id, None)

    async def place_fok(self, params: dict[str, Any], *, terminal_timeout: float = 2.0) -> ExecutionResult:
        """Send one already-validated immediate Gemini order and wait for terminal event."""
        if self.ws is None:
            raise RuntimeError("Gemini trading session is not connected")
        client_id = str(params["clientOrderId"])
        terminal = asyncio.get_running_loop().create_future()
        self._terminal[client_id] = terminal
        self._fill_qty[client_id] = D("0")
        self._fill_notional[client_id] = D("0")
        self._fees[client_id] = D("0")
        sent = time.perf_counter_ns()
        try:
            response = await self.request("order.place", params, timeout=terminal_timeout)
            status_code = int(response.get("status", 0))
            if status_code != 200:
                done = time.perf_counter_ns()
                error = response.get("error") or {}
                return ExecutionResult(
                    venue="gemini",
                    client_order_id=client_id,
                    filled_quantity="0",
                    average_fill_price=None,
                    fee_paid=None,
                    status="REJECTED",
                    reason=str(error.get("msg") or error.get("code") or "request_rejected"),
                    local_send_ns=sent,
                    local_done_ns=done,
                    exchange_ts_ns=None,
                    request_latency_ms=(done - sent) / 1_000_000,
                    raw_request_status=status_code,
                )
            event = await asyncio.wait_for(terminal, timeout=terminal_timeout)
            done = time.perf_counter_ns()
            qty = self._fill_qty.get(client_id, D("0"))
            notional = self._fill_notional.get(client_id, D("0"))
            fee = self._fees.get(client_id, D("0"))
            avg = (notional / qty) if qty > 0 else None
            return ExecutionResult(
                venue="gemini",
                client_order_id=client_id,
                filled_quantity=str(qty),
                average_fill_price=str(avg) if avg is not None else None,
                fee_paid=str(fee) if fee > 0 else ("0" if qty > 0 else None),
                status=str(event.get("X") or "UNKNOWN"),
                reason=str(event.get("r")) if event.get("r") else None,
                local_send_ns=sent,
                local_done_ns=done,
                exchange_ts_ns=int(event["T"]) if event.get("T") is not None else None,
                request_latency_ms=(done - sent) / 1_000_000,
                raw_request_status=status_code,
            )
        finally:
            self._terminal.pop(client_id, None)
            self._fill_qty.pop(client_id, None)
            self._fill_notional.pop(client_id, None)
            self._fees.pop(client_id, None)

    async def emergency_flatten_market(self, original_params: dict[str, Any], *, timeout: float = 2.0) -> ExecutionResult:
        """Sell exactly one filled Gemini outcome using MARKET+IOC."""
        flatten = {
            "symbol": original_params["symbol"],
            "side": "SELL",
            "type": "MARKET",
            "timeInForce": "IOC",
            "quantity": "1",
            "eventOutcome": original_params["eventOutcome"],
            "clientOrderId": f"flatten-g-{uuid.uuid4().hex[:20]}",
        }
        return await self.place_fok(flatten, terminal_timeout=timeout)

    async def _read_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                request_id = str(msg.get("id") or "")
                if request_id:
                    future = self._responses.get(request_id)
                    if future is not None and not future.done():
                        future.set_result(msg)
                    continue
                if msg.get("e") != "orderUpdate":
                    continue
                client_id = str(msg.get("c") or "")
                if not client_id:
                    continue
                status = str(msg.get("X") or "")
                if status in {"FILLED", "PARTIALLY_FILLED"}:
                    delta = D(str(msg.get("Z") or "0"))
                    price = D(str(msg.get("L") or msg.get("p") or "0"))
                    self._fill_qty[client_id] = self._fill_qty.get(client_id, D("0")) + delta
                    self._fill_notional[client_id] = self._fill_notional.get(client_id, D("0")) + delta * price
                    if msg.get("n") is not None:
                        self._fees[client_id] = self._fees.get(client_id, D("0")) + D(str(msg["n"]))
                elif status == "CANCELED" and msg.get("Z") is not None:
                    cumulative = D(str(msg.get("Z") or "0"))
                    if cumulative > self._fill_qty.get(client_id, D("0")):
                        self._fill_qty[client_id] = cumulative

                if status in self.TERMINAL:
                    future = self._terminal.get(client_id)
                    if future is not None and not future.done():
                        future.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for future in list(self._responses.values()) + list(self._terminal.values()):
                if not future.done():
                    future.set_exception(RuntimeError("Gemini trading WebSocket reader failed"))
            raise exc


class KalshiV2Executor:
    def __init__(self, client: KalshiClient | None = None) -> None:
        self.client = client or KalshiClient()
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()

    async def place(self, payload: dict[str, Any]) -> ExecutionResult:
        client_id = str(payload["client_order_id"])
        sent = time.perf_counter_ns()
        response = await self.client.post("/portfolio/events/orders", json=payload)
        done = time.perf_counter_ns()
        status_code = int(response.status_code)
        if status_code != 201:
            reason = f"HTTP_{status_code}"
            try:
                body = response.json()
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(error, dict):
                    reason = str(error.get("code") or reason)
            except Exception:
                pass
            return ExecutionResult(
                venue="kalshi",
                client_order_id=client_id,
                filled_quantity="0",
                average_fill_price=None,
                fee_paid=None,
                status="REJECTED",
                reason=reason,
                local_send_ns=sent,
                local_done_ns=done,
                exchange_ts_ns=None,
                request_latency_ms=(done - sent) / 1_000_000,
                raw_request_status=status_code,
            )
        body = response.json()
        fill_count = D(str(body.get("fill_count") or "0"))
        avg_fee = D(str(body.get("average_fee_paid") or "0")) if fill_count > 0 else D("0")
        return ExecutionResult(
            venue="kalshi",
            client_order_id=client_id,
            filled_quantity=str(fill_count),
            average_fill_price=str(body.get("average_fill_price")) if body.get("average_fill_price") is not None else None,
            fee_paid=str(avg_fee * fill_count) if fill_count > 0 else None,
            status="FILLED" if fill_count == D(str(payload["count"])) else "CANCELED",
            reason=None if fill_count > 0 else "FOK_NOT_FILLED",
            local_send_ns=sent,
            local_done_ns=done,
            exchange_ts_ns=int(body["ts_ms"]) * 1_000_000 if body.get("ts_ms") is not None else None,
            request_latency_ms=(done - sent) / 1_000_000,
            raw_request_status=status_code,
        )

    async def emergency_flatten(self, original_payload: dict[str, Any]) -> ExecutionResult:
        """Attempt to neutralize exactly one filled Kalshi leg with a marketable IOC."""
        original_side = str(original_payload["side"])
        if original_side == "bid":
            side, price = "ask", "0.0100"
        elif original_side == "ask":
            side, price = "bid", "0.9900"
        else:
            raise ValueError("unsupported Kalshi side")
        payload = {
            "ticker": original_payload["ticker"],
            "client_order_id": f"flatten-k-{uuid.uuid4().hex[:20]}",
            "side": side,
            "count": "1.00",
            "price": price,
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": True,
            "subaccount": 0,
            "exchange_index": 0,
        }
        return await self.place(payload)


def _assert_fok_one_contract(gemini_payload: dict[str, Any], kalshi_payload: dict[str, Any]) -> None:
    if str(gemini_payload.get("quantity")) != "1" or str(gemini_payload.get("timeInForce")) != "FOK":
        raise ValueError("Gemini first probe must be exactly one-contract FOK")
    if D(str(kalshi_payload.get("count"))) != D("1") or str(kalshi_payload.get("time_in_force")) != "fill_or_kill":
        raise ValueError("Kalshi first probe must be exactly one-contract FOK")


async def submit_dual_probe(
    gemini: GeminiTradingSession,
    kalshi: KalshiV2Executor,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    quote_skew_ms: int,
    cli_live: bool = False,
) -> dict[str, Any]:
    """Submit two pre-validated one-contract FOK legs concurrently.

    Sending is impossible unless BOTH the caller supplies cli_live=True and the
    environment contains ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES. The same function
    then rechecks the fixed one-contract size, minimum 3-cent modeled after-fee
    lock, and <=50ms quote skew before any network mutation.
    """
    if not live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi probes are disabled")
    _assert_fok_one_contract(gemini_payload, kalshi_payload)
    validate_probe_candidate(
        quantity=D("1"),
        locked_profit=D(modeled_locked_profit),
        quote_skew_ms=int(quote_skew_ms),
    )

    g_result, k_result = await asyncio.gather(
        gemini.place_fok(gemini_payload),
        kalshi.place(kalshi_payload),
    )
    g_filled = D(g_result.filled_quantity) == D("1")
    k_filled = D(k_result.filled_quantity) == D("1")
    outcome = "BOTH_FILLED" if g_filled and k_filled else "NEITHER_FILLED" if not g_filled and not k_filled else "ONE_LEG_FILLED"
    report: dict[str, Any] = {
        "outcome": outcome,
        "gemini": g_result.as_dict(),
        "kalshi": k_result.as_dict(),
        "flatten": None,
    }
    if g_filled and not k_filled:
        flatten = await gemini.emergency_flatten_market(gemini_payload)
        report["flatten"] = flatten.as_dict()
    elif k_filled and not g_filled:
        flatten = await kalshi.emergency_flatten(kalshi_payload)
        report["flatten"] = flatten.as_dict()
    return report
