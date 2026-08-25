"""Event-driven Polymarket/Kalshi MLB shadow scanner.

This module is deliberately read-only. REST is used only for discovery and the
initial Polymarket seed; after readiness, quote changes are driven by venue
websocket mutations and measured with monotonic receive timestamps.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
import logging
import os
import signal
import statistics
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.polymarket_kalshi_shadow_v2 import run_poly_ws
from src.polymarket_kalshi_shadow_v4 import Match, _fetch_poly_book, strict_discover_matches
from src.sports_kp_shadow import evaluate_both_directions

logger = logging.getLogger("kalshi_arb.polymarket_kalshi_shadow_v5")
DEDICATED_KALSHI_WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"


@dataclass
class ActiveSignal:
    opened_ms: int
    last_payload: dict[str, Any]
    fingerprint: str


@dataclass
class EventStats:
    triggers: int = 0
    evaluations: int = 0
    comparable_evaluations: int = 0
    positive_observations: int = 0
    opens: int = 0
    moves: int = 0
    closes: int = 0
    calc_us: deque[float] = field(default_factory=lambda: deque(maxlen=10000))
    trigger_to_calc_us: deque[float] = field(default_factory=lambda: deque(maxlen=10000))

    @staticmethod
    def _avg(values: deque[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    @staticmethod
    def _p95(values: deque[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
        return round(ordered[idx], 2)

    def payload(self, *, poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> dict[str, Any]:
        return {
            "triggers": self.triggers,
            "evaluations": self.evaluations,
            "comparable_evaluations": self.comparable_evaluations,
            "positive_observations": self.positive_observations,
            "opens": self.opens,
            "moves": self.moves,
            "closes": self.closes,
            "calc_avg_us": self._avg(self.calc_us),
            "calc_p95_us": self._p95(self.calc_us),
            "trigger_to_calc_avg_us": self._avg(self.trigger_to_calc_us),
            "trigger_to_calc_p95_us": self._p95(self.trigger_to_calc_us),
            "poly_dropped_updates": poly.dropped_updates,
            "kalshi_dropped_updates": kalshi.dropped_updates,
        }


def _safe_age(now_ms: int, received_ms: int) -> int:
    return max(0, now_ms - received_ms)


def _task_failure(name: str, task: asyncio.Task[Any]) -> RuntimeError:
    if task.cancelled():
        return RuntimeError(f"{name} websocket task exited unexpectedly: cancelled")
    exc = task.exception()
    if exc is not None:
        return RuntimeError(f"{name} websocket task exited unexpectedly: {exc}")
    return RuntimeError(f"{name} websocket task exited unexpectedly")


def _drain(queue: asyncio.Queue[BookUpdate]) -> int:
    count = 0
    while True:
        try:
            queue.get_nowait()
            count += 1
        except asyncio.QueueEmpty:
            return count


def _fingerprint(payload: dict[str, Any]) -> str:
    stable = {
        k: payload[k]
        for k in (
            "direction",
            "qty",
            "profit",
            "poly_price",
            "kalshi_price",
            "poly_depth",
            "kalshi_depth",
        )
        if k in payload
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _signal_key(match: Match, direction: str) -> str:
    return f"{match.poly.slug}|{match.kalshi.ticker}|{direction}"


def _log_signal(kind: str, payload: dict[str, Any], *, lifetime_ms: int | None = None) -> None:
    row = dict(payload)
    if lifetime_ms is not None:
        row["lifetime_ms"] = lifetime_ms
    logger.info("EVENT_SHADOW_%s %s", kind, json.dumps(row, separators=(",", ":")))


async def _fetch_poly_book(
    http: httpx.AsyncClient,
    store: EventingPolyBookStore,
    slug: str,
    sem: asyncio.Semaphore,
) -> bool:
    url = f"https://api.polymarket.us/v1/markets/{slug}/book"
    async with sem:
        try:
            resp = await http.get(url)
            resp.raise_for_status()
            return store.ingest(resp.json(), source="rest")
        except Exception as exc:
            logger.warning("Polymarket seed failed market=%s err=%s", slug, exc)
            return False


async def _evaluate(
    trigger: BookUpdate,
    match: Match,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
    *,
    bankroll: Decimal,
    poly_rate: Decimal,
    kalshi_rate: Decimal,
    max_quote_age_ms: int,
    stats: EventStats,
    active: dict[str, ActiveSignal],
) -> None:
    stats.triggers += 1
    calc_start_ns = time.perf_counter_ns()
    now_ms = int(time.time() * 1000)
    pq = poly.quote(match.poly.slug, max_age_ms=max_quote_age_ms)
    kq = kalshi.quote(match.kalshi.ticker, max_age_ms=max_quote_age_ms)
    poly_age = _safe_age(now_ms, pq.received_ms) if pq is not None else None
    kalshi_age = _safe_age(now_ms, kq.received_ms) if kq is not None else None
    cross_skew = abs(pq.received_ms - kq.received_ms) if pq is not None and kq is not None else None

    plan = None
    if pq is not None and kq is not None:
        stats.comparable_evaluations += 1
        plan = evaluate_both_directions(
            match.poly.slug,
            match.kalshi.ticker,
            poly_yes_ask=pq.yes_ask,
            poly_yes_depth=pq.yes_depth,
            poly_no_ask=pq.no_ask,
            poly_no_depth=pq.no_depth,
            kalshi_yes_ask=kq.yes_ask,
            kalshi_yes_depth=kq.yes_depth,
            kalshi_no_ask=kq.no_ask,
            kalshi_no_depth=kq.no_depth,
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
    kalshi_ws_url = os.getenv("KALSHI_CENSUS_WS_URL", DEDICATED_KALSHI_WS_URL).strip() or DEDICATED_KALSHI_WS_URL
    logger.info(
        "EVENT_START shadow_only=true bankroll=%s poly_taker_rate=%s kalshi_taker_rate=%s max_quote_age_ms=%d kalshi_ws_url=%s",
        bankroll, poly_rate, kalshi_rate, args.max_quote_age_ms, kalshi_ws_url,
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
        kalshi_store = EventingKalshiCrossBooks(list(ticker_to_match), ws_url=kalshi_ws_url)

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
            "kalshi_ws_url": kalshi_ws_url,
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
                    raise _task_failure("Polymarket", poly_task)
                if kalshi_task in done:
                    raise _task_failure("Kalshi", kalshi_task)
                if stop_task in done:
                    update_task.cancel()
                    await asyncio.gather(update_task, return_exceptions=True)
                    break
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
                update = update_task.result()
                match = slug_to_match.get(update.market_id) if update.venue == "polymarket" else ticker_to_match.get(update.market_id)
                if match is None:
                    continue
                await _evaluate(
                    update,
                    match,
                    poly_store,
                    kalshi_store,
                    bankroll=bankroll,
                    poly_rate=poly_rate,
                    kalshi_rate=kalshi_rate,
                    max_quote_age_ms=args.max_quote_age_ms,
                    stats=stats,
                    active=active,
                )
        finally:
            stop.set()
            heartbeat.cancel()
            poly_task.cancel()
            kalshi_task.cancel()
            await asyncio.gather(heartbeat, poly_task, kalshi_task, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Event-driven strict MLB Polymarket/Kalshi shadow scanner")
    parser.add_argument("--bankroll", type=str, default="1")
    parser.add_argument("--poly-taker-rate", type=str, default="0.0")
    parser.add_argument("--kalshi-taker-rate", type=str, default="0.0")
    parser.add_argument("--max-quote-age-ms", type=int, default=250)
    parser.add_argument("--rest-concurrency", type=int, default=16)
    parser.add_argument("--startup-timeout-s", type=float, default=30.0)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
