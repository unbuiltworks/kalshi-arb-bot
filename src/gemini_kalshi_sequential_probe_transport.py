"""Process-local Kalshi-first q=1 hedge transport.

This module is intentionally NOT a new authorization path. It reuses the existing
live-probe authorization and exact payload economics validator, but changes the
submission order so Gemini can never fill unless Kalshi has already confirmed its
FOK fill.

Safety properties:
- exact pre-send payload validation remains unchanged;
- Kalshi FOK is the first leg;
- if Kalshi does not fill, Gemini is never submitted;
- after a confirmed Kalshi fill, Gemini is submitted at the exact validated limit;
- a confirmed Gemini miss triggers immediate Kalshi flatten + hard halt;
- unresolved Gemini ambiguity never guesses or auto-flattens because Gemini may have
  filled despite the missing acknowledgement; the process hard-halts for reconciliation;
- any Kalshi ambiguity hard-halts before Gemini submission.

This reduces the specific Gemini-filled/Kalshi-zero orphan path observed in production.
It cannot make cross-venue execution atomic and therefore must remain bounded until
live reliability is re-proven.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src import gemini_kalshi_probe_transport as base
from src.gemini_kalshi_live_probe_guard import validate_exact_probe_payloads

D = Decimal


async def submit_kalshi_first_probe(
    gemini: base.GeminiTradingSession,
    kalshi: base.KalshiV2Executor,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    cli_live: bool = False,
) -> dict[str, Any]:
    """Execute q=1 with Kalshi confirmed before any Gemini submission."""
    if not base.live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi probes are disabled")

    recomputed = validate_exact_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
    )

    k_result = await kalshi.place(kalshi_payload)
    if k_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_FIRST_LEG",
            "execution_order": "KALSHI_THEN_GEMINI",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": None,
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    k_filled = D(k_result.filled_quantity) == D("1")
    if not k_filled:
        return {
            "outcome": "NEITHER_FILLED",
            "execution_order": "KALSHI_THEN_GEMINI",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": None,
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    g_result = await gemini.place_fok(gemini_payload)
    if g_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_SECOND_LEG",
            "execution_order": "KALSHI_THEN_GEMINI",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    g_filled = D(g_result.filled_quantity) == D("1")
    if g_filled:
        return {
            "outcome": "BOTH_FILLED",
            "execution_order": "KALSHI_THEN_GEMINI",
            "recomputed_locked_profit_dollars": str(recomputed),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    flatten = await kalshi.emergency_flatten(kalshi_payload)
    flatten_filled = D(flatten.filled_quantity) == D("1")
    return {
        "outcome": "ONE_LEG_FILLED_FLATTENED" if flatten_filled else "ONE_LEG_FILLED_FLATTEN_FAILED",
        "execution_order": "KALSHI_THEN_GEMINI",
        "recomputed_locked_profit_dollars": str(recomputed),
        "gemini": g_result.as_dict(),
        "kalshi": k_result.as_dict(),
        "flatten": flatten.as_dict(),
        "hard_halt_required": True,
    }
