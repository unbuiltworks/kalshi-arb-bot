"""Railway entrypoint for the read-only maker shadow plus research sidecars."""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import logging
from pathlib import Path

from src.gemini_kalshi_maker_shadow import collect_all
from src.gemini_kalshi_maker_shadow_ledger import JsonlLedgerForwarder
from src.gemini_kalshi_maker_shadow_resting import install as install_resting_liveness
from src.gemini_kalshi_maker_shadow_stress import install as install_stress

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_shadow_service")
RUNTIME_VERSION = "resting-liveness-v2"


async def run_service(
    *,
    assets: list[str],
    targets: list[Decimal],
    max_book_skew_ms: int,
    max_hedge_age_ms: int,
    max_resting_feed_age_ms: int,
    output_dir: Path,
) -> None:
    install_resting_liveness(max_resting_feed_age_ms)
    install_stress(output_dir)
    logger.info(
        "MAKER_SHADOW_SERVICE_VERSION version=%s max_book_skew_ms=%d max_hedge_age_ms=%d max_resting_feed_age_ms=%d read_only=true order_entry=false",
        RUNTIME_VERSION,
        max_book_skew_ms,
        max_hedge_age_ms,
        max_resting_feed_age_ms,
    )
    maker = asyncio.create_task(
        collect_all(
            assets=assets,
            seconds=0.0,
            output_dir=output_dir,
            targets=targets,
            max_book_skew_ms=max_book_skew_ms,
            max_hedge_age_ms=max_hedge_age_ms,
        ),
        name="maker-shadow",
    )
    ledger = asyncio.create_task(
        JsonlLedgerForwarder(output_dir).run(),
        name="maker-shadow-ledger",
    )
    tasks = {maker, ledger}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    try:
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise RuntimeError(f"{task.get_name()} failed: {type(exc).__name__}: {exc}") from exc
        raise RuntimeError("maker-shadow service component exited unexpectedly")
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL", "XRP"])
    parser.add_argument(
        "--targets", nargs="+", type=Decimal,
        default=[D("0.01"), D("0.02"), D("0.03"), D("0.05")],
    )
    parser.add_argument("--max-book-skew-ms", type=int, default=50)
    parser.add_argument("--max-hedge-age-ms", type=int, default=100)
    parser.add_argument("--max-resting-feed-age-ms", type=int, default=500)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/maker-shadow"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(
        run_service(
            assets=[a.upper() for a in args.assets],
            targets=list(args.targets),
            max_book_skew_ms=max(0, args.max_book_skew_ms),
            max_hedge_age_ms=max(0, args.max_hedge_age_ms),
            max_resting_feed_age_ms=max(0, args.max_resting_feed_age_ms),
            output_dir=args.output_dir,
        )
    )


if __name__ == "__main__":
    main()
