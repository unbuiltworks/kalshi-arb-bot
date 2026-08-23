"""Production combo supervisor v51: live Polymarket nested-total Dutch books.

Scans the broad current Polymarket live sports inventory for same-event full-game
total ladders satisfying YES(low)+NO(high)>=1. This is public-book SHADOW only and
then runs the unchanged v50 combo/MVE chain. No order-entry behavior is changed.
"""
from __future__ import annotations

import asyncio

from src import sports_poly_live_nested_dutch as poly_nested
from src import sports_kp_live_combo_supervisor_v50 as v50


async def run() -> None:
    await poly_nested.run_once()
    await v50.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
