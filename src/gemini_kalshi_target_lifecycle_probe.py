"""Read-only Kalshi lifecycle target probe. Imports no order-entry transport."""
from __future__ import annotations

import asyncio
import logging
import os

from src import gemini_kalshi_target_lifecycle_cache as cache


async def _run() -> None:
    duration = float(os.getenv("TARGET_LIFECYCLE_PROBE_SECONDS", "240"))
    try:
        await asyncio.wait_for(
            cache._kalshi_target_loop_lifecycle(("BTC", "ETH", "SOL", "XRP")),
            timeout=duration,
        )
    except asyncio.TimeoutError:
        logging.getLogger(__name__).info("CRYPTO_TARGET_LIFECYCLE_PROBE_COMPLETE seconds=%s", duration)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
