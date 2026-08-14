"""Event-driven, read-only Polymarket US <-> Kalshi MLB total scanner.

Every websocket book mutation wakes only the affected strict-matched pair and
recalculates the after-fee two-leg arb immediately. REST is used only once to seed
Polymarket books at startup and is never a trading-signal source.

There is deliberately no order-entry code in this module.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
import signal
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src import polymarket_kalshi_shadow as base
from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws
from src.polymarket_kalshi_shadow_v4 import strict_discover_matches

logger = logging.getLogger("kalshi_arb.poly_kalshi_shadow_v5")


@dataclass
class ActiveSignal:
    opened_ms: int
    last_payload: dict[str, Any]
    fingerprint: tuple[Any, ...]


class EventStats:
    def __init__(self) -> None:
        self.started_ms = int(time.time() * 1000)
        self.triggers = 0
        self.poly_triggers = 0
        self.kalshi_triggers = 0
        self.scans = 0
        self.comparable = 0
        self.positive_observations = 0
        self.opens = 0
        self.moves = 0
        self.closes = 0
        self.calc_us: deque[float] = deque(maxlen=50000)
        self.trigger_to_calc_us: deque[float] = deque(maxlen=50000)

    @staticmethod
    def _pct(values: deque[float], p: float) -> float | None:
        if not values:
            return None
        rows = sorted(values)
        idx = min(len(rows) - 1, max(0, round((len(rows) - 1) * p)))
        return round(rows[idx], 2)

    def payload(self, *, poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> dict[str, Any]:
        return {
            "uptime_s": round((int(time.time() * 1000) - self.started_ms) / 1000, 1),
            "triggers": self.triggers,
            "poly_triggers": self.poly_triggers,
            "kalshi_triggers": self.kalshi_triggers,
            "scans": self.scans,
            "comparable": self.comparable,
            "positive_observations": self.positive_observations,
            "opens": self.opens,
            "moves": self.moves,
            "closes": self.closes,
            "calc_us_p50": self._pct(self.calc_us, 0.50),
            "calc_us_p95": self._pct(self.calc_us, 0.95),
            "calc_us_p99": self._pct(self.calc_us, 0.99),
            "trigger_to_calc_us_p50": self._pct(self.trigger_to_calc_us, 0.50),
            "trigger_to_calc_us_p95": self._pct(self.trigger_to_calc_us, 0.95),
            "trigger_to_calc_us_p99": self._pct(self.trigger_to_calc_us, 0.99),
            "poly_queue": poly.updates.qsize(),
            "kalshi_queue": kalshi.updates.qsize(),
            "poly_dropped_updates": poly.dropped_updates,
            "kalshi_dropped_updates": kalshi.dropped_updates,
            "poly_ws_updates": poly.ws_updates,
            "kalshi_initialized_books": kalshi.initialized_count,
        }


def _fingerprint(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        payload["direction"], payload["quantity"], payload["poly_price"],
        payload["kalshi_price"], payload["profit"],
    )


def _signal_key(match: base.Match, direction: str) -> str:
    return f"{match.poly.slug}|{match.kalshi.ticker}|{direction}"


def _log_signal(kind: str, payload: dict[str, Any], *, lifetime_ms: int) -> None:
    logger.info(
        "EVENT_SHADOW %s",
        json.dumps({"kind": kind, "lifetime_ms": lifetime_ms, **payload}, separators=(",", ":")),
    )


def _drain(queue: asyncio.Queue[BookUpdate]) -> int:
    n = 0
    while True:
        try:
            queue.get_nowait()
            n += 1
        except asyncio.QueueEmpty:
            return n


def _task_failure(name: str, task: asyncio.Task[Any]) -> RuntimeError:
    if task.cancelled():
        return RuntimeError(f"{name} market-data task cancelled unexpectedly")
    exc = task.exception()
    if exc is None:
        return RuntimeError(f"{name} market-data task exited unexpectedly")
    return RuntimeError(f"{name} market-data task failed: {type(exc).__name__}: {exc}")


async def _scan_pair(
    match: base.Match,
    *,
    trigger: BookUpdate,
    poly_store: EventingPolyBookStore,
    kalshi_store: EventingKalshiCrossBooks,
    bankroll: Decimal,
    poly_rate: Decimal,
    kalshi_rate: Decimal,
    max_quote_age_ms: int,
    active: dict[str, ActiveSignal],
    stats: EventStats,
) -> None:
    calc_start_ns = time.perf_counter_ns()
    now_ms = int(time.time() * 1000)
    pq = poly_store.quote(match.poly.slug, max_age_ms=max_quote_age_ms)
    kq = await kalshi_store.quote(match.kalshi.ticker, max_age_ms=max_quote_age_ms)
    stats.scans += 1

    plan: base.ShadowPlan | None = None
    poly_age: int | None = None
    kalshi_age: int | None = None
    cross_skew: int | None = None
    if pq is not None and kq is not None and poly_store.connected:
        stats.comparable += 1
        poly_age = max(0, now_ms - pq.received_ms)
        kalshi_age = max(0, now_ms - kq.received_ms)
        cross_skew = abs(pq.received_ms - kq.received_ms)
        plan = base.best_plan(
            pq,
            kq,
            bankroll=bankroll,
            poly_fee_rate=poly_rate,
            kalshi_fee_rate=kalshi_rate,
        )

    calc_end_ns = time.perf_counter_ns()
    calc_us = (calc_end_ns - calc_start_ns) / 1000
    trigger_to_calc_us = (calc_end_ns - trigger.mono_ns) / 1000
    stats.calc_us.append(calc_us)
    stats.trigger_to_calc_us.append(trigger_to_calc_us)

    pair_prefix = f"{match.poly.slug}|{match.kalshi.ticker}|"
    existing = [key for key in active if key.startswith(pair_prefix)]
    current_key: str | None = None

    if plan is not None and plan.profit > 0 and pq is not None and kq is not None:
        stats.positive_observations += 1
        current_key = _signal_key(match, plan.direction)
        max_age = max(poly_age or 0, kalshi_age or 0)
        payload = {
            "ts_ms": now_ms,
            "event": match.poly.event_title,
            "game_date": match.poly.game_date,
            "line": str(match.poly.line),
            "poly_slug": match.poly.slug,
            "kalshi_ticker": match.kalshi.ticker,
            **plan.as_dict(),
            "trigger_venue": trigger.venue,
            "trigger_kind": trigger.kind,
            "poly_age_ms": poly_age,
            "kalshi_age_ms": kalshi_age,
            "cross_receive_skew_ms": cross_skew,
            "kalshi_exchange_ts_ms": kq.exchange_ts_ms,
            "kalshi_seq": kq.seq,
            "calc_us": round(calc_us, 2),
            "trigger_to_calc_us": round(trigger_to_calc_us, 2),
            "quality": (
                "FRESH_250MS" if max_age <= 250
                else "FRESH_1000MS" if max_age <= 1000
                else "RECENT"
            ),
        }
        fp = _fingerprint(payload)
        prior = active.get(current_key)
        if prior is None:
            active[current_key] = ActiveSignal(now_ms, payload, fp)
            stats.opens += 1
            _log_signal("OPEN", payload, lifetime_ms=0)
        elif prior.fingerprint != fp:
            prior.last_payload = payload
            prior.fingerprint = fp
            stats.moves += 1
            _log_signal("MOVE", payload, lifetime_ms=now_ms - prior.opened_ms)

    for key in existing:
        if key == current_key:
            continue
        prior = active.pop(key)
        stats.closes += 1
        close_payload = dict(prior.last_payload)
        close_payload.update({
            "ts_ms": now_ms,
            "close_trigger_venue": trigger.venue,
            "close_trigger_kind": trigger.kind,
            "close_calc_us": round(calc_us, 2),
        })
        _log_signal("CLOSE", close_payload, lifetime_ms=now_ms - prior.opened_ms)


async def _next_update(poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> BookUpdate:
    pt = asyncio.create_task(poly.updates.get())
    kt = asyncio.create_task(kalshi.updates.get())
    done, pending = await asyncio.wait({pt, kt}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def _heartbeat(
    stats: EventStats,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
    active: dict[str, ActiveSignal],
) -> None:
    while True:
        await asyncio.sleep(10)
        payload = stats.payload(poly=poly, kalshi=kalshi)
        payload["active_signals"] = len(active)
        logger.info("EVENT_SUMMARY %s", json.dumps(payload, separators=(",", ":")))


async def _seed_poly(http: httpx.AsyncClient, store: EventingPolyBookStore, slugs: list[str], concurrency: int) -> int:
    sem = asyncio.Semaphore(concurrency)
    store.notifications_enabled = False
    try:
        results = await asyncio.gather(*[_fetch_poly_book(http, store, slug, sem) for slug in slugs])
        return sum(bool(x) for x in results)
    finally:
        store.notifications_enabled = True


async def run(args: argparse.Namespace) -> None:
    bankroll = Decimal(str(args.bankroll))
    poly_rate = Decimal(os.getenv("POLYMARKET_TAKER_FEE_RATE", args.poly_taker_rate))
    kalshi_rate = Decimal(os.getenv("KALSHI_TAKER_FEE_RATE", args.kalshi_taker_rate))
    logger.info(
        "EVENT_START shadow_only=true bankroll=%s poly_taker_rate=%s kalshi_taker_rate=%s max_quote_age_ms=%d",
        bankroll, poly_rate, kalshi_rate, args.max_quote_age_ms,
    )

    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise RuntimeError("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required for market websocket")

    async with httpx.AsyncClient(timeout=15.0) as http:
        discovered = await strict_discover_matches(http)
        if not discovered:
            raise RuntimeError("No strict Polymarket/Kalshi matches discovered")

        # Seed Polymarket once, then fail closed on any strict pair that currently
        # has no executable two-sided public book. This avoids one stale/empty market
        # preventing the entire event-driven feed from becoming ready.
        poly_store = EventingPolyBookStore()
        discovered_slugs = [m.poly.slug for m in discovered]
        seeded = await _seed_poly(http, poly_store, discovered_slugs, args.rest_concurrency)
        matches = [
            m for m in discovered
            if poly_store.quote(m.poly.slug, max_age_ms=60000) is not None
        ]
        excluded = [m for m in discovered if m not in matches]
        if not matches:
            raise RuntimeError("No strict pairs have a current two-sided Polymarket book")
        for match in excluded:
            logger.warning(
                "PAIR_EXCLUDED_NO_POLY_BOOK %s",
                json.dumps({
                    "event": match.poly.event_title,
                    "line": str(match.poly.line),
                    "poly_slug": match.poly.slug,
                    "kalshi_ticker": match.kalshi.ticker,
                }, separators=(",", ":")),
            )

        slug_to_match = {m.poly.slug: m for m in matches}
        ticker_to_match = {m.kalshi.ticker: m for m in matches}
        slugs = list(slug_to_match)
        kalshi_store = EventingKalshiCrossBooks(list(ticker_to_match))

        poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
        poly_task = asyncio.create_task(run_poly_ws(poly_client, poly_store, slugs), name="poly_ws")
        kalshi_task = asyncio.create_task(kalshi_store.run(), name="kalshi_ws")

        deadline = time.monotonic() + args.startup_timeout_s
        while time.monotonic() < deadline:
            if poly_task.done():
                raise _task_failure("Polymarket", poly_task)
            if kalshi_task.done():
                raise _task_failure("Kalshi", kalshi_task)
            if poly_store.connected and kalshi_store.initialized_count == len(matches):
                break
            await asyncio.sleep(0.025)
        else:
            raise RuntimeError(
                f"startup readiness timeout: poly_connected={poly_store.connected} "
                f"poly_books={poly_store.quote_count}/{len(matches)} "
                f"kalshi_books={kalshi_store.initialized_count}/{len(matches)} "
                f"seeded={seeded}/{len(discovered)} excluded={len(excluded)}"
            )

        drained_poly = _drain(poly_store.updates)
        drained_kalshi = _drain(kalshi_store.updates)
        logger.info("EVENT_READY %s", json.dumps({
            "strict_discovered": len(discovered),
            "matched_live_books": len(matches),
            "excluded_no_poly_book": len(excluded),
            "seeded_poly_books": seeded,
            "drained_startup_poly_updates": drained_poly,
            "drained_startup_kalshi_updates": drained_kalshi,
            "rest_is_signal_source": False,
        }, separators=(",", ":")))

        stats = EventStats()
        active: dict[str, ActiveSignal] = {}
        heartbeat = asyncio.create_task(_heartbeat(stats, poly_store, kalshi_store, active), name="heartbeat")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass

        try:
            while not stop.is_set():
                update_task = asyncio.create_task(_next_update(poly_store, kalshi_store), name="next_book_update")
                stop_task = asyncio.create_task(stop.wait(), name="stop_wait")
                done, _ = await asyncio.wait(
                    {update_task, stop_task, poly_task, kalshi_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if poly_task in done:
                    update_task.cancel(); stop_task.cancel()
                    await asyncio.gather(update_task, stop_task, return_exceptions=True)
                    raise _task_failure("Polymarket", poly_task)
                if kalshi_task in done:
                    update_task.cancel(); stop_task.cancel()
                    await asyncio.gather(update_task, stop_task, return_exceptions=True)
                    raise _task_failure("Kalshi", kalshi_task)
                if stop_task in done and stop_task.result():
                    update_task.cancel()
                    await asyncio.gather(update_task, return_exceptions=True)
                    break
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)

                trigger = update_task.result()
                stats.triggers += 1
                if trigger.venue == "polymarket":
                    stats.poly_triggers += 1
                    match = slug_to_match.get(trigger.market_id)
                else:
                    stats.kalshi_triggers += 1
                    match = ticker_to_match.get(trigger.market_id)
                if match is not None:
                    await _scan_pair(
                        match, trigger=trigger, poly_store=poly_store, kalshi_store=kalshi_store,
                        bankroll=bankroll, poly_rate=poly_rate, kalshi_rate=kalshi_rate,
                        max_quote_age_ms=args.max_quote_age_ms, active=active, stats=stats,
                    )
        finally:
            heartbeat.cancel(); poly_task.cancel(); kalshi_task.cancel()
            await kalshi_store.close()
            try:
                poly_client.close()
            except Exception:
                pass
            await asyncio.gather(heartbeat, poly_task, kalshi_task, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Event-driven read-only Polymarket/Kalshi MLB total scanner")
    p.add_argument("--bankroll", default="50")
    p.add_argument("--poly-taker-rate", default="0.05")
    p.add_argument("--kalshi-taker-rate", default="0.07")
    p.add_argument("--max-quote-age-ms", type=int, default=1500)
    p.add_argument("--startup-timeout-s", type=float, default=20.0)
    p.add_argument("--rest-concurrency", type=int, default=12)
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
