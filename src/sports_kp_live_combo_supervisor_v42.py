"""Production combo supervisor v42: existing-MVE minimal NO-cover shadow scan.

Runs the read-only `combo NO + one selected component` scanner before the existing
v41/v40 supervisor chain.  It may request bounded q=1 RFQs but never creates an
MVE, accepts a quote, submits an order, or changes live execution gates.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_live_combo_supervisor_v41 as v41
from src import sports_kp_mve_minimal_no_scan as minimal_no
from src import sports_supervisor_trade_telemetry as telemetry


async def _emit(result: dict) -> None:
    best = result.get("best") or {}
    try:
        await telemetry.emit_cycle({
            "telemetry_kind": "MVE_MINIMAL_NO_SHADOW",
            "supervisor_version": "v42",
            "identity": "MVE_NO+ANY_ONE_SELECTED_COMPONENT>=1",
            "active_short_duration_mves": result.get("active_short_duration_mves", 0),
            "prequalified_q1_component": result.get("prequalified_q1_component", 0),
            "public_combo_no_scored": result.get("public_combo_no_scored", 0),
            "rfqs_requested": result.get("rfqs_requested", 0),
            "rfq_quotes_seen": result.get("rfq_quotes_seen", 0),
            "rfq_positive_yes_bids": result.get("rfq_positive_yes_bids", 0),
            "shadow_executable": result.get("shadow_executable", 0),
            "best_locked_profit": best.get("locked_profit_conservative"),
            "best_ticker": best.get("ticker"),
            "count_as_realized": False,
            "order_entry": False,
        })
    except Exception:
        pass


async def run() -> None:
    result = await minimal_no.run_once()
    await _emit(result)
    await v41.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
