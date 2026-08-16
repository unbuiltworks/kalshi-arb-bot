"""Broader-startup wrapper for the isolated MLB prop event shadow scanner.

Read-only. REST seeding is only a warm-start optimization: every participation-cleared
counterpart in the shard is subscribed on both venue WebSockets even when its one-time
Polymarket REST seed is missing or one-sided. Price changes are still evaluated only
from fresh cached books and never authorize order entry.

This wrapper also keeps a quiet five-second market-data interval from tearing down the
whole session and reports book-update -> evaluation latency in heartbeat summaries.
Shard 0 canaries the persistent all-counterpart subscription layer while shards 1-3
remain on this proven v2 path.
"""
from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src import strict_mlb_prop_event_shadow as hot

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_event_shadow_fast_v2")


async def _bounded_seed_poly(
    http: httpx.AsyncClient,
    store: hot.EventingPolyBookStore,
    slugs: list[str],
    concurrency: int,
) -> int:
    """Best-effort warm seed; never determines the WebSocket subscription set."""
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
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info(
            "MLB_PROP_HOT_SEED_BOUND requested=%d seeded=%d cancelled=%d",
            len(slugs), seeded, len(pending),
        )
        return seeded
    finally:
        store.notifications_enabled = True


class LatencyStats(hot.Stats):
    """Hot-scanner counters plus recent trigger-to-evaluation latency."""

    def __init__(self) -> None:
        super().__init__()
        self._decision_ms: deque[float] = deque(maxlen=5000)

    def observe_decision_ms(self, value: float) -> None:
        self._decision_ms.append(max(0.0, value))

    def payload(
        self,
        poly: hot.EventingPolyBookStore,
        kalshi: hot.EventingKalshiCrossBooks,
    ) -> dict[str, Any]:
        payload = super().payload(poly, kalshi)
        if not self._decision_ms:
            payload.update({
                "trigger_to_eval_samples": 0,
                "trigger_to_eval_avg_ms": None,
                "trigger_to_eval_p95_ms": None,
                "trigger_to_eval_max_ms": None,
            })
            return payload

        values = sorted(self._decision_ms)
        n = len(values)
        p95 = values[min(n - 1, max(0, int(0.95 * n) - 1))]
        payload.update({
            "trigger_to_eval_samples": n,
            "trigger_to_eval_avg_ms": round(sum(values) / n, 3),
            "trigger_to_eval_p95_ms": round(p95, 3),
            "trigger_to_eval_max_ms": round(values[-1], 3),
        })
        return payload


def _task_failure(task: asyncio.Task[Any], name: str) -> RuntimeError:
    if task.cancelled():
        return RuntimeError(f"{name} market-data task was cancelled")
    exc = task.exception()
    if exc is not None:
        return RuntimeError(f"{name} market-data task exited: {exc}")
    return RuntimeError(f"{name} market-data task exited unexpectedly")


async def run_session(args) -> None:
    if args.shard_count < 1 or not (0 <= args.shard_index < args.shard_count):
        raise ValueError("shard-index must be in [0, shard-count)")

    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise RuntimeError("Polymarket market-data credentials are required")

    async with httpx.AsyncClient(timeout=20.0) as http:
        candidates, discovery = await hot._discover_hot(
            http, args.shard_index, args.shard_count
        )
        if not candidates:
            logger.warning(
                "MLB_PROP_HOT_EMPTY %s",
                json.dumps(discovery, separators=(",", ":")),
            )
            await asyncio.sleep(args.empty_sleep_s)
            return

        # REST is a warm-start only. The complete cleared shard goes to both WS feeds.
        poly = hot.EventingPolyBookStore()
        slugs = [h.candidate.poly_slug for h in candidates]
        seeded = await _bounded_seed_poly(http, poly, slugs, args.rest_concurrency)

        slug_map = {h.candidate.poly_slug: h for h in candidates}
        ticker_map = {h.candidate.kalshi_ticker: h for h in candidates}
        kalshi = hot.EventingKalshiCrossBooks(list(ticker_map))
        poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
        poly_task = asyncio.create_task(
            hot.run_poly_ws(poly_client, poly, list(slug_map)), name="poly_ws"
        )
        kalshi_task = asyncio.create_task(kalshi.run(), name="kalshi_ws")

        deadline = time.monotonic() + args.startup_timeout_s
        while time.monotonic() < deadline:
            if poly_task.done() or kalshi_task.done():
                break
            if poly.connected and kalshi.initialized_count > 0:
                break
            await asyncio.sleep(0.025)

        if poly_task.done():
            failure = _task_failure(poly_task, "Polymarket")
            kalshi_task.cancel()
            await kalshi.close()
            try:
                poly_client.close()
            except Exception:
                pass
            await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)
            raise failure
        if kalshi_task.done():
            failure = _task_failure(kalshi_task, "Kalshi")
            poly_task.cancel()
            await kalshi.close()
            try:
                poly_client.close()
            except Exception:
                pass
            await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)
            raise failure
        if not poly.connected or kalshi.initialized_count == 0:
            poly_task.cancel()
            kalshi_task.cancel()
            await kalshi.close()
            try:
                poly_client.close()
            except Exception:
                pass
            await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)
            raise RuntimeError(
                f"hot startup timeout poly_connected={poly.connected} "
                f"kalshi_initialized={kalshi.initialized_count}/{len(candidates)}"
            )

        discovery.update({
            "seeded_poly_books": seeded,
            "subscribed_hot_pairs": len(candidates),
            # Kept for log-consumer compatibility; unlike v1 this is the complete
            # subscription set, not the REST-seeded subset.
            "live_hot_pairs": len(candidates),
            "seed_coverage_pct": round(100.0 * seeded / len(candidates), 1),
            "kalshi_initialized_at_ready": kalshi.initialized_count,
            "max_quote_age_ms": args.max_quote_age_ms,
            "session_seconds": args.session_seconds,
            "rest_seed_is_gate": False,
        })
        hot._drain(poly.updates)
        hot._drain(kalshi.updates)
        logger.info(
            "MLB_PROP_HOT_READY_FEED %s",
            json.dumps(
                {**discovery, "read_only": True, "order_entry": False},
                separators=(",", ":"),
            ),
        )

        stats = LatencyStats()
        active: dict[str, hot.ActiveSignal] = {}
        heartbeat = asyncio.create_task(
            hot._heartbeat(stats, poly, kalshi, active, discovery),
            name="hot_heartbeat",
        )
        end_at = time.monotonic() + args.session_seconds
        try:
            while time.monotonic() < end_at:
                if poly_task.done():
                    raise _task_failure(poly_task, "Polymarket")
                if kalshi_task.done():
                    raise _task_failure(kalshi_task, "Kalshi")

                try:
                    trigger = await asyncio.wait_for(
                        hot._next_update(poly, kalshi), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    # A quiet market is not a feed failure and must not force a
                    # full rediscovery/reconnect cycle.
                    continue

                stats.triggers += 1
                row = (
                    slug_map.get(trigger.market_id)
                    if trigger.venue == "polymarket"
                    else ticker_map.get(trigger.market_id)
                )
                if row is None:
                    continue

                await hot._evaluate(
                    row,
                    trigger,
                    poly,
                    kalshi,
                    args.max_quote_age_ms,
                    active,
                    stats,
                )
                stats.observe_decision_ms(
                    (time.perf_counter_ns() - trigger.mono_ns) / 1_000_000
                )
        finally:
            heartbeat.cancel()
            poly_task.cancel()
            kalshi_task.cancel()
            await kalshi.close()
            try:
                poly_client.close()
            except Exception:
                pass
            await asyncio.gather(
                heartbeat, poly_task, kalshi_task, return_exceptions=True
            )


def main() -> None:
    args = hot.parse_args()
    if args.shard_index == 0:
        # Canary the persistent architecture on exactly one read-only shard. The
        # other three keep the already-validated v2 path until the canary proves
        # subscription capacity, gate refresh, and latency under the larger set.
        from src import strict_mlb_prop_event_shadow_persistent as persistent

        logging.basicConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format="%(asctime)s %(levelname)-8s %(message)s",
            stream=sys.stdout,
            force=True,
        )
        hot.run_session = persistent.run_session
    else:
        logging.basicConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format="%(asctime)s %(levelname)-8s %(message)s",
            stream=sys.stdout,
            force=True,
        )
        hot.run_session = run_session
    asyncio.run(hot.run(args))


if __name__ == "__main__":
    main()
