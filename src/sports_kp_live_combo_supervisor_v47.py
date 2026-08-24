"""Production combo supervisor v47: explicit Dutch-book constructions.

Runs a read-only nested-threshold cover scan and a separately disarmed bounded
four-corner MVE partition seed before the unchanged v46 chain. No quote acceptance
or order-entry behavior is added here.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_mve_market_quote_patch as quote_patch
from src import sports_kp_nested_dutch_scan as nested
from src import sports_kp_mve_four_corner_seed as four_corner
from src import sports_kp_live_combo_supervisor_v46 as v46


async def run() -> None:
    quote_patch.install()
    await nested.run_once()
    await four_corner.run_once()
    await v46.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
