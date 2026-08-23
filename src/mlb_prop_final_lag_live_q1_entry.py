"""Production entrypoint for the bounded MLB final-lag q1 executor.

The existing sports live-budget guard recognizes ``sports_gk_*`` strategies. Keep
this first final-lag canary inside that already-approved cumulative $5 namespace so
introducing the new lane cannot silently create a second risk budget.

The underlying handler uses an asyncio lock to serialize executions. Python's
``logging.Handler`` also owns an attribute named ``lock`` and normally treats it as
an RLock. The production entrypoint therefore makes logging's acquire/release hooks
no-ops for this one event-loop handler; its asyncio lock remains exclusively for
execution serialization rather than logging internals.
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


if __name__ == "__main__":
    impl.main()
