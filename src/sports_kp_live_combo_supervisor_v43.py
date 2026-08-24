"""Production combo supervisor v43: bounded one-shot monotonicity seed.

When explicitly armed with ENABLE_MVE_MONOTONICITY_SEED=YES, this layer creates
at most four q=1-observation MVE tickers, requests RFQs, scores the guaranteed
`combo NO + one selected component` cover, then continues into the read-only v42
chain. It never accepts an RFQ quote or submits an order. With the arm absent it
is a no-op apart from a compact DISARMED diagnostic.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_live_combo_supervisor_v42 as v42
from src import sports_kp_mve_monotonicity_seed as seed
from src import sports_supervisor_trade_telemetry as telemetry


async def _emit(result: dict) -> None:
    best = result.get("best") or {}
    minimal = best.get("minimal_no") or {} if isinstance(best, dict) else {}
    try:
        await telemetry.emit_cycle({
            "telemetry_kind": "MVE_MONOTONICITY_SEED",
            "supervisor_version": "v43",
            "status": result.get("status"),
            "attempts": result.get("attempts", 0),
            "created_count": len(result.get("created_tickers") or []),
            "positive_yes_bid_count": result.get("positive_yes_bid_count", 0),
            "positive_no_bid_count": result.get("positive_no_bid_count", 0),
            "shadow_executable": result.get("shadow_executable", 0),
            "best_locked_profit": minimal.get("locked_profit_conservative"),
            "best_ticker": best.get("ticker") if isinstance(best, dict) else None,
            "count_as_realized": False,
            "order_entry": False,
        })
    except Exception:
        pass


async def run() -> None:
    result = await seed.run_once()
    await _emit(result)
    await v42.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
