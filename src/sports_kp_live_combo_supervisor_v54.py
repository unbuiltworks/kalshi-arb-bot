"""Production combo supervisor v54: add Polymarket maker-trigger census.

Runs the read-only maker-first census across strict same-venue total/spread/MLB
player-prop ladders, then the unchanged v53 Dutch/combo/MVE shadow chain.  The
maker census models a hypothetical confirmed maker fill followed by an immediate
q=1 taker hedge using the current 2026 Polymarket US fee/rebate schedule.  It does
not submit, cancel, or modify any order.
"""
from __future__ import annotations

import asyncio

from src import sports_poly_maker_trigger_census as maker_census
from src import sports_kp_live_combo_supervisor_v53 as v53


async def run() -> None:
    await maker_census.run_once()
    await v53.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
