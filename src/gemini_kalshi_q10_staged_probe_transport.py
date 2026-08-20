"""Test-only q=10 Gemini-first transport core for future scale promotion.

There is intentionally no q=10 campaign runner or arm path in this module.  A caller
would need existing live authorization *and* explicit per-venue capital reservations
covering the exact cushioned outbound pair before Gemini can send.  Exact q=10 fees,
>=3c/contract economics, <=50ms skew, at-most-2c Kalshi cushion and exact-known-quantity
Gemini emergency exits are enforced before/after submission as appropriate.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.gemini_kalshi_live_probe_guard import live_probe_authorized
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor
from src.gemini_kalshi_q10_gemini_flatten import emergency_flatten_q10_exact_quantity
from src.gemini_kalshi_q10_hedge_cushion import best_q10_fee_aware_kalshi_hedge
from src.gemini_kalshi_q10_probe_guard import (
    MIN_PROFIT_PER_CONTRACT,
    Q10,
    q10_reservation_requirements,
    validate_q10_probe_payloads,
)

D = Decimal


def _filled(result: Any) -> Decimal:
    return D(str(getattr(result, "filled_quantity", "0") or "0"))


def _reservation_snapshot(reserved_cash_by_venue: dict[str, Decimal]) -> dict[str, Decimal]:
    if not isinstance(reserved_cash_by_venue, dict):
        raise ValueError("q10 transport requires explicit per-venue capital reservations")
    return {
        "gemini": D(str(reserved_cash_by_venue.get("gemini", "0"))),
        "kalshi": D(str(reserved_cash_by_venue.get("kalshi", "0"))),
    }


def _assert_reservations_cover(required: dict[str, Decimal], reserved: dict[str, Decimal]) -> None:
    for venue in ("gemini", "kalshi"):
        if reserved[venue] < required[venue]:
            raise ValueError(
                f"q10 {venue} reservation insufficient reserved={reserved[venue]} required={required[venue]}"
            )


async def submit_q10_gemini_first_probe(
    gemini: GeminiTradingSession,
    kalshi: KalshiV2Executor,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    reserved_cash_by_venue: dict[str, Decimal],
    min_profit_per_contract: Decimal = MIN_PROFIT_PER_CONTRACT,
    max_cushion_cents: int = 2,
    cli_live: bool = False,
) -> dict[str, Any]:
    """Submit q=10 only after exact economics and both venue reservations pass."""
    if not live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi q10 probes are disabled")

    original = validate_q10_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
        min_profit_per_contract=D(min_profit_per_contract),
    )
    cushion = best_q10_fee_aware_kalshi_hedge(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        min_profit_per_contract=D(min_profit_per_contract),
        max_cushion_cents=int(max_cushion_cents),
    )
    hedge_payload = dict(cushion.payload)
    hedged = validate_q10_probe_payloads(
        gemini_payload,
        hedge_payload,
        modeled_locked_profit=cushion.cushioned_locked_profit,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
        min_profit_per_contract=D(min_profit_per_contract),
    )

    # Capital is validated against the actual cushioned outbound hedge, not the
    # original quote and not a fixed per-attempt dollar assumption.
    required = q10_reservation_requirements(
        gemini_payload,
        hedge_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    reserved = _reservation_snapshot(reserved_cash_by_venue)
    _assert_reservations_cover(required, reserved)
    reservation_meta = {
        "required_gemini": str(required["gemini"]),
        "required_kalshi": str(required["kalshi"]),
        "required_pair_total": str(required["pair_total"]),
        "reserved_gemini": str(reserved["gemini"]),
        "reserved_kalshi": str(reserved["kalshi"]),
    }
    cushion_meta = {
        "enabled": True,
        "cushion_cents": cushion.cushion_cents,
        "original_yes_price": str(cushion.original_yes_price),
        "cushioned_yes_price": str(cushion.cushioned_yes_price),
        "original_locked_profit": str(cushion.original_locked_profit),
        "cushioned_locked_profit": str(cushion.cushioned_locked_profit),
        "total_profit_floor": str(cushion.total_profit_floor),
    }

    # No venue call occurs above this line.
    g_result = await gemini.place_fok(gemini_payload)
    if g_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_PRIMARY_SUBMISSION",
            "execution_policy": "GEMINI_FIRST_Q10_TERMINAL_THEN_KALSHI",
            "required_quantity": str(Q10),
            "original_recomputed_locked_profit_dollars": str(original.locked_profit),
            "hedge_recomputed_locked_profit_dollars": str(hedged.locked_profit),
            "capital_reservation": reservation_meta,
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(), "kalshi": None, "flatten": None,
            "hard_halt_required": True,
        }

    g_filled = _filled(g_result)
    if g_filled != Q10:
        flatten = None
        hard_halt = g_filled > 0
        if g_filled > 0:
            flatten_result = await emergency_flatten_q10_exact_quantity(
                gemini, gemini_payload, quantity=g_filled,
            )
            flatten = flatten_result.as_dict()
        return {
            "outcome": "PRIMARY_PARTIAL_FILL" if g_filled > 0 else "PRIMARY_NOT_FILLED",
            "execution_policy": "GEMINI_FIRST_Q10_TERMINAL_THEN_KALSHI",
            "required_quantity": str(Q10),
            "capital_reservation": reservation_meta,
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(), "kalshi": None, "flatten": flatten,
            "hard_halt_required": hard_halt,
        }

    k_result = await kalshi.place(hedge_payload)
    if k_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_HEDGE_SUBMISSION",
            "execution_policy": "GEMINI_FIRST_Q10_TERMINAL_THEN_KALSHI",
            "required_quantity": str(Q10),
            "capital_reservation": reservation_meta,
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(), "kalshi": k_result.as_dict(), "flatten": None,
            "hard_halt_required": True,
        }

    k_filled = _filled(k_result)
    if k_filled == Q10:
        return {
            "outcome": "BOTH_FILLED",
            "execution_policy": "GEMINI_FIRST_Q10_TERMINAL_THEN_KALSHI",
            "required_quantity": str(Q10),
            "original_recomputed_locked_profit_dollars": str(original.locked_profit),
            "hedge_recomputed_locked_profit_dollars": str(hedged.locked_profit),
            "capital_reservation": reservation_meta,
            "hedge_cushion": cushion_meta,
            "gemini": g_result.as_dict(), "kalshi": k_result.as_dict(), "flatten": None,
            "hard_halt_required": False,
        }

    if k_filled < 0 or k_filled > Q10:
        return {
            "outcome": "INVALID_HEDGE_FILL_QUANTITY",
            "execution_policy": "GEMINI_FIRST_Q10_TERMINAL_THEN_KALSHI",
            "required_quantity": str(Q10),
            "capital_reservation": reservation_meta,
            "gemini": g_result.as_dict(), "kalshi": k_result.as_dict(), "flatten": None,
            "hard_halt_required": True,
        }

    unhedged = Q10 - k_filled
    flatten = None
    if unhedged > 0:
        flatten_result = await emergency_flatten_q10_exact_quantity(
            gemini, gemini_payload, quantity=unhedged,
        )
        flatten = flatten_result.as_dict()
    return {
        "outcome": "HEDGE_PARTIAL_FILL" if k_filled > 0 else "HEDGE_NOT_FILLED",
        "execution_policy": "GEMINI_FIRST_Q10_TERMINAL_THEN_KALSHI",
        "required_quantity": str(Q10),
        "unhedged_quantity_before_flatten": str(unhedged),
        "capital_reservation": reservation_meta,
        "hedge_cushion": cushion_meta,
        "gemini": g_result.as_dict(), "kalshi": k_result.as_dict(), "flatten": flatten,
        "hard_halt_required": True,
    }
