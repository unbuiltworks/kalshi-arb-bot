"""Production combo supervisor v48: direct-hydrated minimal-NO Dutch covers.

Kalshi's multivariate event catalog is dominated by historical markets and can
omit freshly manufactured active MVEs from bounded pagination.  Run the explicit
recent-ticker two-contract Dutch-book probe before the unchanged v47 chain.

This layer remains read-only/RFQ-only: no market creation, quote acceptance, or
order entry is enabled here.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_mve_market_quote_patch as quote_patch
from src import sports_kp_mve_minimal_no_direct_probe as direct_minimal
from src import sports_kp_live_combo_supervisor_v47 as v47


async def run() -> None:
    quote_patch.install()
    await direct_minimal.run_once()
    await v47.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
