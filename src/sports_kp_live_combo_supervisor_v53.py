"""Production combo supervisor v53: strict same-venue Polymarket Dutch suite.

Runs strict live Polymarket nested full-game totals, same-team spreads, and
same-game/same-player/same-stat MLB prop threshold ladders, then the unchanged v50
combo/MVE chain. All added scanners are public-book SHADOW only. No quote
acceptance or order-entry behavior changes.
"""
from __future__ import annotations

import asyncio

from src import sports_poly_live_nested_dutch as poly_total_nested
from src import sports_poly_live_nested_spread as poly_spread_nested
from src import sports_poly_live_nested_player_props as poly_prop_nested
from src import sports_kp_live_combo_supervisor_v50 as v50


async def run() -> None:
    await poly_total_nested.run_once()
    await poly_spread_nested.run_once()
    await poly_prop_nested.run_once()
    await v50.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
