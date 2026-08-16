"""Transport primitives for a tightly bounded Gemini/Kalshi BTC execution probe.

Live sending remains impossible unless both the caller opts in and
ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES. The transport forces Kalshi production,
recomputes exact payload economics before send, records acknowledgement and
terminal latency separately, reconciles ambiguous submissions where possible,
and halts on unresolved ambiguity rather than guessing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from decimal import Decimal
import json
import time
from typing import Any
import uuid

import httpx

from src.auth import KalshiClient
from src.gemini_kalshi_live_probe_guard import (
    live_probe_authorized,
    validate_exact_probe_payloads,
)
from src.gemini_prediction_auth import authenticated_ws_connect, private_rest_request

D = Decimal
KALSHI_PROD_REST = "https://external-api.kalshi.com/trade-api/v2"


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
    ack_latency_ms: float | None = None
    raw_request_status: int | None = None
    reconciled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class GeminiTradingSession:
    """Persistent authenticated Gemini WS with order-response/event demux."""

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

    async def _reconcile_rest(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """Recover an immediate order by clientOrderId after an ambiguous WS request."""
        client_id = str(params["clientOrderId"])
        symbol = str(params["symbol"])
        now_ms = int(time.time() * 1000)
        queries = [
            ("/v1/prediction-markets/orders/active", {"symbol": symbol, "limit": 100, "offset": 0}),
            ("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": symbol, "limit": 100, "from": now_ms - 120000, "to": now_ms + 1000}),
            ("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": symbol, "limit": 100, "from": now_ms - 120000, "to": now_ms + 1000}),
        ]
        for path, body in queries:
            try:
                response = await private_rest_request("POST", path, params=body, timeout=3.0)
                if response.status_code != 200:
                    continue
                for row in response.json().get("orders", []):
                    if str(row.get("clientOrderId") or "") == client_id:
                        return row
            except Exception:
                continue
        return None

    def _event_result(
        self,
        *,
        client_id: str,
        event: dict[str, Any],
        sent_ns: int,
        ack_ns: int | None,
        raw_status: int | None,
        reconciled: bool,
    ) -> ExecutionResult:
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
            local_send_ns=sent_ns,
            local_done_ns=done,
            exchange_ts_ns=int(event["T"]) if event.get("T") is not None else None,
            request_latency_ms=(done - sent_ns) / 1_000_000,
            ack_latency_ms=((ack_ns - sent_ns) / 1_000_000) if ack_ns is not None else None,
            raw_request_status=raw_status,
            reconciled=reconciled,
        )

    async def place_fok(self, params: dict[str, Any], *, terminal_timeout: float = 2.0) -> ExecutionResult:
        """Send one immediate Gemini order and resolve fill/cancel state conservatively."""
        if self.ws is None:
            raise RuntimeError("Gemini trading session is not connected")
        client_id = str(params["clientOrderId"])
        terminal = asyncio.get_running_loop().create_future()
        self._terminal[client_id] = terminal
        self._fill_qty[client_id] = D("0")
        self._fill_notional[client_id] = D("0")
        self._fees[client_id] = D("0")
        sent = time.perf_counter_ns()
        ack_ns: int | None = None
        try:
            try:
                response = await self.request("order.place", params, timeout=terminal_timeout)
                ack_ns = time.perf_counter_ns()
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
                        ack_latency_ms=(ack_ns - sent) / 1_000_000,
                        raw_request_status=status_code,
                    )
                event = await asyncio.wait_for(terminal, timeout=terminal_timeout)
                return self._event_result(
                    client_id=client_id,
                    event=event,
                    sent_ns=sent,
                    ack_ns=ack_ns,
                    raw_status=status_code,
                    reconciled=False,
                )
            except (asyncio.TimeoutError, TimeoutError):
                # The order may have reached Gemini even if the request acknowledgement did not.
                try:
                    event = await asyncio.wait_for(terminal, timeout=1.0)
                    return self._event_result(
                        client_id=client_id,
                        event=event,
                        sent_ns=sent,
                        ack_ns=ack_ns,
                        raw_status=None,
                        reconciled=True,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    row = await self._reconcile_rest(params)
                    done = time.perf_counter_ns()
                    if row is None:
                        return ExecutionResult(
                            venue="gemini",
                            client_order_id=client_id,
                            filled_quantity="0",
                            average_fill_price=None,
                            fee_paid=None,
                            status="UNKNOWN",
                            reason="AMBIGUOUS_SUBMISSION_UNRESOLVED",
                            local_send_ns=sent,
                            local_done_ns=done,
                            exchange_ts_ns=None,
                            request_latency_ms=(done - sent) / 1_000_000,
                            ack_latency_ms=None,
                            raw_request_status=None,
                            reconciled=False,
                        )
                    return ExecutionResult(
                        venue="gemini",
                        client_order_id=client_id,
                        filled_quantity=str(row.get("filledQuantity") or "0"),
                        average_fill_price=str(row.get("avgExecutionPrice")) if row.get("avgExecutionPrice") is not None else None,
                        fee_paid=None,
                        status=str(row.get("status") or "UNKNOWN").upper(),
                        reason="RECONCILED_REST",
                        local_send_ns=sent,
                        local_done_ns=done,
                        exchange_ts_ns=None,
                        request_latency_ms=(done - sent) / 1_000_000,
                        ack_latency_ms=None,
                        raw_request_status=None,
                        reconciled=True,
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
                    # Gemini documents Z as the quantity filled in the last execution
                    # for fill events, and as cumulative filled quantity on CANCELED.
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
    """V2 order executor pinned to Kalshi's recommended production Trade API host."""

    def __init__(self, client: KalshiClient | None = None) -> None:
        self.client = client or KalshiClient(base_url=KALSHI_PROD_REST)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()

    async def _reconcile_by_client_id(self, payload: dict[str, Any]) -> ExecutionResult | None:
        client_id = str(payload["client_order_id"])
        try:
            response = await self.client.get(
                "/portfolio/orders",
                params={"ticker": str(payload["ticker"]), "limit": 100},
            )
            if response.status_code != 200:
                return None
            for row in response.json().get("orders", []):
                if str(row.get("client_order_id") or "") != client_id:
                    continue
                fill_count = D(str(row.get("fill_count_fp") or "0"))
                fee = D(str(row.get("taker_fees_dollars") or "0")) if fill_count > 0 else D("0")
                cost = D(str(row.get("taker_fill_cost_dollars") or "0")) if fill_count > 0 else D("0")
                avg = (cost / fill_count) if fill_count > 0 else None
                now = time.perf_counter_ns()
                return ExecutionResult(
                    venue="kalshi",
                    client_order_id=client_id,
                    filled_quantity=str(fill_count),
                    average_fill_price=str(avg) if avg is not None else None,
                    fee_paid=str(fee) if fill_count > 0 else None,
                    status="FILLED" if fill_count == D(str(payload["count"])) else "CANCELED",
                    reason="RECONCILED_REST",
                    local_send_ns=now,
                    local_done_ns=now,
                    exchange_ts_ns=None,
                    request_latency_ms=0.0,
                    ack_latency_ms=None,
                    raw_request_status=200,
                    reconciled=True,
                )
        except Exception:
            return None
        return None

    async def place(self, payload: dict[str, Any]) -> ExecutionResult:
        client_id = str(payload["client_order_id"])
        sent = time.perf_counter_ns()
        try:
            response = await self.client.post("/portfolio/events/orders", json=payload)
        except (httpx.TimeoutException, TimeoutError, asyncio.TimeoutError):
            recovered = await self._reconcile_by_client_id(payload)
            if recovered is not None:
                return recovered
            done = time.perf_counter_ns()
            return ExecutionResult(
                venue="kalshi",
                client_order_id=client_id,
                filled_quantity="0",
                average_fill_price=None,
                fee_paid=None,
                status="UNKNOWN",
                reason="AMBIGUOUS_SUBMISSION_UNRESOLVED",
                local_send_ns=sent,
                local_done_ns=done,
                exchange_ts_ns=None,
                request_latency_ms=(done - sent) / 1_000_000,
                ack_latency_ms=None,
                raw_request_status=None,
                reconciled=False,
            )
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
                ack_latency_ms=(done - sent) / 1_000_000,
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
            ack_latency_ms=(done - sent) / 1_000_000,
            raw_request_status=status_code,
        )

    async def emergency_flatten(self, original_payload: dict[str, Any]) -> ExecutionResult:
        """Attempt to neutralize exactly one confirmed Kalshi fill with a marketable IOC."""
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


async def submit_dual_probe(
    gemini: GeminiTradingSession,
    kalshi: KalshiV2Executor,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    cli_live: bool = False,
) -> dict[str, Any]:
    """Submit two exact one-contract FOK legs concurrently after all hard gates."""
    if not live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi probes are disabled")
    recomputed = validate_exact_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
    )

    g_result, k_result = await asyncio.gather(
        gemini.place_fok(gemini_payload),
        kalshi.place(kalshi_payload),
    )
    if g_result.status == "UNKNOWN" or k_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_SUBMISSION",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    g_filled = D(g_result.filled_quantity) == D("1")
    k_filled = D(k_result.filled_quantity) == D("1")
    outcome = "BOTH_FILLED" if g_filled and k_filled else "NEITHER_FILLED" if not g_filled and not k_filled else "ONE_LEG_FILLED"
    report: dict[str, Any] = {
        "outcome": outcome,
        "recomputed_locked_profit_dollars": str(recomputed),
        "gemini": g_result.as_dict(),
        "kalshi": k_result.as_dict(),
        "flatten": None,
        "hard_halt_required": outcome == "ONE_LEG_FILLED",
    }
    if g_filled and not k_filled:
        report["flatten"] = (await gemini.emergency_flatten_market(gemini_payload)).as_dict()
    elif k_filled and not g_filled:
        report["flatten"] = (await kalshi.emergency_flatten(kalshi_payload)).as_dict()
    return report
