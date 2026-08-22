"""Fail-closed Gemini Prediction REST FOK execution fallback.

The live crypto lane prefers its persistent authenticated trading WebSocket. Gemini's
current Prediction REST API also supports limit orders with
``timeInForce=fill-or-kill``. This module is a bounded fallback for q=1 only when the
private trading WebSocket is unavailable.

Safety rules:
- primary execution remains Gemini first;
- only a synchronous HTTP 201 response proving filledQuantity == requested quantity
  and status=filled is treated as a fill;
- a synchronous documented client error is a confirmed zero-fill rejection;
- HTTP 5xx, transport timeout/error, malformed responses, partial fills, or any
  unexpected FOK state are UNKNOWN (submission ambiguity), which the staged executor
  hard-halts;
- the REST request schema has no caller-supplied clientOrderId, so ambiguous requests
  are deliberately NOT guessed/reconciled by similarity;
- emergency flatten is q=1 SELL LIMIT FOK at a deliberately aggressive $0.01 floor,
  preserving an explicit worst-price bound rather than using an unbounded market
  order;
- fees are computed from Gemini's published deterministic Prediction taker-fee formula
  using the confirmed average execution price because the REST order response does not
  expose a fee field.

No market/rule/freshness/skew/profit/boundary gate is changed here.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import time
from typing import Any

import httpx

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_probe_transport import ExecutionResult
from src.gemini_prediction_auth import private_rest_request

D = Decimal
ORDER_PATH = "/v1/prediction-markets/order"
CONFIRMED_REJECT_HTTP = {400, 401, 403, 404, 409, 422, 429}
AMBIGUOUS_HTTP = {500, 502, 503, 504}
QTY = D("1")
EMERGENCY_SELL_LIMIT = D("0.01")


def _result(
    *,
    client_id: str,
    sent_ns: int,
    status: str,
    filled_quantity: D = D("0"),
    average_fill_price: D | None = None,
    reason: str | None = None,
    raw_status: int | None = None,
    reconciled: bool = False,
) -> ExecutionResult:
    done = time.perf_counter_ns()
    fee: D | None = None
    if filled_quantity > 0 and average_fill_price is not None:
        fee = gemini_predictions_taker_fee(average_fill_price, filled_quantity)
    return ExecutionResult(
        venue="gemini",
        client_order_id=client_id,
        filled_quantity=str(filled_quantity),
        average_fill_price=str(average_fill_price) if average_fill_price is not None else None,
        fee_paid=str(fee) if fee is not None else None,
        status=status,
        reason=reason,
        local_send_ns=sent_ns,
        local_done_ns=done,
        exchange_ts_ns=None,
        request_latency_ms=(done - sent_ns) / 1_000_000,
        ack_latency_ms=(done - sent_ns) / 1_000_000 if raw_status is not None else None,
        raw_request_status=raw_status,
        reconciled=reconciled,
    )


def _rest_payload(params: dict[str, Any], *, flatten: bool = False) -> dict[str, Any]:
    quantity = D(str(params.get("quantity") or "0"))
    if quantity != QTY:
        raise ValueError("Gemini REST fallback is q=1 only")
    outcome = str(params.get("eventOutcome") or "").strip().lower()
    if outcome not in {"yes", "no"}:
        raise ValueError("Gemini REST fallback requires YES/NO eventOutcome")
    symbol = str(params.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("Gemini REST fallback requires symbol")

    if flatten:
        side = "sell"
        price = EMERGENCY_SELL_LIMIT
    else:
        if str(params.get("side") or "").upper() != "BUY":
            raise ValueError("Gemini REST primary fallback supports BUY only")
        if str(params.get("type") or "").upper() != "LIMIT":
            raise ValueError("Gemini REST primary fallback supports LIMIT only")
        if str(params.get("timeInForce") or "").upper() != "FOK":
            raise ValueError("Gemini REST primary fallback requires FOK")
        side = "buy"
        price = D(str(params.get("price")))
        if price <= 0 or price >= 1:
            raise ValueError("Gemini REST fallback price outside (0,1)")

    return {
        "symbol": symbol,
        "orderType": "limit",
        "side": side,
        "quantity": "1",
        "price": str(price),
        "outcome": outcome,
        "timeInForce": "fill-or-kill",
    }


async def _submit(params: dict[str, Any], *, flatten: bool, timeout: float) -> ExecutionResult:
    client_id = str(params.get("clientOrderId") or "")
    sent = time.perf_counter_ns()
    try:
        payload = _rest_payload(params, flatten=flatten)
    except Exception as exc:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="UNKNOWN",
            reason=f"REST_FOK_LOCAL_VALIDATION:{type(exc).__name__}:{str(exc)[:120]}",
        )

    try:
        response = await private_rest_request(
            "POST",
            ORDER_PATH,
            params=payload,
            timeout=timeout,
        )
    except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError) as exc:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="UNKNOWN",
            reason=f"REST_FOK_AMBIGUOUS_TIMEOUT:{type(exc).__name__}",
        )
    except Exception as exc:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="UNKNOWN",
            reason=f"REST_FOK_AMBIGUOUS_TRANSPORT:{type(exc).__name__}:{str(exc)[:120]}",
        )

    code = int(response.status_code)
    if code in CONFIRMED_REJECT_HTTP:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="REJECTED",
            reason=f"REST_FOK_HTTP_{code}",
            raw_status=code,
        )
    if code in AMBIGUOUS_HTTP or code != 201:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="UNKNOWN",
            reason=f"REST_FOK_AMBIGUOUS_HTTP_{code}",
            raw_status=code,
        )

    try:
        row = response.json()
        status = str(row.get("status") or "").lower()
        filled = D(str(row.get("filledQuantity") or "0"))
        remaining = D(str(row.get("remainingQuantity") or "0"))
        qty = D(str(row.get("quantity") or payload["quantity"]))
        avg_raw = row.get("avgExecutionPrice")
        avg = D(str(avg_raw)) if avg_raw is not None else None
    except Exception as exc:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="UNKNOWN",
            reason=f"REST_FOK_MALFORMED_201:{type(exc).__name__}",
            raw_status=code,
        )

    if qty != QTY:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="UNKNOWN",
            reason=f"REST_FOK_UNEXPECTED_QUANTITY:{qty}",
            raw_status=code,
        )
    if status == "filled" and filled == QTY and remaining == 0 and avg is not None:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="FILLED",
            filled_quantity=filled,
            average_fill_price=avg,
            reason="REST_FOK_CONFIRMED_FULL_FILL",
            raw_status=code,
        )
    if status == "cancelled" and filled == 0:
        return _result(
            client_id=client_id,
            sent_ns=sent,
            status="CANCELED",
            reason="REST_FOK_CONFIRMED_ZERO_FILL",
            raw_status=code,
        )

    # FOK should never remain open or partially fill. Treat any such response as
    # submission ambiguity rather than guessing about exposure.
    return _result(
        client_id=client_id,
        sent_ns=sent,
        status="UNKNOWN",
        filled_quantity=filled,
        average_fill_price=avg,
        reason=f"REST_FOK_UNEXPECTED_TERMINAL_STATE:{status}:remaining={remaining}",
        raw_status=code,
    )


async def place_primary_fok(params: dict[str, Any], *, timeout: float = 2.0) -> ExecutionResult:
    return await _submit(params, flatten=False, timeout=timeout)


async def emergency_flatten_fok(
    original_params: dict[str, Any], *, timeout: float = 2.0
) -> ExecutionResult:
    return await _submit(original_params, flatten=True, timeout=timeout)
