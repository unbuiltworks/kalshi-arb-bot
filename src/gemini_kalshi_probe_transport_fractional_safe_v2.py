"""Fail-closed sequential G/K sports q=1 transport.

Incident addressed: a Gemini leg filled while the exact Kalshi market disappeared
between discovery and submission. This transport makes Kalshi authoritative first:

1. recompute the existing >=3c q=1 economics;
2. authenticated GET the exact Kalshi market and exact depth-1 orderbook;
3. send Kalshi FOK first;
4. only after an exact Kalshi q=1 fill, re-read Gemini exact top-of-book and require
   the original Gemini limit still has q1 executable depth;
5. send Gemini FOK;
6. if Gemini does not fill, flatten only the known Kalshi quantity;
7. if Gemini is ambiguous, reconcile from authoritative Gemini position delta before
   any flatten decision;
8. every Gemini emergency reduction is SELL LIMIT+FOK, never MARKET.

This is intentionally sequential. Price limits preserve the modeled floor; sequencing
trades a small amount of fill probability for elimination of the observed failure mode.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Mapping
import uuid

from src.gemini_kalshi_live_probe_guard import live_probe_authorized, validate_exact_probe_payloads
from src.gemini_kalshi_live_shadow import gemini_book_ticker_once
from src.gemini_kalshi_probe_transport import ExecutionResult
from src.gemini_prediction_auth import private_rest_request

D = Decimal
ZERO = D("0")
ONE = D("1")


def _d(value: Any) -> D:
    try:
        return D(str(value or "0"))
    except Exception:
        return ZERO


def _zero_result(venue: str, client_id: str, *, status: str, reason: str) -> dict[str, Any]:
    return {
        "venue": venue,
        "client_order_id": client_id,
        "filled_quantity": "0",
        "average_fill_price": None,
        "fee_paid": None,
        "status": status,
        "reason": reason,
        "local_send_ns": 0,
        "local_done_ns": 0,
        "exchange_ts_ns": None,
        "request_latency_ms": 0.0,
        "ack_latency_ms": None,
        "raw_request_status": None,
        "reconciled": False,
    }


def _result_dict(result: Any) -> dict[str, Any]:
    return result.as_dict() if hasattr(result, "as_dict") else dict(result)


async def _kalshi_market_preflight(kalshi: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ticker = str(payload.get("ticker") or "")
    if not ticker:
        return {"ok": False, "reason": "MISSING_TICKER"}
    try:
        market_response = await asyncio.wait_for(kalshi.client.get(f"/markets/{ticker}"), timeout=0.75)
        if int(market_response.status_code) != 200:
            return {"ok": False, "reason": f"MARKET_HTTP_{market_response.status_code}"}
        market_body = market_response.json()
        market = market_body.get("market") if isinstance(market_body, Mapping) else None
        if not isinstance(market, Mapping) or str(market.get("ticker") or "") != ticker:
            return {"ok": False, "reason": "MARKET_IDENTITY_MISMATCH"}
        status = str(market.get("status") or "").strip().lower()
        if status in {"closed", "settled", "finalized", "determined", "inactive"}:
            return {"ok": False, "reason": f"MARKET_STATUS_{status.upper()}"}

        book_response = await asyncio.wait_for(
            kalshi.client.get(f"/markets/{ticker}/orderbook", params={"depth": 1}),
            timeout=0.75,
        )
        if int(book_response.status_code) != 200:
            return {"ok": False, "reason": f"ORDERBOOK_HTTP_{book_response.status_code}"}
        book_body = book_response.json()
        book = book_body.get("orderbook_fp") or book_body.get("orderbook") or {}
        if not isinstance(book, Mapping):
            return {"ok": False, "reason": "ORDERBOOK_MISSING"}
        yes_levels = book.get("yes_dollars") or book.get("yes_dollars_fp") or []
        no_levels = book.get("no_dollars") or book.get("no_dollars_fp") or []
        # We need the side opposite the submitted contract side to cross a FOK.
        submitted_side = str(payload.get("side") or "")
        required = no_levels if submitted_side == "bid" else yes_levels if submitted_side == "ask" else []
        if not required:
            return {"ok": False, "reason": "ORDERBOOK_NO_EXECUTABLE_COUNTERSIDE"}
        return {"ok": True, "reason": "OK", "status": status or None}
    except Exception as exc:
        return {"ok": False, "reason": f"PREFLIGHT_{type(exc).__name__}"}


async def _gemini_position_qty(symbol: str, outcome: str) -> D:
    response = await private_rest_request(
        "POST", "/v1/prediction-markets/positions", params={}, timeout=3.0
    )
    response.raise_for_status()
    body = response.json()
    rows = body.get("positions") or [] if isinstance(body, Mapping) else []
    total = ZERO
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("symbol") or "").lower() != symbol.lower():
            continue
        if str(row.get("outcome") or "").upper() != outcome.upper():
            continue
        total += _d(row.get("totalQuantity"))
    return total


def _gemini_buy_actionable(book: Any, payload: dict[str, Any], quantity: D) -> tuple[bool, D, D]:
    outcome = str(payload.get("eventOutcome") or "").upper()
    limit = _d(payload.get("price"))
    if outcome == "YES":
        ask = D(str(book.yes_ask)); depth = D(str(book.yes_ask_size))
    elif outcome == "NO":
        ask = ONE - D(str(book.yes_bid)); depth = D(str(book.yes_bid_size))
    else:
        return False, ZERO, ZERO
    return bool(D("0.01") <= ask <= limit <= D("0.99") and depth >= quantity), ask, depth


async def _flatten_gemini_limit_fok(gemini: Any, original: dict[str, Any], quantity: D):
    if quantity <= ZERO:
        raise ValueError("Gemini flatten quantity must be positive")
    book = await gemini_book_ticker_once(str(original["symbol"]), timeout_s=2.0)
    outcome = str(original.get("eventOutcome") or "").upper()
    if outcome == "YES":
        px = D(str(book.yes_bid)); depth = D(str(book.yes_bid_size))
    elif outcome == "NO":
        px = ONE - D(str(book.yes_ask)); depth = D(str(book.yes_ask_size))
    else:
        raise ValueError("unsupported Gemini prediction outcome")
    if not (D("0.01") <= px <= D("0.99")) or depth < quantity:
        raise RuntimeError(f"no exact Gemini flatten depth outcome={outcome} price={px} depth={depth} qty={quantity}")
    payload = {
        "symbol": original["symbol"],
        "side": "SELL",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": str(px),
        "quantity": str(quantity),
        "eventOutcome": outcome,
        "clientOrderId": f"flatten-gq2-{uuid.uuid4().hex[:18]}",
    }
    return await gemini.place_fok(payload)


async def _flatten_kalshi_exact(kalshi: Any, original: dict[str, Any], quantity: D):
    original_side = str(original["side"])
    if original_side == "bid":
        side, price = "ask", "0.0100"
    elif original_side == "ask":
        side, price = "bid", "0.9900"
    else:
        raise ValueError("unsupported Kalshi side")
    payload = {
        "ticker": original["ticker"],
        "client_order_id": f"flatten-kq2-{uuid.uuid4().hex[:18]}",
        "side": side,
        "count": str(quantity),
        "price": price,
        "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": True,
        "subaccount": 0,
        "exchange_index": 0,
    }
    return await kalshi.place(payload)


async def submit_dual_probe_fractional_safe_v2(
    gemini: Any,
    kalshi: Any,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    cli_live: bool = False,
) -> dict[str, Any]:
    if not live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi probes are disabled")
    recomputed = validate_exact_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
    )

    preflight = await _kalshi_market_preflight(kalshi, kalshi_payload)
    if not bool(preflight.get("ok")):
        return {
            "outcome": "NEITHER_FILLED",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _zero_result("gemini", str(gemini_payload.get("clientOrderId") or ""), status="NOT_SUBMITTED", reason="KALSHI_MARKET_PREFLIGHT_BLOCK"),
            "kalshi": _zero_result("kalshi", str(kalshi_payload.get("client_order_id") or ""), status="NOT_SUBMITTED", reason=str(preflight.get("reason") or "KALSHI_MARKET_PREFLIGHT_BLOCK")),
            "flatten": None,
            "hard_halt_required": False,
            "kalshi_market_preflight": preflight,
        }

    # First mutation: Kalshi only. If Kalshi cannot fill, Gemini is never touched.
    k_result = await kalshi.place(kalshi_payload)
    kq = _d(k_result.filled_quantity)
    if str(k_result.status).upper() == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_KALSHI_FIRST_LEG",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _zero_result("gemini", str(gemini_payload.get("clientOrderId") or ""), status="NOT_SUBMITTED", reason="KALSHI_FIRST_LEG_AMBIGUOUS"),
            "kalshi": _result_dict(k_result),
            "flatten": None,
            "hard_halt_required": True,
            "kalshi_market_preflight": preflight,
        }
    if kq < ZERO or kq > ONE:
        return {
            "outcome": "INVALID_KALSHI_FILL_QUANTITY",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _zero_result("gemini", str(gemini_payload.get("clientOrderId") or ""), status="NOT_SUBMITTED", reason="INVALID_KALSHI_FILL_QUANTITY"),
            "kalshi": _result_dict(k_result),
            "flatten": None,
            "hard_halt_required": True,
            "kalshi_market_preflight": preflight,
        }
    if kq == ZERO:
        return {
            "outcome": "NEITHER_FILLED",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _zero_result("gemini", str(gemini_payload.get("clientOrderId") or ""), status="NOT_SUBMITTED", reason="KALSHI_FOK_ZERO_FILL"),
            "kalshi": _result_dict(k_result),
            "flatten": None,
            "hard_halt_required": False,
            "kalshi_market_preflight": preflight,
        }
    if kq != ONE:
        flatten_k = await _flatten_kalshi_exact(kalshi, kalshi_payload, kq)
        return {
            "outcome": "FRACTIONAL_KALSHI_FIRST_LEG",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _zero_result("gemini", str(gemini_payload.get("clientOrderId") or ""), status="NOT_SUBMITTED", reason="KALSHI_FRACTIONAL_FILL"),
            "kalshi": _result_dict(k_result),
            "flatten": {"kalshi": _result_dict(flatten_k)},
            "hard_halt_required": True,
            "kalshi_market_preflight": preflight,
        }

    # Kalshi is exactly filled. Refuse Gemini unless the original limit is still
    # executable right now; if not, immediately reduce the known Kalshi exposure.
    try:
        g_before = await _gemini_position_qty(str(gemini_payload["symbol"]), str(gemini_payload["eventOutcome"]))
        book = await gemini_book_ticker_once(str(gemini_payload["symbol"]), timeout_s=1.5)
        actionable, current_ask, current_depth = _gemini_buy_actionable(book, gemini_payload, ONE)
    except Exception as exc:
        flatten_k = await _flatten_kalshi_exact(kalshi, kalshi_payload, ONE)
        return {
            "outcome": "KALSHI_FILLED_GEMINI_REVALIDATION_FAILED",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _zero_result("gemini", str(gemini_payload.get("clientOrderId") or ""), status="NOT_SUBMITTED", reason=f"GEMINI_REVALIDATION_{type(exc).__name__}"),
            "kalshi": _result_dict(k_result),
            "flatten": {"kalshi": _result_dict(flatten_k)},
            "hard_halt_required": True,
            "kalshi_market_preflight": preflight,
        }
    if not actionable:
        flatten_k = await _flatten_kalshi_exact(kalshi, kalshi_payload, ONE)
        return {
            "outcome": "KALSHI_FILLED_GEMINI_LIMIT_LOST",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _zero_result("gemini", str(gemini_payload.get("clientOrderId") or ""), status="NOT_SUBMITTED", reason=f"GEMINI_LIMIT_LOST ask={current_ask} depth={current_depth}"),
            "kalshi": _result_dict(k_result),
            "flatten": {"kalshi": _result_dict(flatten_k)},
            "hard_halt_required": True,
            "kalshi_market_preflight": preflight,
        }

    g_result = await gemini.place_fok(gemini_payload)
    gq = _d(g_result.filled_quantity)
    if str(g_result.status).upper() == "UNKNOWN":
        try:
            g_after = await _gemini_position_qty(str(gemini_payload["symbol"]), str(gemini_payload["eventOutcome"]))
            delta = g_after - g_before
        except Exception:
            return {
                "outcome": "AMBIGUOUS_GEMINI_SECOND_LEG",
                "recomputed_locked_profit_dollars": str(recomputed),
                "gemini": _result_dict(g_result),
                "kalshi": _result_dict(k_result),
                "flatten": None,
                "hard_halt_required": True,
                "kalshi_market_preflight": preflight,
            }
        if delta == ONE:
            gq = ONE
            g_result = ExecutionResult(
                venue="gemini", client_order_id=str(gemini_payload["clientOrderId"]), filled_quantity="1",
                average_fill_price=None, fee_paid=None, status="FILLED", reason="RECONCILED_POSITION_DELTA",
                local_send_ns=g_result.local_send_ns, local_done_ns=g_result.local_done_ns,
                exchange_ts_ns=g_result.exchange_ts_ns, request_latency_ms=g_result.request_latency_ms,
                ack_latency_ms=g_result.ack_latency_ms, raw_request_status=g_result.raw_request_status, reconciled=True,
            )
        elif delta == ZERO:
            flatten_k = await _flatten_kalshi_exact(kalshi, kalshi_payload, ONE)
            return {
                "outcome": "ONE_LEG_FILLED",
                "recomputed_locked_profit_dollars": str(recomputed),
                "gemini": _result_dict(g_result),
                "kalshi": _result_dict(k_result),
                "flatten": {"kalshi": _result_dict(flatten_k)},
                "hard_halt_required": True,
                "kalshi_market_preflight": preflight,
            }
        else:
            flattened: dict[str, Any] = {}
            if delta > ZERO:
                flattened["gemini"] = _result_dict(await _flatten_gemini_limit_fok(gemini, gemini_payload, delta))
            flattened["kalshi"] = _result_dict(await _flatten_kalshi_exact(kalshi, kalshi_payload, ONE))
            return {
                "outcome": "FRACTIONAL_OR_INVALID_GEMINI_POSITION_DELTA",
                "recomputed_locked_profit_dollars": str(recomputed),
                "gemini": _result_dict(g_result),
                "kalshi": _result_dict(k_result),
                "flatten": flattened,
                "hard_halt_required": True,
                "kalshi_market_preflight": preflight,
            }

    if gq < ZERO or gq > ONE:
        return {
            "outcome": "INVALID_GEMINI_FILL_QUANTITY",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _result_dict(g_result),
            "kalshi": _result_dict(k_result),
            "flatten": None,
            "hard_halt_required": True,
            "kalshi_market_preflight": preflight,
        }
    if gq == ONE:
        return {
            "outcome": "BOTH_FILLED",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": _result_dict(g_result),
            "kalshi": _result_dict(k_result),
            "flatten": None,
            "hard_halt_required": False,
            "kalshi_market_preflight": preflight,
        }

    flattened: dict[str, Any] = {}
    if gq > ZERO:
        flattened["gemini"] = _result_dict(await _flatten_gemini_limit_fok(gemini, gemini_payload, gq))
    flattened["kalshi"] = _result_dict(await _flatten_kalshi_exact(kalshi, kalshi_payload, ONE))
    return {
        "outcome": "FRACTIONAL_GEMINI_SECOND_LEG" if gq > ZERO else "ONE_LEG_FILLED",
        "recomputed_locked_profit_dollars": str(recomputed),
        "gemini": _result_dict(g_result),
        "kalshi": _result_dict(k_result),
        "flatten": flattened,
        "hard_halt_required": True,
        "kalshi_market_preflight": preflight,
    }
