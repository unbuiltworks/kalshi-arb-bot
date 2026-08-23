"""Basis-risk-only thin-leg-first q=1 execution transport.

The generic Gemini/Kalshi probe transport submits both FOK legs concurrently. That is
appropriate only when both legs are comparably reliable. The promoted basis canary hit
a real failure mode on 2026-08-23: Gemini filled while Kalshi's thin FOK disappeared
and rejected. This module fixes that failure mode only inside the isolated basis-risk
process:

1. Recompute/validate the exact two-leg economics and skew exactly as before.
2. Submit Kalshi (the observed thinner/less reliable leg) first.
3. If Kalshi does not fill q=1, do NOT submit Gemini.
4. Only after a confirmed Kalshi q=1 fill, submit the original Gemini FOK.
5. If Gemini explicitly does not fill, immediately attempt the proven reverse Kalshi
   emergency flatten and hard-halt for review.
6. If Gemini is ambiguous after its built-in reconciliation, do not guess; hard-halt.

No guaranteed-arbitrage authorization is changed. The same q=1, exact prices, fee
validation, freshness/skew gate and basis-risk authorization remain upstream.
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from src import gemini_kalshi_probe_transport as transport

D = Decimal


def _not_submitted(venue: str, client_order_id: str, reason: str) -> transport.ExecutionResult:
    now = time.perf_counter_ns()
    return transport.ExecutionResult(
        venue=venue,
        client_order_id=client_order_id,
        filled_quantity="0",
        average_fill_price=None,
        fee_paid=None,
        status="NOT_SUBMITTED",
        reason=reason,
        local_send_ns=now,
        local_done_ns=now,
        exchange_ts_ns=None,
        request_latency_ms=0.0,
        ack_latency_ms=None,
        raw_request_status=None,
        reconciled=False,
    )


async def submit_kalshi_first_probe(
    gemini: transport.GeminiTradingSession,
    kalshi: transport.KalshiV2Executor,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    cli_live: bool = False,
) -> dict[str, Any]:
    """Execute q=1 with Kalshi confirmed first; never create a Gemini-first orphan."""
    if not transport.live_probe_authorized(cli_live):
        raise PermissionError("live Gemini/Kalshi probes are disabled")

    recomputed = transport.validate_exact_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=D(modeled_locked_profit),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=int(quote_skew_ms),
    )

    gemini_skipped = _not_submitted(
        "gemini",
        str(gemini_payload["clientOrderId"]),
        "KALSHI_FIRST_NOT_CONFIRMED_FILLED",
    )
    k_result = await kalshi.place(kalshi_payload)

    if k_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_KALSHI_FIRST",
            "recomputed_locked_profit_dollars": str(recomputed),
            "execution_sequence": "KALSHI_THEN_GEMINI",
            "gemini": gemini_skipped.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    k_filled = D(k_result.filled_quantity) == D("1")
    if not k_filled:
        return {
            "outcome": "NEITHER_FILLED",
            "recomputed_locked_profit_dollars": str(recomputed),
            "execution_sequence": "KALSHI_THEN_GEMINI",
            "gemini": gemini_skipped.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    g_result = await gemini.place_fok(gemini_payload)
    if g_result.status == "UNKNOWN":
        return {
            "outcome": "AMBIGUOUS_GEMINI_SECOND",
            "recomputed_locked_profit_dollars": str(recomputed),
            "execution_sequence": "KALSHI_THEN_GEMINI",
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": True,
        }

    g_filled = D(g_result.filled_quantity) == D("1")
    if g_filled:
        return {
            "outcome": "BOTH_FILLED",
            "recomputed_locked_profit_dollars": str(recomputed),
            "execution_sequence": "KALSHI_THEN_GEMINI",
            "gemini": g_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None,
            "hard_halt_required": False,
        }

    flatten = await kalshi.emergency_flatten(kalshi_payload)
    return {
        "outcome": "ONE_LEG_FILLED",
        "recomputed_locked_profit_dollars": str(recomputed),
        "execution_sequence": "KALSHI_THEN_GEMINI",
        "gemini": g_result.as_dict(),
        "kalshi": k_result.as_dict(),
        "flatten": flatten.as_dict(),
        "flatten_recovered": D(flatten.filled_quantity) == D("1"),
        "hard_halt_required": True,
    }
