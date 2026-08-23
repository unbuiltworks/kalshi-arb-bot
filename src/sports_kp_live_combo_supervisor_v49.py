"""Production combo supervisor v49: exact-fee minimal-NO Dutch economics.

Scores the eight directly hydrated recent MVEs using centicent fee rounding and
live Kalshi series fee metadata, including a diagnostic component-maker scenario.
This remains SHADOW/RFQ-only and then runs the unchanged v48 chain.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_mve_exact_fee_probe as exact_fee
from src import sports_kp_live_combo_supervisor_v48 as v48


async def run() -> None:
    await exact_fee.run_once()
    await v48.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
