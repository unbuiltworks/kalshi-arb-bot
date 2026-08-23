"""Production combo supervisor v55: authenticate the richest strict MLB maker q1 path.

Runs the existing broad maker-trigger census, then a mutation-free authenticated
q1 preview for the richest strict same-game/player/stat MLB threshold pair, then
the unchanged v53 Dutch/combo/MVE chain. No orders are created by this revision.
"""
from __future__ import annotations

import asyncio

from src import sports_poly_maker_trigger_census as maker_census
from src import sports_poly_maker_q1_preview as maker_preview
from src import sports_kp_live_combo_supervisor_v53 as v53


async def run() -> None:
    await maker_census.run_once()
    await maker_preview.run_once()
    await v53.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
