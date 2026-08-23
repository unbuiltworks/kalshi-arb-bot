"""Production hardening entrypoint for MLB G/K Hits final-lag q1.

Keeps the implementation small while enforcing two fail-closed corrections:
1. ``_kalshi_exact`` already returns a TopOfBook, so the live loop must not try to
   transform it again through ``kalshi_tob``.
2. Any nonzero fill other than exactly q=1 is reclassified UNKNOWN so the parent
   path hard-halts and preserves the reservation instead of treating exposure as an
   unfilled FOK.

The cumulative sports live-probe ceiling is externally configured through
``SPORTS_LIVE_CUMULATIVE_BUDGET_USD``; changing that ceiling does not change q=1,
profit, freshness, settlement, or revalidation gates.
FanDuel/sportsbook transport remains unreachable from this entrypoint.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src import mlb_gk_hits_final_lag_live_q1 as impl
from src.gemini_kalshi_probe_transport import ExecutionResult

D = Decimal

# The implementation's exact REST helper already emits canonical YES-space
# TopOfBook. Make the second transform an identity operation.
impl.kalshi_tob = lambda quote, ticker: quote

# Require a discrete canonical line in addition to direct native orientation.
_base_native_direct = impl._native_direct
impl._native_direct = lambda row: bool(_base_native_direct(row) and row.gemini_side.identity.line is not None)

_base_execute = impl._execute


async def _execute_fail_closed(row: Any, edge: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
    result = await _base_execute(row, edge, context)
    filled = D(str(result.filled_quantity))
    if filled == D("0") or filled == D("1"):
        return result
    return ExecutionResult(
        venue=result.venue,
        client_order_id=result.client_order_id,
        filled_quantity=result.filled_quantity,
        average_fill_price=result.average_fill_price,
        fee_paid=result.fee_paid,
        status="UNKNOWN",
        reason=f"UNEXPECTED_PARTIAL_FILL_{result.filled_quantity}",
        local_send_ns=result.local_send_ns,
        local_done_ns=result.local_done_ns,
        exchange_ts_ns=result.exchange_ts_ns,
        request_latency_ms=result.request_latency_ms,
        ack_latency_ms=result.ack_latency_ms,
        raw_request_status=result.raw_request_status,
        reconciled=result.reconciled,
    )


impl._execute = _execute_fail_closed


if __name__ == "__main__":
    impl.main()
