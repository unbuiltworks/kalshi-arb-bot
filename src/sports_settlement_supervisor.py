"""Run all authoritative sports settlement reconcilers in one Railway service."""
from __future__ import annotations

import argparse
import asyncio
import logging

from src.sports_poly_structural_settlement_reconciler import run_forever as run_poly_structural
from src.sports_trade_settlement_reconciler_v2 import run_forever as run_gemini_kalshi


async def run(interval_seconds: float) -> None:
    # asyncio.gather propagates an unexpected task exit/exception so Railway's
    # existing restart policy can restart the whole settlement service rather
    # than silently losing one accounting lane.
    await asyncio.gather(
        run_gemini_kalshi(interval_seconds),
        run_poly_structural(interval_seconds),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=20.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.interval_seconds))


if __name__ == "__main__":
    main()
