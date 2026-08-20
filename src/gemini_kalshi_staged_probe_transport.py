"""Execution-reliability transport for the Gemini/Kalshi 15m canary.

The original probe transport submits both FOK legs concurrently. That is fast, but it
can create a one-leg Kalshi fill when Gemini's FOK disappears during the request race.
This module preserves every existing authorization/economics gate while changing only
submission ordering for the bounded live canary:

1. Gemini FOK is the primary/fragile leg and must terminally fill first.
2. If Gemini does not fill, Kalshi is never submitted and the outcome is a benign miss.
3. If Gemini fills, submit the prevalidated Kalshi FOK immediately.
4. Optionally, and only when explicitly enabled by the caller, precompute a more
   aggressive fee-aware Kalshi limit that still guarantees the existing >=3c modeled
   after-fee floor. This computation and validation happen BEFORE Gemini is sent.
5. If the Kalshi hedge fails, flatten Gemini and hard-halt exactly as before.
6. Any unresolved submission ambiguity hard-halts fail-closed.

No quantity, settlement, freshness, skew, boundary, or minimum-profit threshold is
relaxed. Hedge cushioning is disabled by default.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.gemini_kalshi_hedge_cushion import best_fee_aware_kalshi_hedge
from src.gemini_kalshi_live_probe_guard import (
    DEFAULT_MIN_LOCKED_PROFIT,
    live_probe_authorized,
    validate_exact_probe_payloads,
)
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor

D = Decimal


def _cushion_metadata(*, enabled: bool, cushion: Any | None = None) -> dict[str, Any]:
    if cushion is None:
        return {
            "enabled": bool(enabled),
            "applied": False,
            "cushion_cents": 0,
            "original_kalshi_yes_limit": None,
            "cushioned_kalshi_yes_limit": None,
            "original_locked_profit_dollars": None,
            "cushioned_locked_profit_dollars": None,
            "profit_floor_dollars": str(DEFAULT_MIN_LOCKED_PROFIT),
        }
    return {
        "enabled": bool(enabled),
        "applied": bool(enabled and cushion.has_cushion),
        "cushion_cents": int(cushion.cushion_cents),
        "original_kalshi_yes_limit": str(cushion.original_yes_price),
        "cushioned_kalshi_yes_limit": str(cushion.cushioned_yes_price),
        "original_locked_profit_dollars": str(cushion.original_locked_profit),
        "cushioned_locked_profit_dollars": str(cushion.cushioned_locked_profit),
        "profit_floor_dollars": str(cushion.profit_floor),
    }


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
    kalshi_hedge_cushion_enabled: bool = False,
) -> dict[str, Any]:
    """Submit Gemini first, then the prevalidated Kalshi hedge after a confirmed fill.

    When ``kalshi_hedge_cushion_enabled`` is true, the alternate Kalshi payload is
    computed and fully validated before the Gemini submission. The cushion uses the
    original Gemini LIMIT price, not a hoped-for better fill, so its >=3c guarantee is
    conservative. Any cushion-preparation error therefore occurs while both legs are
    still unsent.
    """
    if not live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi probes are disabled")

    original_recomputed = validate_exact_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
    )

    kalshi_to_send = dict(kalshi_payload)
    effective_recomputed = original_recomputed
    cushion = None
    execution_policy = "GEMINI_FIRST_TERMINAL_THEN_KALSHI"

    if kalshi_hedge_cushion_enabled:
        cushion = best_fee_aware_kalshi_hedge(
            gemini_payload,
            kalshi_payload,
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
            profit_floor=DEFAULT_MIN_LOCKED_PROFIT,
        )
        kalshi_to_send = dict(cushion.payload)
        effective_recomputed = validate_exact_probe_payloads(
            gemini_payload,
            kalshi_to_send,
            modeled_locked_profit=D(cushion.cushioned_locked_profit),
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
            quote_skew_ms=int(quote_skew_ms),
        )
        execution_policy = "GEMINI_FIRST_TERMINAL_THEN_KALSHI_FEE_AWARE_CUSHION"

    cushion_meta = _cushion_metadata(
        enabled=kalshi_hedge_cushion_enabled,
        cushion=cushion,
    )

    g_result = await gemini.place_fok(gemini_payload)
    if g_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_PRIMARY_SUBMISSION",
            "execution_policy": execution_policy,
            "recomputed_locked_profit_dollars": str(effective_recomputed),
            "original_recomputed_locked_profit_dollars": str(original_recomputed),
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(),
            "kalshi": None,
            "flatten": None,
            "hard_halt_required": True,
        }

    g_filled = D(g_result.filled_quantity) == D("1")
    if not g_filled:
        return {
            "outcome": "PRIMARY_NOT_FILLED",
            "execution_policy": execution_policy,
            "recomputed_locked_profit_dollars": str(effective_recomputed),
            "original_recomputed_locked_profit_dollars": str(original_recomputed),
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(),
            "kalshi": None,
            "flatten": None,
            "hard_halt_required": False,
        }

    k_result = await kalshi.place(kalshi_to_send)
    if k_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_HEDGE_SUBMISSION",
            "execution_policy": execution_policy,
            "recomputed_locked_profit_dollars": str(effective_recomputed),
            "original_recomputed_locked_profit_dollars": str(original_recomputed),
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    k_filled = D(k_result.filled_quantity) == D("1")
    if k_filled:
        return {
            "outcome": "BOTH_FILLED",
            "execution_policy": execution_policy,
            "recomputed_locked_profit_dollars": str(effective_recomputed),
            "original_recomputed_locked_profit_dollars": str(original_recomputed),
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    flatten = await gemini.emergency_flatten_market(gemini_payload)
    return {
        "outcome": "ONE_LEG_FILLED",
        "execution_policy": execution_policy,
        "recomputed_locked_profit_dollars": str(effective_recomputed),
        "original_recomputed_locked_profit_dollars": str(original_recomputed),
        "hedge_cushion": cushion_meta,
        "gemini": g_result.as_dict(),
        "kalshi": k_result.as_dict(),
        "flatten": flatten.as_dict(),
        "hard_halt_required": True,
    }
