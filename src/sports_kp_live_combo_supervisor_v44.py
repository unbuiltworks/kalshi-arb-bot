"""Production combo supervisor v44: read-only monotonicity candidate geometry.

Runs one bounded public-data diagnostic before the v43 chain so targeted MVE
creation can be based on actual anchor/booster availability instead of guessed
thresholds. The diagnostic creates no market, opens no RFQ, and places no order.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_live_combo_supervisor_v43 as v43
from src import sports_kp_mve_monotonicity_diagnostic as diagnostic
from src import sports_supervisor_trade_telemetry as telemetry


async def _emit(result: dict) -> None:
    try:
        collections = result.get("collections") or []
        pairs_94 = sum(int((x.get("allowed_pair_counts") or {}).get("0.94") or 0) for x in collections if isinstance(x, dict))
        pairs_90 = sum(int((x.get("allowed_pair_counts") or {}).get("0.90") or 0) for x in collections if isinstance(x, dict))
        await telemetry.emit_cycle({
            "telemetry_kind": "MVE_MONOTONICITY_CANDIDATE_DIAGNOSTIC",
            "supervisor_version": "v44",
            "collection_count": result.get("collection_count", 0),
            "events_visited": result.get("events_visited", 0),
            "selected_q1_count": result.get("selected_q1_count", 0),
            "allowed_pairs_ge_90": pairs_90,
            "allowed_pairs_ge_94": pairs_94,
            "count_as_realized": False,
            "order_entry": False,
        })
    except Exception:
        pass


async def run() -> None:
    result = await diagnostic.run_once()
    await _emit(result)
    await v43.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
