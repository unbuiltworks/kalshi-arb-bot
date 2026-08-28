"""Event-driven, read-only MLB prop hot scanner for Polymarket US <-> Kalshi.

This module is deliberately isolated from all order-entry code. Discovery and MLB
participation checks happen once per session; already-cleared exact counterparts are
then sharded and watched through venue market-data WebSockets. Every accepted book
mutation recalculates the <=$1 and <=$5 conservative paired plans immediately.

A HOT_READY log means only that the observed market-data state passed identity,
participation, fee, depth and conservative economics checks. It is not an order and
never authorizes one.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import logging
import os
import signal
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v3 as v3
from src import strict_mlb_prop_snapshot_v4 as v4
from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, proof_dict
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_event_shadow")
base._fetch_poly_mlb = _fetch_poly_mlb_search
MIN_READY_NET = Decimal("0.03")


@dataclass(frozen=True)
class HotCandidate:
    candidate: base.StrictCandidate
    fee_multiplier: Decimal
    fee_type: str
    proof: dict[str, Any]

    @property
    def key(self) -> str:
        return self.candidate.key


@dataclass
class ActiveSignal:
    opened_ms: int
    fingerprint: tuple[Any, ...]
    payload: dict[str, Any]


class Stats:
    def __init__(self) -> None:
        self.started_ms = int(time.time() * 1000)
        self.triggers = 0
        self.scans = 0
        self.comparable = 0
        self.positive = 0
        self.ready = 0
        self.opens = 0
        self.moves = 0
        self.closes = 0

    def payload(self, poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> dict[str, Any]:
        return {
            "uptime_s": round((int(time.time() * 1000) - self.started_ms) / 1000, 1),
            "triggers": self.triggers,
            "scans": self.scans,
            "comparable": self.comparable,
            "positive_observations": self.positive,
            "ready_observations": self.ready,
            "opens": self.opens,
            "moves": self.moves,
            "closes": self.closes,
            "poly_ws_updates": poly.ws_updates,
            "kalshi_initialized_books": kalshi.initialized_count,
            "poly_queue": poly.updates.qsize(),
            "kalshi_queue": kalshi.updates.qsize(),
            "poly_dropped_updates": poly.dropped_updates,
            "kalshi_dropped_updates": kalshi.dropped_updates,
            "read_only": True,
            "order_entry": False,
        }


def _shard(candidate: base.StrictCandidate, shard_count: int) -> int:
    digest = hashlib.sha256(candidate.key.encode()).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def _top_book(quote: Any) -> base.TopBook:
    return base.TopBook(
        yes_ask=quote.yes_ask,
        yes_depth=quote.yes_depth,
        no_ask=quote.no_ask,
        no_depth=quote.no_depth,
    )


def _fingerprint(payload: dict[str, Any]) -> tuple[Any, ...]:
    plan = payload.get("probe_5usd") or {}
    return (
        plan.get("direction"), plan.get("quantity"), plan.get("poly_price"),
        plan.get("kalshi_price"), plan.get("conservative_locked_profit"),
        payload.get("ready"),
    )


def _signal_key(hot: HotCandidate) -> str:
    c = hot.candidate
    return f"{c.poly_slug}|{c.kalshi_ticker}"


async def _seed_poly(
    http: httpx.AsyncClient,
    store: EventingPolyBookStore,
    slugs: list[str],
    concurrency: int,
) -> int:
    sem = asyncio.Semaphore(concurrency)
    store.notifications_enabled = False
    try:
        rows = await asyncio.gather(*[_fetch_poly_book(http, store, slug, sem) for slug in slugs])
        return sum(bool(x) for x in rows)
    finally:
        store.notifications_enabled = True


async def _discover_hot(http: httpx.AsyncClient, shard_index: int, shard_count: int) -> tuple[list[HotCandidate], dict[str, Any]]:
    poly_markets, kalshi_events = await asyncio.gather(
        base._fetch_poly_mlb(http), base._fetch_kalshi_events(http)
    )
    candidates = base.discover_strict_candidates(poly_markets, kalshi_events)
    games = await fetch_game_boxscores(http, {c.game_date for c in candidates})

    series_meta: dict[str, dict[str, Any]] = {}
    for series in sorted(set(base.FAMILY_SERIES.values())):
        body, error = await v3._fetch_optional(http, f"{base.KALSHI}/series/{series}")
        if body is not None and error is None:
            series_meta[series] = dict(body.get("series") or {})

    cleared: list[HotCandidate] = []
    pending = 0
    fee_blocked = 0
    for c in candidates:
        proof = participation_proof(
            family=c.family,
            player_name=c.player,
            poly_slug=c.poly_slug,
            games=games,
        )
        assessment = base.assess_rule_equivalence(c.family, proof)
        if not assessment.settlement_equivalent_now:
            pending += 1
            continue
        meta = series_meta.get(base.FAMILY_SERIES[c.family]) or {}
        fee_type = str(meta.get("fee_type") or "")
        multiplier = base._d(meta.get("fee_multiplier"))
        if (
            fee_type not in {"quadratic", "quadratic_with_maker_fees"}
            or multiplier is None
            or not (Decimal("0") <= multiplier <= Decimal("100"))
        ):
            fee_blocked += 1
            continue
        if _shard(c, shard_count) != shard_index:
            continue
        cleared.append(HotCandidate(c, multiplier, fee_type, proof_dict(proof)))

    cleared.sort(key=lambda h: h.key)
    summary = {
        "strict_candidates": len(candidates),
        "participation_pending": pending,
        "fee_blocked": fee_blocked,
        "hot_shard_candidates": len(cleared),
        "shard_index": shard_index,
        "shard_count": shard_count,
        "mlb_games_with_boxscores": len(games),
    }
    return cleared, summary


async def _next_update(poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> BookUpdate:
    pt = asyncio.create_task(poly.updates.get())
    kt = asyncio.create_task(kalshi.updates.get())
    done, pending = await asyncio.wait({pt, kt}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


def _drain(queue: asyncio.Queue[BookUpdate]) -> int:
    n = 0
    while True:
        try:
            queue.get_nowait()
            n += 1
        except asyncio.QueueEmpty:
            return n


async def _evaluate(
    hot: HotCandidate,
    trigger: BookUpdate,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
    max_quote_age_ms: int,
    active: dict[str, ActiveSignal],
    stats: Stats,
) -> None:
    c = hot.candidate
    now_ms = int(time.time() * 1000)
    pq = poly.quote(c.poly_slug, max_age_ms=max_quote_age_ms)
    kq = await kalshi.quote(c.kalshi_ticker, max_age_ms=max_quote_age_ms)
    stats.scans += 1
    key = _signal_key(hot)

    if pq is None or kq is None or not poly.connected:
        prior = active.pop(key, None)
        if prior is not None:
            stats.closes += 1
            logger.info("MLB_PROP_HOT_CLOSE %s", json.dumps({
                **prior.payload,
                "closed_ms": now_ms,
                "lifetime_ms": now_ms - prior.opened_ms,
                "close_reason": "BOOK_NOT_FRESH_OR_TWO_SIDED",
            }, separators=(",", ":")))
        return

    stats.comparable += 1
    pbook = _top_book(pq)
    kbook = _top_book(kq)
    probe1 = v4.best_capped_plan(
        pbook, kbook, kalshi_fee_multiplier=hot.fee_multiplier, capital_cap=Decimal("1")
    )
    probe5 = v4.best_capped_plan(
        pbook, kbook, kalshi_fee_multiplier=hot.fee_multiplier, capital_cap=Decimal("5")
    )
    if probe5 is None:
        prior = active.pop(key, None)
        if prior is not None:
            stats.closes += 1
            logger.info("MLB_PROP_HOT_CLOSE %s", json.dumps({
                **prior.payload,
                "closed_ms": now_ms,
                "lifetime_ms": now_ms - prior.opened_ms,
                "close_reason": "EDGE_CLOSED",
            }, separators=(",", ":")))
        return

    stats.positive += 1
    profit5 = Decimal(probe5["conservative_locked_profit"])
    ready = profit5 >= MIN_READY_NET
    if ready:
        stats.ready += 1

    payload = {
        "ts_ms": now_ms,
        "family": c.family,
        "player": c.player,
        "threshold": str(c.threshold),
        "game_date": c.game_date,
        "poly_slug": c.poly_slug,
        "kalshi_ticker": c.kalshi_ticker,
        "rule_status": "EQUIVALENT_AFTER_PARTICIPATION",
        "participation_proof": hot.proof,
        "fee_type": hot.fee_type,
        "fee_multiplier": str(hot.fee_multiplier),
        "probe_1usd": probe1,
        "probe_5usd": probe5,
        "ready": ready,
        "preferred_net_threshold": str(MIN_READY_NET),
        "trigger_venue": trigger.venue,
        "trigger_kind": trigger.kind,
        "poly_age_ms": max(0, now_ms - pq.received_ms),
        "kalshi_age_ms": max(0, now_ms - kq.received_ms),
        "cross_receive_skew_ms": abs(pq.received_ms - kq.received_ms),
        "kalshi_exchange_ts_ms": kq.exchange_ts_ms,
        "read_only": True,
        "order_entry": False,
    }
    fp = _fingerprint(payload)
    prior = active.get(key)
    kind = "OPEN" if prior is None else "MOVE"
    if prior is None:
        active[key] = ActiveSignal(now_ms, fp, payload)
        stats.opens += 1
    elif prior.fingerprint == fp:
        return
    else:
        prior.fingerprint = fp
        prior.payload = payload
        stats.moves += 1

    logger.info("MLB_PROP_HOT_%s %s", "READY" if ready else kind, json.dumps({
        "kind": kind,
        "lifetime_ms": 0 if prior is None else now_ms - prior.opened_ms,
        **payload,
    }, separators=(",", ":")))


async def _heartbeat(
    stats: Stats,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
    active: dict[str, ActiveSignal],
    discovery: dict[str, Any],
) -> None:
    while True:
        await asyncio.sleep(10)
        logger.info("MLB_PROP_HOT_SUMMARY %s", json.dumps({
            **discovery,
            **stats.payload(poly, kalshi),
            "active_signals": len(active),
        }, separators=(",", ":")))


async def run_session(args: argparse.Namespace) -> None:
    if args.shard_count < 1 or not (0 <= args.shard_index < args.shard_count):
        raise ValueError("shard-index must be in [0, shard-count)")
    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise RuntimeError("Polymarket market-data credentials are required")

    async with httpx.AsyncClient(timeout=20.0) as http:
        hot, discovery = await _discover_hot(http, args.shard_index, args.shard_count)
        if not hot:
            logger.warning("MLB_PROP_HOT_EMPTY %s", json.dumps(discovery, separators=(",", ":")))
            await asyncio.sleep(args.empty_sleep_s)
            return

        poly = EventingPolyBookStore()
        seeded = await _seed_poly(http, poly, [h.candidate.poly_slug for h in hot], args.rest_concurrency)
        live = [h for h in hot if poly.quote(h.candidate.poly_slug, max_age_ms=60000) is not None]
        if not live:
            raise RuntimeError("No hot-shard Polymarket books were two-sided at startup")

        slug_map = {h.candidate.poly_slug: h for h in live}
        ticker_map = {h.candidate.kalshi_ticker: h for h in live}
        kalshi = EventingKalshiCrossBooks(list(ticker_map))
        poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
        poly_task = asyncio.create_task(run_poly_ws(poly_client, poly, list(slug_map)), name="poly_ws")
        kalshi_task = asyncio.create_task(kalshi.run(), name="kalshi_ws")

        deadline = time.monotonic() + args.startup_timeout_s
        while time.monotonic() < deadline:
            if poly_task.done() or kalshi_task.done():
                break
            if poly.connected and kalshi.initialized_count > 0:
                break
            await asyncio.sleep(0.025)
        if not poly.connected or kalshi.initialized_count == 0:
            poly_task.cancel(); kalshi_task.cancel()
            await kalshi.close()
            try:
                poly_client.close()
            except Exception:
                pass
            await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)
            raise RuntimeError(
                f"hot startup timeout poly_connected={poly.connected} "
                f"kalshi_initialized={kalshi.initialized_count}/{len(live)}"
            )

        discovery.update({
            "seeded_poly_books": seeded,
            "live_hot_pairs": len(live),
            "kalshi_initialized_at_ready": kalshi.initialized_count,
            "max_quote_age_ms": args.max_quote_age_ms,
            "session_seconds": args.session_seconds,
        })
        _drain(poly.updates); _drain(kalshi.updates)
        logger.info("MLB_PROP_HOT_READY_FEED %s", json.dumps({
            **discovery, "read_only": True, "order_entry": False
        }, separators=(",", ":")))

        stats = Stats()
        active: dict[str, ActiveSignal] = {}
        heartbeat = asyncio.create_task(_heartbeat(stats, poly, kalshi, active, discovery))
        end_at = time.monotonic() + args.session_seconds
        try:
            while time.monotonic() < end_at:
                trigger = await asyncio.wait_for(_next_update(poly, kalshi), timeout=5.0)
                stats.triggers += 1
                hot_row = slug_map.get(trigger.market_id) if trigger.venue == "polymarket" else ticker_map.get(trigger.market_id)
                if hot_row is not None:
                    await _evaluate(hot_row, trigger, poly, kalshi, args.max_quote_age_ms, active, stats)
        except asyncio.TimeoutError:
            pass
        finally:
            heartbeat.cancel(); poly_task.cancel(); kalshi_task.cancel()
            await kalshi.close()
            try:
                poly_client.close()
            except Exception:
                pass
            await asyncio.gather(heartbeat, poly_task, kalshi_task, return_exceptions=True)


async def run(args: argparse.Namespace) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    while not stop.is_set():
        try:
            await run_session(args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("MLB_PROP_HOT_SESSION_ERROR %s", exc)
            await asyncio.sleep(args.error_backoff_s)
        if not stop.is_set():
            await asyncio.sleep(args.recycle_sleep_s)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only event-driven MLB prop hot scanner")
    p.add_argument("--shard-index", type=int, default=0)
    p.add_argument("--shard-count", type=int, default=4)
    p.add_argument("--max-quote-age-ms", type=int, default=2500)
    p.add_argument("--startup-timeout-s", type=float, default=25.0)
    p.add_argument("--rest-concurrency", type=int, default=8)
    p.add_argument("--session-seconds", type=float, default=180.0)
    p.add_argument("--recycle-sleep-s", type=float, default=1.0)
    p.add_argument("--empty-sleep-s", type=float, default=15.0)
    p.add_argument("--error-backoff-s", type=float, default=5.0)
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
