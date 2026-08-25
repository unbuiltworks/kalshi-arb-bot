"""Sports G/K q=1 transport hardening after the 2026-08-24 one-leg incident.

The prior concurrent transport could fill Gemini while Kalshi rejected an order with
market_not_found. This wrapper keeps all existing q=1/fractional-fill/economic guards
but adds an exact Kalshi market-tradeability preflight immediately before mutation.
If that preflight is not authoritative and tradeable, outcome is NEITHER_FILLED and
Gemini is never touched.

It also replaces the invalid Gemini MARKET emergency flatten with a prediction-market
SELL LIMIT+FOK at the current executable outcome bid, with exact displayed depth.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Mapping
import uuid

import src.gemini_kalshi_probe_transport_fractional_safe as base
from src.gemini_kalshi_live_probe_guard import live_probe_authorized, validate_exact_probe_payloads
from src.gemini_kalshi_live_shadow import gemini_book_ticker_once

D = Decimal
ZERO = D("0")
ONE = D("1")


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


async def _kalshi_market_preflight(kalshi: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ticker = str(payload.get("ticker") or "")
    if not ticker:
        return {"ok": False, "reason": "MISSING_TICKER"}
    try:
        response = await asyncio.wait_for(
            kalshi.client.get(f"/markets/{ticker}"),
            timeout=0.75,
        )
    except Exception as exc:
        return {"ok": False, "reason": f"PREFLIGHT_{type(exc).__name__}"}
    if int(response.status_code) != 200:
        return {"ok": False, "reason": f"PREFLIGHT_HTTP_{response.status_code}"}
    try:
        body = response.json()
    except Exception:
        return {"ok": False, "reason": "PREFLIGHT_BAD_JSON"}
    market = body.get("market") if isinstance(body, Mapping) else None
    if not isinstance(market, Mapping):
        return {"ok": False, "reason": "PREFLIGHT_MARKET_MISSING"}
    actual = str(market.get("ticker") or "")
    if actual != ticker:
        return {"ok": False, "reason": "PREFLIGHT_TICKER_MISMATCH"}
    status = str(market.get("status") or "").strip().lower()
    if status in {"closed", "settled", "finalized", "determined"}:
        return {"ok": False, "reason": f"PREFLIGHT_STATUS_{status.upper()}"}
    return {"ok": True, "reason": "OK", "status": status or None}


async def _flatten_gemini_limit_fok(gemini: Any, original: dict[str, Any], quantity: D):
    """Reduce exact known Gemini exposure with a valid prediction LIMIT+FOK SELL."""
    if quantity <= ZERO:
        raise ValueError("Gemini flatten quantity must be positive")
    book = await gemini_book_ticker_once(str(original["symbol"]), timeout_s=3.0)
    outcome = str(original.get("eventOutcome") or "").upper()
    if outcome == "YES":
        px = D(str(book.yes_bid))
        depth = D(str(book.yes_bid_size))
    elif outcome == "NO":
        px = ONE - D(str(book.yes_ask))
        depth = D(str(book.yes_ask_size))
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
            "gemini": _zero_result(
                "gemini", str(gemini_payload.get("clientOrderId") or ""),
                status="NOT_SUBMITTED", reason="KALSHI_MARKET_PREFLIGHT_BLOCK",
            ),
            "kalshi": _zero_result(
                "kalshi", str(kalshi_payload.get("client_order_id") or ""),
                status="NOT_SUBMITTED", reason=str(preflight.get("reason") or "KALSHI_MARKET_PREFLIGHT_BLOCK"),
            ),
            "flatten": None,
            "hard_halt_required": False,
            "kalshi_market_preflight": preflight,
        }

    # Patch only this call's emergency Gemini reduction primitive. The base transport
    # still owns fractional quantity accounting, ambiguity handling, and dual-FOK send.
    original_flatten = base._flatten_gemini_exact
    base._flatten_gemini_exact = _flatten_gemini_limit_fok
    try:
        result = await base.submit_dual_probe_fractional_safe(
            gemini,
            kalshi,
            gemini_payload,
            kalshi_payload,
            modeled_locked_profit=D(modeled_locked_profit),
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
            quote_skew_ms=int(quote_skew_ms),
            cli_live=cli_live,
        )
    finally:
        base._flatten_gemini_exact = original_flatten
    result = dict(result)
    result["kalshi_market_preflight"] = preflight
    return result
