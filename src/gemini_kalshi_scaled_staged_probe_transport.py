"""Bounded q1-q5 Gemini-first transport for the scale canary.

This is a separate path from the proven q=1 transport.  It requires exact scaled
payload economics before any send, optionally spends at most two cents of excess edge
on the Kalshi hedge while preserving a 3c per-contract floor, and flattens the exact
known unhedged Gemini quantity on deterministic partial/zero hedge failures.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.gemini_kalshi_live_probe_guard import live_probe_authorized
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor
from src.gemini_kalshi_scaled_gemini_flatten import emergency_flatten_exact_quantity
from src.gemini_kalshi_scaled_hedge_cushion import best_scaled_fee_aware_kalshi_hedge
from src.gemini_kalshi_scaled_probe_guard import (
    DEFAULT_MIN_PROFIT_PER_CONTRACT,
    validate_scaled_probe_payloads,
)

D = Decimal


def _filled(result: Any) -> D:
    return D(str(getattr(result, "filled_quantity", "0") or "0"))


async def submit_scaled_gemini_first_probe(
    gemini: GeminiTradingSession,
    kalshi: KalshiV2Executor,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    required_quantity: Decimal = D("5"),
    min_profit_per_contract: Decimal = DEFAULT_MIN_PROFIT_PER_CONTRACT,
    max_cushion_cents: int = 2,
    use_hedge_cushion: bool = True,
    cli_live: bool = False,
) -> dict[str, Any]:
    """Submit exact q first on Gemini, then hedge only after a terminal full fill."""
    if not live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi scaled probes are disabled")

    q = D(required_quantity)
    original_recomputed = validate_scaled_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
        required_quantity=q,
        min_profit_per_contract=D(min_profit_per_contract),
    )

    hedge_payload = dict(kalshi_payload)
    hedge_recomputed = original_recomputed
    cushion_meta: dict[str, Any] = {
        "enabled": False,
        "cushion_cents": 0,
        "original_yes_price": str(kalshi_payload.get("price")),
        "cushioned_yes_price": str(kalshi_payload.get("price")),
        "original_locked_profit": str(original_recomputed),
        "cushioned_locked_profit": str(original_recomputed),
    }
    if use_hedge_cushion:
        cushion = best_scaled_fee_aware_kalshi_hedge(
            gemini_payload,
            kalshi_payload,
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
            min_profit_per_contract=D(min_profit_per_contract),
            max_cushion_cents=int(max_cushion_cents),
        )
        hedge_payload = dict(cushion.payload)
        hedge_recomputed = validate_scaled_probe_payloads(
            gemini_payload,
            hedge_payload,
            modeled_locked_profit=cushion.cushioned_locked_profit,
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
            quote_skew_ms=int(quote_skew_ms),
            required_quantity=q,
            min_profit_per_contract=D(min_profit_per_contract),
        )
        cushion_meta = {
            "enabled": True,
            "cushion_cents": cushion.cushion_cents,
            "original_yes_price": str(cushion.original_yes_price),
            "cushioned_yes_price": str(cushion.cushioned_yes_price),
            "original_locked_profit": str(cushion.original_locked_profit),
            "cushioned_locked_profit": str(cushion.cushioned_locked_profit),
            "total_profit_floor": str(cushion.total_profit_floor),
        }

    # Everything above must succeed before the fragile Gemini leg is allowed to send.
    g_result = await gemini.place_fok(gemini_payload)
    if g_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_PRIMARY_SUBMISSION",
            "execution_policy": "GEMINI_FIRST_SCALED_TERMINAL_THEN_KALSHI",
            "required_quantity": str(q),
            "original_recomputed_locked_profit_dollars": str(original_recomputed),
            "hedge_recomputed_locked_profit_dollars": str(hedge_recomputed),
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(),
            "kalshi": None,
            "flatten": None,
            "hard_halt_required": True,
        }

    g_filled = _filled(g_result)
    if g_filled != q:
        flatten = None
        hard_halt = g_filled > 0
        if g_filled > 0:
            flatten_result = await emergency_flatten_exact_quantity(
                gemini,
                gemini_payload,
                quantity=g_filled,
            )
            flatten = flatten_result.as_dict()
        return {
            "outcome": "PRIMARY_PARTIAL_FILL" if g_filled > 0 else "PRIMARY_NOT_FILLED",
            "execution_policy": "GEMINI_FIRST_SCALED_TERMINAL_THEN_KALSHI",
            "required_quantity": str(q),
            "original_recomputed_locked_profit_dollars": str(original_recomputed),
            "hedge_recomputed_locked_profit_dollars": str(hedge_recomputed),
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(),
            "kalshi": None,
            "flatten": flatten,
            "hard_halt_required": hard_halt,
        }

    k_result = await kalshi.place(hedge_payload)
    if k_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_HEDGE_SUBMISSION",
            "execution_policy": "GEMINI_FIRST_SCALED_TERMINAL_THEN_KALSHI",
            "required_quantity": str(q),
            "original_recomputed_locked_profit_dollars": str(original_recomputed),
            "hedge_recomputed_locked_profit_dollars": str(hedge_recomputed),
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    k_filled = _filled(k_result)
    if k_filled == q:
        return {
            "outcome": "BOTH_FILLED",
            "execution_policy": "GEMINI_FIRST_SCALED_TERMINAL_THEN_KALSHI",
            "required_quantity": str(q),
            "original_recomputed_locked_profit_dollars": str(original_recomputed),
            "hedge_recomputed_locked_profit_dollars": str(hedge_recomputed),
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    if k_filled < 0 or k_filled > q:
        return {
            "outcome": "INVALID_HEDGE_FILL_QUANTITY",
            "execution_policy": "GEMINI_FIRST_SCALED_TERMINAL_THEN_KALSHI",
            "required_quantity": str(q),
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    unhedged = q - k_filled
    flatten = None
    if unhedged > 0:
        flatten_result = await emergency_flatten_exact_quantity(
            gemini,
            gemini_payload,
            quantity=unhedged,
        )
        flatten = flatten_result.as_dict()
    return {
        "outcome": "HEDGE_PARTIAL_FILL" if k_filled > 0 else "HEDGE_NOT_FILLED",
        "execution_policy": "GEMINI_FIRST_SCALED_TERMINAL_THEN_KALSHI",
        "required_quantity": str(q),
        "unhedged_quantity_before_flatten": str(unhedged),
        "original_recomputed_locked_profit_dollars": str(original_recomputed),
        "hedge_recomputed_locked_profit_dollars": str(hedge_recomputed),
        "hedge_cushion": cushion_meta,
        "gemini": g_result.as_dict(),
        "kalshi": k_result.as_dict(),
        "flatten": flatten,
        "hard_halt_required": True,
    }
