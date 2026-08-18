"""Strict read-only Kalshi <-> Polymarket execution-quality shadow.

This entrypoint deliberately hard-codes the final-book freshness budget instead
of trusting deployment CLI wiring. It reuses the generic event-driven scanner but
cannot place orders. Settlement remains fail-closed in the pair registry.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from src.generic_cross_market_shadow import run

STRICT_MAX_QUOTE_AGE_MS = 100


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger = logging.getLogger("kalshi_arb.generic_cross_market_execution_shadow")
    args = argparse.Namespace(
        config="config/cross_market_shadow_pairs.json",
        bankroll="50",
        max_quote_age_ms=STRICT_MAX_QUOTE_AGE_MS,
        poly_fee_rate="0.05",
        kalshi_fee_rate="0.07",
    )
    logger.info(
        "GENERIC_EXECUTION_SHADOW_ENTRYPOINT strict_max_quote_age_ms=%s read_only=true order_entry=false",
        STRICT_MAX_QUOTE_AGE_MS,
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
