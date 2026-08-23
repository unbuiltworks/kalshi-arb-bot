"""Production combo supervisor v50: external component substitution probe.

Tests MVE-NO + exact public Polymarket NFL component economics before the unchanged
v49 chain. Any positive economics remain rule-uncertified SHADOW until explicit
settlement-rule certification. No private Polymarket API or order entry is used.
"""
from __future__ import annotations

import asyncio

from src import sports_kp_mve_poly_component_probe as poly_component
from src import sports_kp_live_combo_supervisor_v49 as v49


async def run() -> None:
    await poly_component.run_once()
    await v49.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
