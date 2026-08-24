"""Exact-target fractional-fill-safe transport for future q=5 G/K probes.

The production q=1 transport is intentionally untouched.  This transport accepts
only payloads validated by ``gemini_kalshi_graduated_probe_guard``.  Success means
both venues report exactly the authorized target quantity.  Any known nonzero
partial/mismatched fill below target is flattened at the exact reported quantity and
requires a hard halt.  Ambiguous or impossible (>target) reports hard-halt without
guessing position size.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from src.gemini_kalshi_graduated_probe_guard import validate_exact_graduated_payloads
from src.gemini_kalshi_live_probe_guard import live_probe_authorized
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor
from src.gemini_kalshi_probe_transport_fractional_safe import (
    _flatten_gemini_exact,
    _flatten_kalshi_exact,
)
from src.sports_dynamic_quantity import QuantitySafetyGates

D = Decimal
ZERO = D("0")


async def submit_dual_probe_quantity_safe(
    gemini: GeminiTradingSession,
    kalshi: KalshiV2Executor,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    authorized_max_quantity: int,
    gates: QuantitySafetyGates,
    cli_live: bool = False,
) -> dict[str, Any]:
    if not live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi probes are disabled")

    target = D(str(gemini_payload.get("quantity")))
    recomputed = validate_exact_graduated_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
        authorized_max_quantity=authorized_max_quantity,
        gates=gates,
    )

    g_result, k_result = await asyncio.gather(
        gemini.place_fok(gemini_payload),
        kalshi.place(kalshi_payload),
    )
    if g_result.status == "UNKNOWN" or k_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_SUBMISSION",
            "target_quantity": str(target),
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    gq = D(str(g_result.filled_quantity))
    kq = D(str(k_result.filled_quantity))
    if gq < ZERO or gq > target or kq < ZERO or kq > target:
        return {
            "outcome": "INVALID_FILL_QUANTITY",
            "target_quantity": str(target),
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    if gq == target and kq == target:
        return {
            "outcome": "BOTH_FILLED",
            "target_quantity": str(target),
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    if gq == ZERO and kq == ZERO:
        return {
            "outcome": "NEITHER_FILLED",
            "target_quantity": str(target),
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    flattened: dict[str, Any] = {}
    if gq > ZERO:
        flattened["gemini"] = (
            await _flatten_gemini_exact(gemini, gemini_payload, gq)
        ).as_dict()
    if kq > ZERO:
        flattened["kalshi"] = (
            await _flatten_kalshi_exact(kalshi, kalshi_payload, kq)
        ).as_dict()

    if gq == target or kq == target:
        outcome = "ONE_OR_MISMATCHED_LEG_FILLED"
    else:
        outcome = "PARTIAL_FILL"
    return {
        "outcome": outcome,
        "target_quantity": str(target),
        "recomputed_locked_profit_dollars": str(recomputed),
        "gemini": g_result.as_dict(),
        "kalshi": k_result.as_dict(),
        "flatten": flattened,
        "hard_halt_required": True,
    }
