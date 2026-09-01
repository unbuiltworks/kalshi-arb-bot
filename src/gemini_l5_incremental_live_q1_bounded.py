"""Production-bound wrapper for the first incremental q1 live experiment.

Tightens the generic 20-80c research band to 20-70c so, even if both the crypto
and sports families each end with one q1 residual, aggregate new residual notional
cannot exceed $1.40. No strategy logic is otherwise changed.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D

from src import gemini_l5_incremental_live_q1 as base

base.MAX_PRICE = D("0.70")
base.MAX_NEW_RESIDUAL_NOTIONAL = D("1.40")


def main() -> None:
    asyncio.run(base.run())


if __name__ == "__main__":
    main()
