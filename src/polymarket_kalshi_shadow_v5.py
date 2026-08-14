"""Event-driven, read-only Polymarket US <-> Kalshi MLB total scanner.

This is the latency-measurement successor to the 50ms polling shadow scanner.
Every Polymarket or Kalshi book mutation wakes only the affected strict-matched
pair, recalculates both arb directions immediately, and records calculation
latency plus quote ages. REST is used only to seed Polymarket books at startup;
REST refreshes never create trading signals.

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

from src import polymarket_kalshi_shadow as base
from src import polymarket_kalshi_shadow_v2 as calc
from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.polymarket_kalshi_shadow_v4 import strict_discover_matches
from src.polymarket_us_market_ws import poly_ws_loop, seed_poly_rest

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
        self.positive_observations = 0
        self.opens = 0
        self.moves = 0
        self.closes = 0
        self.calc_us: deque[float] = deque(maxlen=20000)

    def add_latency(self, value_us: float) -> None:
        self.calc_us.append(value_us)

    def percentile(self, p: float) -> float | None:
        if not self.calc_us:
            return None
        values = sorted(self.calc_us)
        idx = min(len(values) - 1, max(0, round((len(values) - 1) * p)))
        return round(values[idx], 2)

    def payload(self, *, poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> dict[str, Any]:
        return {
            "uptime_s": round((int(time.time() * 1000) - self.started_ms) / 1000, 1),
            "triggers": self.triggers,
            "poly_triggers": self.poly_triggers,
            "kalshi_triggers": self.kalshi_triggers,
            "scans": self.scans,
            "positive_observations": self.positive_observations,
            "opens": self.opens,
            "moves": self.moves,
            "closes": self.closes,
            "calc_us_p50": self.percentile(0.50),
            "calc_us_p95": self.percentile(0.95),
            "calc_us_p99": self.percentile(0.99),
            "poly_queue": poly.updates.qsize(),
            "kalshi_queue": kalshi.updates.qsize(),
            "poly_dropped_updates": poly.dropped_updates,
            "kalshi_dropped_updates": kalshi.dropped_updates,
            "poly_ws_updates": poly.ws_updates,
            "kalshi_initialized_books": kalshi.initialized_count,
        }


def _fingerprint(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        payload["direction"],
        payload["quantity"],
        payload["poly_price"],
        payload["kalshi_price"],
        payload["profit"],
    )


def _signal_key(match: base.Match, direction: str) -> str:
    return f"{match.poly.slug}|{match.kalshi.ticker}|{direction}"


def _log_signal(kind: str, payload: dict[str, Any], *, lifetime_ms: int) -> None:
    body = {"kind": kind, "lifetime_ms": lifetime_ms, **payload}
    logger.info("EVENT_SHADOW %s", json.dumps(body, separators=(",", ":")))


def _drain(queue: asyncio.Queue[BookUpdate]) -> int:
    count = 0
    while True:
        try:
            queue.get_nowait()
            count += 1
        except asyncio.QueueEmpty:
            return count


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
    calc_start = time.perf_counter_ns()
    now_ms = int(time.time() * 1000)
    pq = poly_store.quote(match.poly.slug, max_age_ms=max_quote_age_ms)
    kq = await kalshi_store.quote(match.kalshi.ticker, max_age_ms=max_quote_age_ms)
    stats.scans += 1

    current: dict[str, dict[str, Any]] = {}
    if pq is not None and kq is not None and poly_store.connected:
        poly_age = max(0, now_ms - pq.received_ms)
        kalshi_age = max(0, now_ms - kq.received_ms)
        cross_skew = abs(pq.received_ms - kq.received_ms)

        for direction in ("POLY_OVER_KALSHI_UNDER", "KALSHI_OVER_POLY_UNDER"):
            opp = calc.calculate_opportunity(
                match,
                pq,
                kq,
                direction=direction,
                bankroll=bankroll,
                poly_rate=poly_rate,
                kalshi_rate=kalshi_rate,
            )
            if opp is None or opp.profit <= 0:
                continue
            stats.positive_observations += 1
            calc_us = (time.perf_counter_ns() - calc_start) / 1000
            trigger_to_calc_us = (time.perf_counter_ns() - trigger.mono_ns) / 1000
            stats.add_latency(calc_us)
            payload = {
                "ts_ms": now_ms,
                "event": match.poly.event_title,
                "game_date": match.poly.game_date,
                "line": str(match.poly.line),
                "poly_slug": match.poly.slug,
                "kalshi_ticker": match.kalshi.ticker,
                **opp.as_dict(),
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
                    "FRESH_250MS" if max(poly_age, kalshi_age) <= 250
                    else "FRESH_1000MS" if max(poly_age, kalshi_age) <= 1000
                    else "RECENT"
                ),
            }
            current[_signal_key(match, direction)] = payload

    pair_prefix = f"{match.poly.slug}|{match.kalshi.ticker}|"
    existing_pair_keys = [key for key in active if key.startswith(pair_prefix)]

    for key, payload in current.items():
        fp = _fingerprint(payload)
        prior = active.get(key)
        if prior is None:
            active[key] = ActiveSignal(opened_ms=now_ms, last_payload=payload, fingerprint=fp)
            stats.opens += 1
            _log_signal("OPEN", payload, lifetime_ms=0)
        elif fp != prior.fingerprint:
            prior.last_payload = payload
            prior.fingerprint = fp
            stats.moves += 1
            _log_signal("MOVE", payload, lifetime_ms=now_ms - prior.opened_ms)

    for key in existing_pair_keys:
        if key in current:
            continue
        prior = active.pop(key)
        stats.closes += 1
        close_payload = dict(prior.last_payload)
        close_payload["ts_ms"] = now_ms
        close_payload["close_trigger_venue"] = trigger.venue
        close_payload["close_trigger_kind"] = trigger.kind
        _log_signal("CLOSE", close_payload, lifetime_ms=now_ms - prior.opened_ms)


async def _next_update(
    poly_store: EventingPolyBookStore,
    kalshi_store: EventingKalshiCrossBooks,
) -> BookUpdate:
    poly_task = asyncio.create_task(poly_store.updates.get())
    kalshi_task = asyncio.create_task(kalshi_store.updates.get())
    done, pending = await asyncio.wait(
        {poly_task, kalshi_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            pass
    return next(iter(done)).result()


async def _heartbeat(
    stats: EventStats,
    poly_store: EventingPolyBookStore,
    kalshi_store: EventingKalshiCrossBooks,
    active: dict[str, ActiveSignal],
    *,
    interval_s: float = 10.0,
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        payload = stats.payload(poly=poly_store, kalshi=kalshi_store)
        payload["active_signals"] = len(active)
        logger.info("EVENT_SUMMARY %s", json.dumps(payload, separators=(",", ":")))


async def run(args: argparse.Namespace) -> None:
    bankroll = Decimal(str(args.bankroll))
    poly_rate = Decimal(str(os.getenv("POLYMARKET_TAKER_FEE_RATE", args.poly_taker_rate)))
    kalshi_rate = Decimal(str(os.getenv("KALSHI_TAKER_FEE_RATE", args.kalshi_taker_rate)))
    logger.info(
        "EVENT_START shadow_only=true bankroll=%s poly_taker_rate=%s kalshi_taker_rate=%s max_quote_age_ms=%d",
        bankroll, poly_rate, kalshi_rate, args.max_quote_age_ms,
    )

    async with httpx.AsyncClient(timeout=15.0) as http:
        matches = await strict_discover_matches(http)
        if not matches:
            raise RuntimeError("No strict Polymarket/Kalshi matches discovered")

        slug_to_match = {m.poly.slug: m for m in matches}
        ticker_to_match = {m.kalshi.ticker: m for m in matches}
        poly_store = EventingPolyBookStore(list(slug_to_match))
        kalshi_store = EventingKalshiCrossBooks(list(ticker_to_match))

        # Seed only. Do not let REST create candidate signals.
        poly_store.notifications_enabled = False
        await seed_poly_rest(http, list(slug_to_match), poly_store, concurrency=args.rest_concurrency)
        poly_store.notifications_enabled = True

        poly_task = asyncio.create_task(poly_ws_loop(list(slug_to_match), poly_store))
        kalshi_task = asyncio.create_task(kalshi_store.run())

        # Require the complete first Kalshi snapshot set and a live Polymarket WS
        # before any signal can open. Startup snapshots are drained below.
        deadline = time.monotonic() + args.startup_timeout_s
        while time.monotonic() < deadline:
            if (
                poly_store.connected
                and kalshi_store.initialized_count == len(matches)
                and len(poly_store._books) >= len(matches)
            ):
                break
            await asyncio.sleep(0.025)
        else:
            poly_task.cancel()
            kalshi_task.cancel()
            raise RuntimeError(
                f"startup readiness timeout: poly_connected={poly_store.connected} "
                f"poly_books={len(poly_store._books)}/{len(matches)} "
                f"kalshi_books={kalshi_store.initialized_count}/{len(matches)}"
            )

        drained_poly = _drain(poly_store.updates)
        drained_kalshi = _drain(kalshi_store.updates)
        logger.info(
            "EVENT_READY %s",
            json.dumps({
                "matched": len(matches),
                "drained_startup_poly_updates": drained_poly,
                "drained_startup_kalshi_updates": drained_kalshi,
                "rest_is_signal_source": False,
            }, separators=(",", ":")),
        )

        stats = EventStats()
        active: dict[str, ActiveSignal] = {}
        heartbeat_task = asyncio.create_task(_heartbeat(stats, poly_store, kalshi_store, active))

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass

        try:
            while not stop.is_set():
                update_task = asyncio.create_task(_next_update(poly_store, kalshi_store))
                stop_task = asyncio.create_task(stop.wait())
                done, pending = await asyncio.wait(
                    {update_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if stop_task in done and stop_task.result():
                    break
                trigger = update_task.result()
                stats.triggers += 1
                if trigger.venue == "polymarket":
                    stats.poly_triggers += 1
                    match = slug_to_match.get(trigger.market_id)
                else:
                    stats.kalshi_triggers += 1
                    match = ticker_to_match.get(trigger.market_id)
                if match is None:
                    continue
                await _scan_pair(
                    match,
                    trigger=trigger,
                    poly_store=poly_store,
                    kalshi_store=kalshi_store,
                    bankroll=bankroll,
                    poly_rate=poly_rate,
                    kalshi_rate=kalshi_rate,
                    max_quote_age_ms=args.max_quote_age_ms,
                    active=active,
                    stats=stats,
                )
        finally:
            heartbeat_task.cancel()
            await kalshi_store.close()
            poly_task.cancel()
            kalshi_task.cancel()
            await asyncio.gather(poly_task, kalshi_task, heartbeat_task, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Event-driven read-only Polymarket/Kalshi MLB total scanner")
    p.add_argument("--bankroll", type=str, default="50")
    p.add_argument("--poly-taker-rate", type=str, default="0.05")
    p.add_argument("--kalshi-taker-rate", type=str, default="0.07")
    p.add_argument("--max-quote-age-ms", type=int, default=1500)
    p.add_argument("--startup-timeout-s", type=float, default=15.0)
    p.add_argument("--rest-concurrency", type=int, default=12)
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
