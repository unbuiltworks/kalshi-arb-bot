"""Production entrypoint for the bounded MLB final-lag q1 executor.

The existing sports live-budget guard recognizes ``sports_gk_*`` strategies. Keep
this first final-lag canary inside that already-approved cumulative $5 namespace so
introducing the new lane cannot silently create a second risk budget.

The underlying handler uses an asyncio lock to serialize executions. Python's
``logging.Handler`` also owns an attribute named ``lock`` and normally treats it as
an RLock. The production entrypoint therefore makes logging's acquire/release hooks
no-ops for this one event-loop handler; its asyncio lock remains exclusively for
execution serialization rather than logging internals.

Any nonzero fill that is not exactly q=1 is reclassified UNKNOWN. The parent then
hard-halts and preserves the reservation rather than releasing capital while an
unexpected fractional position may exist.
"""
from __future__ import annotations

from typing import Any

from src import mlb_prop_final_lag_live_q1 as impl

impl.STRATEGY = "sports_gk_final_lag_kp_q1"


def _unresolved(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminal = {"REALIZED", "TRADE_REALIZED", "SETTLED", "CAPITAL_RELEASED"}
    for row in impl._shared_sports_live_rows(rows):
        strategy = str(row.get("strategy") or "")
        if "final_lag" not in strategy:
            continue
        if str(row.get("status") or "").upper() in terminal:
            continue
        actual = impl._d(row.get("capital_filled"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = impl._d(metadata.get("reserved_usd"))
        if actual > 0 or reserved > 0:
            return row
    return None


impl._unresolved_final_lag = _unresolved

# ``FinalLagLiveHandler.__init__`` intentionally reuses ``self.lock`` as an
# asyncio.Lock. Prevent the synchronous logging framework from acquiring/releasing
# that object; execution code still uses ``async with self.lock`` normally.
impl.FinalLagLiveHandler.acquire = lambda self: None
impl.FinalLagLiveHandler.release = lambda self: None

_base_submit = impl._submit


async def _submit_fail_closed(leg: Any) -> Any:
    result = await _base_submit(leg)
    filled = impl._d(result.filled_quantity)
    if filled == impl.D("0") or filled == impl.ONE:
        return result
    return impl.VenueResult(
        venue=result.venue,
        order_id=result.order_id,
        status="UNKNOWN",
        filled_quantity=result.filled_quantity,
        average_price=result.average_price,
        reason=f"UNEXPECTED_PARTIAL_FILL_{result.filled_quantity}",
        reconciled=result.reconciled,
    )


impl._submit = _submit_fail_closed


if __name__ == "__main__":
    impl.main()
