"""Read-only HOT scanner entrypoint with true two-book readiness telemetry.

The underlying scanner historically logged CRYPTO_HOT_READY_FEED before either
venue had delivered a first book. This wrapper leaves trading/economics behavior
untouched and derives a verified readiness timestamp from the first persisted
cross-venue evaluation row, which can only exist after both Gemini and Kalshi
books are present.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from decimal import Decimal
import json
import logging
from pathlib import Path

from src import gemini_kalshi_multi_ws_hot_catalog as catalog

hot = catalog.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_hot_verified_entry")


async def _monitor_verified_ready(output_dir: Path, stop: asyncio.Event) -> None:
    offsets: dict[Path, int] = {}
    seen: set[tuple[str, str]] = set()
    while not stop.is_set():
        for path in output_dir.glob("crypto_hot_*.jsonl"):
            try:
                offset = offsets.get(path, 0)
                with path.open("r", encoding="utf-8") as fh:
                    fh.seek(offset)
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        asset = str(row.get("asset") or "")
                        window_end = str(row.get("window_end_utc") or "")
                        key = (asset, window_end)
                        if not asset or not window_end or key in seen:
                            continue
                        g_ms = row.get("gemini_receive_ts_ms")
                        k_ms = row.get("kalshi_receive_ts_ms")
                        if g_ms is None or k_ms is None:
                            continue
                        ready_ms = max(int(g_ms), int(k_ms))
                        end = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
                        boundary_ms = int((end.timestamp() - 15 * 60) * 1000)
                        seen.add(key)
                        logger.info(
                            "CRYPTO_HOT_READY_BOTH_BOOKS %s",
                            json.dumps({
                                "asset": asset,
                                "window_end_utc": window_end,
                                "gemini_symbol": row.get("gemini_symbol"),
                                "kalshi_ticker": row.get("kalshi_ticker"),
                                "gemini_first_seen_ms": int(g_ms),
                                "kalshi_first_seen_ms": int(k_ms),
                                "both_books_ready_ms": ready_ms,
                                "ready_after_boundary_ms": max(0, ready_ms - boundary_ms),
                                "verified_from": "FIRST_CROSS_VENUE_EVALUATION_ROW",
                                "read_only": True,
                                "order_entry": False,
                            }, separators=(",", ":")),
                        )
                    offsets[path] = fh.tell()
            except FileNotFoundError:
                pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.02)
        except asyncio.TimeoutError:
            pass


async def _run(args: argparse.Namespace) -> None:
    stop = asyncio.Event()
    monitor = asyncio.create_task(_monitor_verified_ready(args.output_dir, stop))
    try:
        await hot.collect_all(
            assets=args.assets,
            seconds=args.seconds,
            output_dir=args.output_dir,
            max_quote_skew_ms=args.max_quote_skew_ms,
            capital_limit=args.capital_limit,
            min_profit=args.min_profit,
        )
    finally:
        stop.set()
        await asyncio.gather(monitor, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL", "XRP"])
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=100)
    parser.add_argument("--capital-limit", type=Decimal, default=D("5"))
    parser.add_argument("--min-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/crypto-hot"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
