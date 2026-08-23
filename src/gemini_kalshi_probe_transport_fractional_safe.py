"""Fractional-fill-safe wrapper for the live Gemini/Kalshi q=1 transport.

Gemini prediction contracts can report sub-contract fills. The legacy q=1 dual
transport classified any quantity other than exactly 1 as "not filled", which can
silently leave exposure if a FOK-style order returns a fractional fill. This module
keeps the same authorization/economic gates but treats every nonzero non-1 fill as
a hard-halt condition and flattens the *exact known filled quantity* on each venue.

Unknown/ambiguous submissions still halt without guessing at position size.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import uuid
from typing import Any

from src.gemini_kalshi_live_probe_guard import live_probe_authorized, validate_exact_probe_payloads
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor

D = Decimal
ZERO = D("0")
ONE = D("1")


async def _flatten_gemini_exact(
    gemini: GeminiTradingSession,
    original: dict[str, Any],
    quantity: D,
):
    payload = {
        "symbol": original["symbol"],
        "side": "SELL",
        "type": "MARKET",
        "timeInForce": "IOC",
        "quantity": str(quantity),
        "eventOutcome": original["eventOutcome"],
        "clientOrderId": f"flatten-gq-{uuid.uuid4().hex[:20]}",
    }
    return await gemini.place_fok(payload)


async def _flatten_kalshi_exact(
    kalshi: KalshiV2Executor,
    original: dict[str, Any],
    quantity: D,
):
    original_side = str(original["side"])
    if original_side == "bid":
        side, price = "ask", "0.0100"
    elif original_side == "ask":
        side, price = "bid", "0.9900"
    else:
        raise ValueError("unsupported Kalshi side")
    payload = {
        "ticker": original["ticker"],
        "client_order_id": f"flatten-kq-{uuid.uuid4().hex[:20]}",
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


async def submit_dual_probe_fractional_safe(
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

    gq = D(str(g_result.filled_quantity))
    kq = D(str(k_result.filled_quantity))
    if gq < ZERO or gq > ONE or kq < ZERO or kq > ONE:
        return {
            "outcome": "INVALID_FILL_QUANTITY",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    fractional = (gq not in {ZERO, ONE}) or (kq not in {ZERO, ONE})
    if fractional:
        flattened: dict[str, Any] = {}
        # Quantities are known, so flatten only the exact known exposure. If any
        # flatten is incomplete/unknown the caller remains hard-halted.
        if gq > ZERO:
            flattened["gemini"] = (await _flatten_gemini_exact(gemini, gemini_payload, gq)).as_dict()
        if kq > ZERO:
            flattened["kalshi"] = (await _flatten_kalshi_exact(kalshi, kalshi_payload, kq)).as_dict()
        return {
            "outcome": "FRACTIONAL_FILL",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": flattened,
            "hard_halt_required": True,
        }

    g_filled = gq == ONE
    k_filled = kq == ONE
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
        report["flatten"] = {"gemini": (await _flatten_gemini_exact(gemini, gemini_payload, ONE)).as_dict()}
    elif k_filled and not g_filled:
        report["flatten"] = {"kalshi": (await _flatten_kalshi_exact(kalshi, kalshi_payload, ONE)).as_dict()}
    return report
