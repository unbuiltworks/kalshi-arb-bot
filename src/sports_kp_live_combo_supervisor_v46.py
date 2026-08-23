"""Production combo supervisor v46: inspect MVE component market field shape.

Installs bid-sided quote normalization, emits one bounded read-only field-shape
diagnostic, then runs the unchanged v44 diagnostic/seed chain. No market is
created by this wrapper and no live execution gate is changed.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_mve_market_quote_patch as quote_patch
from src import sports_kp_mve_market_shape_diagnostic as shape
from src import sports_kp_live_combo_supervisor_v44 as v44


async def run() -> None:
    quote_patch.install()
    await shape.run_once()
    await v44.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
