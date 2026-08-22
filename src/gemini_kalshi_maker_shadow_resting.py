"""More realistic resting-quote liveness for the maker shadow.

The initial quote still requires a tightly synchronized Gemini/Kalshi snapshot.
Once hypothetically resting, however, a quote is not canceled merely because one
venue has not emitted another book update within the 50ms synchronization window.
That old behavior made the shadow churn essentially every quote before it could be
hit. We instead cancel only when either cached book is genuinely stale.

This is research-only. Fill-time hedgeability remains governed by the stricter
``max_hedge_age_ms`` gate in the core maker simulator.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Any

from src import gemini_kalshi_maker_shadow as maker

logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_shadow_resting")
DEFAULT_MAX_RESTING_FEED_AGE_MS = 500


async def capture_pair_resting(
    *,
    asset: str,
    pair: maker.Pair,
    fee_meta: maker.KalshiSeriesFeeMetadata,
    output: Path,
    targets: list[maker.Decimal],
    max_book_skew_ms: int,
    max_hedge_age_ms: int,
    overall_deadline: float | None,
    max_resting_feed_age_ms: int = DEFAULT_MAX_RESTING_FEED_AGE_MS,
) -> None:
    queue: asyncio.Queue = asyncio.Queue(maxsize=maker.MAX_QUEUE)
    stats = maker.MakerStats(asset=asset)
    tasks = [
        asyncio.create_task(maker._gemini_stream(pair.gemini_symbol, queue, stats)),
        asyncio.create_task(maker._kalshi_stream(pair.kalshi_ticker, queue, stats)),
    ]
    g: maker.TopOfBook | None = None
    k: maker.TopOfBook | None = None
    active: dict[str, maker.RestingQuote] = {}
    next_heartbeat = time.monotonic() + maker.HEARTBEAT_S
    remaining_window_s = max(0.0, pair.window_end.timestamp() - time.time() + 0.75)
    pair_deadline = time.monotonic() + remaining_window_s
    if overall_deadline is not None:
        pair_deadline = min(pair_deadline, overall_deadline)

    logger.info(
        "MAKER_SHADOW_RESTING_LIVENESS %s",
        json.dumps({
            "asset": asset,
            "initial_snapshot_max_skew_ms": max_book_skew_ms,
            "max_resting_feed_age_ms": max_resting_feed_age_ms,
            "fill_hedge_max_age_ms": max_hedge_age_ms,
            "behavior": "OPEN_ON_SYNC_HOLD_UNTIL_TRUE_STALE_OR_REPRICE",
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("a", encoding="utf-8") as fh:
            while time.monotonic() < pair_deadline:
                timeout = max(0.01, min(0.5, pair_deadline - time.monotonic()))
                try:
                    kind, receive_ms, payload = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue

                emitted: list[dict[str, Any]] = []
                if kind == "gemini":
                    g = payload
                elif kind == "kalshi":
                    k = payload
                else:
                    emitted.extend(maker._process_trade(
                        active=active,
                        trade=payload,
                        receive_ms=receive_ms,
                        gemini=g,
                        fee_meta=fee_meta,
                        max_hedge_age_ms=max_hedge_age_ms,
                        stats=stats,
                    ))

                # Do not reopen/reprice from a trade event against a pre-trade book.
                if kind in {"gemini", "kalshi"} and g is not None and k is not None:
                    g_age = max(0, receive_ms - g.ts_ms)
                    k_age = max(0, receive_ms - k.ts_ms)
                    skew = abs(g.ts_ms - k.ts_ms)

                    # A truly stale venue invalidates every resting hypothetical quote.
                    if max(g_age, k_age) > max_resting_feed_age_ms:
                        for key, quote in list(active.items()):
                            emitted.append({
                                "event": "CANCEL",
                                "key": key,
                                "reason": "RESTING_FEED_STALE",
                                "price": str(quote.price),
                                "age_ms": max(0, receive_ms - quote.opened_ms),
                                "book_skew_ms": skew,
                                "gemini_book_age_ms": g_age,
                                "kalshi_book_age_ms": k_age,
                            })
                            stats.quote_cancels += 1
                        active.clear()

                    # Only synchronized snapshots may create/reprice quotes. When the
                    # books are merely >50ms apart but still live, leave existing maker
                    # quotes resting rather than generating artificial churn.
                    elif skew <= max_book_skew_ms:
                        emitted.extend(maker._refresh_quotes(
                            active=active,
                            asset=asset,
                            now_ms=receive_ms,
                            gemini=g,
                            kalshi=k,
                            fee_meta=fee_meta,
                            targets=targets,
                            stats=stats,
                        ))

                for row in emitted:
                    full = {
                        "asset": asset,
                        "receive_ts_ms": receive_ms,
                        "gemini_symbol": pair.gemini_symbol,
                        "kalshi_ticker": pair.kalshi_ticker,
                        **row,
                        "settlement_status": maker.SETTLEMENT_STATUS,
                        "execution_eligible": False,
                        "read_only": True,
                        "order_entry": False,
                    }
                    fh.write(json.dumps(full, separators=(",", ":"), default=str) + "\n")

                if time.monotonic() >= next_heartbeat:
                    payload = stats.payload(len(active), queue.qsize())
                    payload.update({
                        "resting_liveness": "OPEN_ON_SYNC_HOLD_UNTIL_TRUE_STALE_OR_REPRICE",
                        "max_resting_feed_age_ms": max_resting_feed_age_ms,
                    })
                    logger.info(
                        "MAKER_SHADOW_SUMMARY %s",
                        json.dumps(payload, separators=(",", ":"), default=str),
                    )
                    next_heartbeat = time.monotonic() + maker.HEARTBEAT_S
            fh.flush()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def install(max_resting_feed_age_ms: int = DEFAULT_MAX_RESTING_FEED_AGE_MS) -> None:
    """Replace only the pair-capture liveness policy; core economics/fills stay intact."""
    original = capture_pair_resting

    async def patched(**kwargs):
        return await original(
            **kwargs,
            max_resting_feed_age_ms=max(0, int(max_resting_feed_age_ms)),
        )

    maker._capture_pair = patched
