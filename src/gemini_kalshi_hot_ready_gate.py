"""Ready-gated rollover wrapper for the persistence-probe HOT scanner.

The first double-buffer implementation promoted the prewarmed feed at the quarter-hour
boundary even when Kalshi had not yet delivered its first new-window book. Economics
still failed closed, but the state transition was semantically early.

This wrapper changes only the handoff contract:
- start next deterministic sockets at T-1s as before;
- keep the feed inactive while its pump drains messages;
- return it to the switch loop only after both Gemini and Kalshi tops exist;
- seed the sequential evaluator with those exact warm tops before activation.

The outgoing feed therefore remains active to the boundary, while the new feed is not
promoted until it is genuinely usable. Read-only; no order-entry path.
"""
from __future__ import annotations

import asyncio
import json
import time

from src import gemini_kalshi_hot_persistence_probe as persistence

quarantine = persistence.quarantine
base = persistence.base
hot = persistence.hot
_original_start_next_at = base._start_next_at


async def _start_next_at_ready(**kwargs):
    feed = await _original_start_next_at(**kwargs)
    wait_started = time.monotonic()
    while feed.gemini is None or feed.kalshi is None:
        await asyncio.sleep(0.001)

    # The warm pump owns these latest top snapshots. Seed a separate evaluator state
    # so the first post-switch trigger can be evaluated in event order without waiting
    # for both venues to publish a second message.
    feed.eval_gemini = feed.gemini
    feed.eval_kalshi = feed.kalshi
    feed.eval_latest_k_exchange_ts = feed.latest_k_exchange_ts
    feed.ready_gate_passed_ms = int(time.time() * 1000)
    hot.logger.info(
        "CRYPTO_HOT_READY_GATE_PASS %s",
        json.dumps({
            "asset": feed.asset,
            "gemini_symbol": feed.pair.gemini_symbol,
            "kalshi_ticker": feed.pair.kalshi_ticker,
            "window_end_utc": feed.pair.window_end.isoformat(),
            "gemini_first_seen_ms": feed.gemini_first_seen_ms,
            "kalshi_first_seen_ms": feed.kalshi_first_seen_ms,
            "ready_gate_passed_ms": feed.ready_gate_passed_ms,
            "gate_wait_ms": round((time.monotonic() - wait_started) * 1000, 3),
            "active": feed.active,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


base._start_next_at = _start_next_at_ready


def main() -> None:
    persistence.main()


if __name__ == "__main__":
    main()
