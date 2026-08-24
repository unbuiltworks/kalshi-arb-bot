"""Production combo supervisor v52: strict same-venue Polymarket Dutch scans.

Runs strict live Polymarket full-game half-point total and same-team spread
nested-Dutch scanners, then the unchanged v50 combo/MVE chain. Both new scanners
are public-book SHADOW only. No quote acceptance or order-entry behavior changes.
"""
from __future__ import annotations

import asyncio

from src import sports_poly_live_nested_dutch as poly_total_nested
from src import sports_poly_live_nested_spread as poly_spread_nested
from src import sports_kp_live_combo_supervisor_v50 as v50


async def run() -> None:
    await poly_total_nested.run_once()
    await poly_spread_nested.run_once()
    await v50.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
