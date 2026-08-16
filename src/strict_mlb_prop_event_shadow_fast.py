"""Bounded-startup wrapper for the isolated MLB prop event shadow scanner.

Read-only. This wrapper only replaces the one-time Polymarket REST seed helper so
slow/stale books cannot hold the whole hot shard open. Successful seed responses are
kept; laggards are cancelled after the bounded startup window. Live signals still
come only from the WebSocket-backed event scanner.
"""
from __future__ import annotations

import asyncio
import logging
import os

from src import strict_mlb_prop_event_shadow as hot

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_event_shadow_fast")


async def _bounded_seed_poly(http, store, slugs, concurrency):
    sem = asyncio.Semaphore(concurrency)
    tasks = [
        asyncio.create_task(hot._fetch_poly_book(http, store, slug, sem))
        for slug in slugs
    ]
    store.notifications_enabled = False
    try:
        done, pending = await asyncio.wait(tasks, timeout=12.0)
        seeded = 0
        for task in done:
            try:
                seeded += int(bool(task.result()))
            except Exception:
                pass
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info(
            "MLB_PROP_HOT_SEED_BOUND requested=%d seeded=%d cancelled=%d",
            len(slugs), seeded, len(pending),
        )
        return seeded
    finally:
        store.notifications_enabled = True


# run_session resolves this helper from the module global at call time.
hot._seed_poly = _bounded_seed_poly


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    asyncio.run(hot.run(hot.parse_args()))


if __name__ == "__main__":
    main()
