"""Execution-reliability transport for the Gemini/Kalshi 15m canary.

The original probe transport submits both FOK legs concurrently. That is fast, but it
can create a one-leg Kalshi fill when Gemini's FOK disappears during the request race.
This module preserves every existing authorization/economics gate while changing only
submission ordering for the bounded live canary:

1. Gemini FOK is the primary/fragile leg and must terminally fill first.
2. If Gemini does not fill, Kalshi is never submitted and the outcome is a benign miss.
3. If Gemini fills, submit the exact prevalidated Kalshi FOK immediately.
4. If the Kalshi hedge fails, flatten Gemini and hard-halt exactly as before.
5. Any unresolved submission ambiguity hard-halts fail-closed.

No price, quantity, settlement, freshness, skew, depth, or profit threshold is relaxed.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.gemini_kalshi_live_probe_guard import (
    live_probe_authorized,
    validate_exact_probe_payloads,
)
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor

D = Decimal


async def submit_gemini_first_probe(
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
    """Submit the fragile Gemini FOK first, then hedge on Kalshi only after a fill."""
    if not live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi probes are disabled")

    recomputed = validate_exact_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
    )

    g_result = await gemini.place_fok(gemini_payload)
    if g_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_PRIMARY_SUBMISSION",
            "execution_policy": "GEMINI_FIRST_TERMINAL_THEN_KALSHI",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": None,
            "flatten": None,
            "hard_halt_required": True,
        }

    g_filled = D(g_result.filled_quantity) == D("1")
    if not g_filled:
        return {
            "outcome": "PRIMARY_NOT_FILLED",
            "execution_policy": "GEMINI_FIRST_TERMINAL_THEN_KALSHI",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": None,
            "flatten": None,
            "hard_halt_required": False,
        }

    k_result = await kalshi.place(kalshi_payload)
    if k_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_HEDGE_SUBMISSION",
            "execution_policy": "GEMINI_FIRST_TERMINAL_THEN_KALSHI",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    k_filled = D(k_result.filled_quantity) == D("1")
    if k_filled:
        return {
            "outcome": "BOTH_FILLED",
            "execution_policy": "GEMINI_FIRST_TERMINAL_THEN_KALSHI",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    flatten = await gemini.emergency_flatten_market(gemini_payload)
    return {
        "outcome": "ONE_LEG_FILLED",
        "execution_policy": "GEMINI_FIRST_TERMINAL_THEN_KALSHI",
        "recomputed_locked_profit_dollars": str(recomputed),
        "gemini": g_result.as_dict(),
        "kalshi": k_result.as_dict(),
        "flatten": flatten.as_dict(),
        "hard_halt_required": True,
    }
