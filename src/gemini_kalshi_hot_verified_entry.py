"""Read-only HOT scanner entrypoint with true two-book readiness telemetry.

The underlying scanner logs CRYPTO_HOT_READY_FEED before either venue has delivered
a first book. This wrapper leaves market-data and economics behavior untouched and
instruments the existing in-memory queue. The first time both Gemini and Kalshi have
enqueued a top-of-book for a capture queue, it emits CRYPTO_HOT_READY_BOTH_BOOKS.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import re

from src import gemini_kalshi_multi_ws_hot_catalog as catalog

hot = catalog.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_hot_verified_entry")
_GEMINI_END_RE = re.compile(r"^GEMI-[A-Z]+15M(\d{10})-UP$", re.I)
_ORIGINAL_ENQUEUE = hot._enqueue
_READY_STATE: dict[int, dict[str, tuple[int, str]]] = {}
_READY_LOGGED: set[int] = set()


def _window_end_from_gemini_symbol(symbol: str) -> datetime | None:
    match = _GEMINI_END_RE.match(symbol)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _verified_enqueue(queue, item, stats) -> None:
    """Observe first venue books, then delegate to the proven queue implementation."""
    venue, receive_ms, _exchange_ms, top, _mono_ns = item
    queue_id = id(queue)
    state = _READY_STATE.setdefault(queue_id, {})
    if venue not in state:
        state[venue] = (int(receive_ms), str(top.market_id))

    if queue_id not in _READY_LOGGED and "gemini" in state and "kalshi" in state:
        g_ms, g_symbol = state["gemini"]
        k_ms, k_ticker = state["kalshi"]
        ready_ms = max(g_ms, k_ms)
        end = _window_end_from_gemini_symbol(g_symbol)
        payload = {
            "asset": stats.asset,
            "gemini_symbol": g_symbol,
            "kalshi_ticker": k_ticker,
            "gemini_first_seen_ms": g_ms,
            "kalshi_first_seen_ms": k_ms,
            "both_books_ready_ms": ready_ms,
            "verified_from": "FIRST_ENQUEUED_TOP_OF_BOOK_FROM_EACH_VENUE",
            "read_only": True,
            "order_entry": False,
        }
        if end is not None:
            boundary_ms = int((end - timedelta(minutes=15)).timestamp() * 1000)
            payload["window_end_utc"] = end.isoformat()
            payload["ready_after_boundary_ms"] = max(0, ready_ms - boundary_ms)
        _READY_LOGGED.add(queue_id)
        logger.info("CRYPTO_HOT_READY_BOTH_BOOKS %s", json.dumps(payload, separators=(",", ":")))

    _ORIGINAL_ENQUEUE(queue, item, stats)


hot._enqueue = _verified_enqueue


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
    asyncio.run(hot.collect_all(
        assets=args.assets,
        seconds=args.seconds,
        output_dir=args.output_dir,
        max_quote_skew_ms=args.max_quote_skew_ms,
        capital_limit=args.capital_limit,
        min_profit=args.min_profit,
    ))


if __name__ == "__main__":
    main()
