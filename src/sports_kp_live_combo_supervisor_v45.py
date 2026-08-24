"""Production combo supervisor v45: normalize bid-sided Kalshi list books.

Installs binary opposite-bid ask derivation for targeted MVE selected components,
then runs the unchanged v44 chain. This repairs discovery only; q=1 depth, fees,
settlement math, RFQ observation, and all live execution gates are unchanged.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_mve_market_quote_patch as quote_patch
from src import sports_kp_live_combo_supervisor_v44 as v44


async def run() -> None:
    quote_patch.install()
    await v44.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
