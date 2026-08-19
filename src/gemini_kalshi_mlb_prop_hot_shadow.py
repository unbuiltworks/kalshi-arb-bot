"""Event-driven read-only Gemini <-> Kalshi MLB player-prop hot shadow.

Cold discovery comes from ``gemini_kalshi_mlb_prop_shadow`` and therefore does not
require a Polymarket counterpart. Exact game/player/stat/discrete-threshold matches
are promoted into a hot set. Gemini uses public ``@bookTicker`` WebSockets; Kalshi
uses the authenticated read-only ``orderbook_delta`` WebSocket.

There is no order transport in this module. Positive q=1 economics remain blocked
until Gemini's complete MLB player-prop lifecycle fingerprint is audited against the
paired Kalshi series. Official MLB participation is tracked only as rule context.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx
import websockets

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks
from src.gemini_kalshi_execution_shadow import TopOfBook, evaluate_pair, gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker
from src.gemini_kalshi_mlb_prop_shadow import _candidate_sides
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, proof_dict
from src.mlb_prop_rule_certifications import assess_prop_pair
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events
from src.three_venue_mlb_prop_safe_scan import _kalshi_event_map
from src.three_venue_mlb_snapshot_pricing import _series_multipliers
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal
ONE = D("1")
SHARD_SIZE = 8
logger = logging.getLogger("kalshi_arb.gemini_kalshi_mlb_prop_hot_shadow")


@dataclass(frozen=True)
class HotProp:
    key: str
    gemini_side: Any
    kalshi_side: Any
    family: str
    player: str
    game_slug: str
    kalshi_fee_multiplier: Decimal


class GeminiPropBooks:
    """Adaptive public Gemini prediction bookTicker store with bounded wakeups."""

    def __init__(self, symbols: list[str], *, max_queue: int = 20000) -> None:
        self.symbols = sorted(set(symbols))
        self.books: dict[str, TopOfBook] = {}
        self.updates: asyncio.Queue[BookUpdate] = asyncio.Queue(maxsize=max_queue)
        self.connected_symbols: set[str] = set()
        self.messages = 0
        self.book_updates = 0
        self.dropped_updates = 0
        self.last_message_ms = 0

    def _notify(self, symbol: str, received_ms: int) -> None:
        row = BookUpdate("gemini", symbol, received_ms, time.perf_counter_ns(), "bookTicker")
        try:
            self.updates.put_nowait(row)
        except asyncio.QueueFull:
            try:
                self.updates.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped_updates += 1
            try:
                self.updates.put_nowait(row)
            except asyncio.QueueFull:
                self.dropped_updates += 1

    async def _leaf(self, symbols: list[str], shard: str) -> None:
        wanted = {symbol.lower(): symbol for symbol in symbols}
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    GEMINI_WS,
                    open_timeout=5.0,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    await ws.send(json.dumps({
                        "id": f"mlb-gk-prop-{shard}",
                        "method": "SUBSCRIBE",
                        "params": [f"{symbol}@bookTicker" for symbol in symbols],
                    }, separators=(",", ":")))
                    accepted = False
                    backoff = 1.0
                    async for raw in ws:
                        received_ms = int(time.time() * 1000)
                        self.last_message_ms = received_ms
                        self.messages += 1
                        payload = json.loads(raw)
                        if payload.get("status") is not None:
                            if int(payload.get("status")) >= 400:
                                raise RuntimeError(f"subscription status={payload.get('status')}")
                            accepted = True
                            self.connected_symbols.update(symbols)
                            continue
                        symbol = wanted.get(str(payload.get("s") or "").lower())
                        if symbol is None:
                            continue
                        if not accepted:
                            accepted = True
                            self.connected_symbols.update(symbols)
                        try:
                            book = parse_gemini_book_ticker(payload, received_ms=received_ms)
                        except (KeyError, TypeError, ValueError):
                            continue
                        self.books[symbol] = book
                        self.book_updates += 1
                        self._notify(symbol, received_ms)
            except asyncio.CancelledError:
                for symbol in symbols:
                    self.connected_symbols.discard(symbol)
                raise
            except RuntimeError as exc:
                for symbol in symbols:
                    self.connected_symbols.discard(symbol)
                if "subscription status=" in str(exc) and len(symbols) > 1:
                    mid = max(1, len(symbols) // 2)
                    left, right = symbols[:mid], symbols[mid:]
                    logger.warning("MLB_GK_PROP_GEMINI_ADAPTIVE_SPLIT %s", json.dumps({
                        "shard": shard, "symbols": len(symbols), "left": len(left),
                        "right": len(right), "reason": str(exc), "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":")))
                    await asyncio.gather(self._leaf(left, shard + "L"), self._leaf(right, shard + "R"))
                    return
                logger.warning("MLB_GK_PROP_GEMINI_RECONNECT shard=%s symbols=%d error=%s retry_s=%.1f", shard, len(symbols), type(exc).__name__, backoff)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)
            except Exception as exc:
                for symbol in symbols:
                    self.connected_symbols.discard(symbol)
                logger.warning("MLB_GK_PROP_GEMINI_RECONNECT shard=%s symbols=%d error=%s retry_s=%.1f", shard, len(symbols), type(exc).__name__, backoff)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    async def run(self) -> None:
        if not self.symbols:
            raise RuntimeError("no Gemini prop symbols")
        shards = [self.symbols[i:i + SHARD_SIZE] for i in range(0, len(self.symbols), SHARD_SIZE)]
        logger.info("MLB_GK_PROP_GEMINI_SHARDS %s", json.dumps({
            "symbols": len(self.symbols), "shard_size": SHARD_SIZE, "shards": len(shards),
            "fallback": "BISECT_TO_SINGLE_SYMBOL", "read_only": True, "order_entry": False,
        }, separators=(",", ":")))
        tasks = [asyncio.create_task(self._leaf(rows, str(idx))) for idx, rows in enumerate(shards)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def quote(self, symbol: str, *, max_age_ms: int) -> TopOfBook | None:
        row = self.books.get(symbol)
        if row is None or int(time.time() * 1000) - row.ts_ms > max_age_ms:
            return None
        return row


def orient_tob(book: TopOfBook, source_side: str) -> TopOfBook:
    """Orient raw contract YES-space into canonical >=N YES-space."""
    if source_side in {"YES", "CONTRACT"}:
        return book
    if source_side != "NO":
        raise ValueError(f"unsupported source_side={source_side}")
    return TopOfBook(
        venue=book.venue,
        market_id=book.market_id,
        ts_ms=book.ts_ms,
        yes_bid=ONE - book.yes_ask,
        yes_bid_size=book.yes_ask_size,
        yes_ask=ONE - book.yes_bid,
        yes_ask_size=book.yes_bid_size,
    )


def kalshi_tob(quote: Any, ticker: str) -> TopOfBook:
    """Transform Kalshi YES/NO asks into a YES-space top of book."""
    return TopOfBook(
        venue="kalshi",
        market_id=ticker,
        ts_ms=int(quote.received_ms),
        yes_bid=ONE - D(str(quote.no_ask)),
        yes_bid_size=D(str(quote.no_depth)),
        yes_ask=D(str(quote.yes_ask)),
        yes_ask_size=D(str(quote.yes_depth)),
    )


async def discover_hot(http: httpx.AsyncClient) -> tuple[dict[str, HotProp], dict[tuple[str, str], tuple[str, ...]], dict[str, Any]]:
    gemini_events, kalshi_events = await asyncio.gather(fetch_gemini_mlb(http), _fetch_kalshi_events(http))
    sides, _venues, joins, context = _candidate_sides(kalshi_events, gemini_events)
    grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for side in sides:
        grouped[side.contract_key][side.identity.venue].append(side)

    _event_by_ticker, ticker_to_series = _kalshi_event_map(kalshi_events)
    series = sorted({
        ticker_to_series.get(side.venue_market_id, "")
        for side in sides
        if side.identity.venue == "kalshi" and ticker_to_series.get(side.venue_market_id, "")
    })
    fee_errors: list[dict[str, str]] = []
    multipliers = await _series_multipliers(http, asyncio.Semaphore(8), series, fee_errors)

    hot: dict[str, HotProp] = {}
    ambiguous = 0
    fee_blocked = 0
    for key, by_venue in grouped.items():
        g_unique = {(row.venue_market_id, row.side): row for row in by_venue.get("gemini", [])}
        k_unique = {(row.venue_market_id, row.side): row for row in by_venue.get("kalshi", [])}
        if len(g_unique) != 1 or len(k_unique) != 1:
            if g_unique or k_unique:
                ambiguous += 1
            continue
        gside = next(iter(g_unique.values()))
        kside = next(iter(k_unique.values()))
        multiplier = multipliers.get(ticker_to_series.get(kside.venue_market_id, ""))
        row_context = context.get(key)
        if multiplier is None:
            fee_blocked += 1
            continue
        if row_context is None:
            continue
        hot[key] = HotProp(
            key=key,
            gemini_side=gside,
            kalshi_side=kside,
            family=row_context["family"],
            player=row_context["player"],
            game_slug=row_context["game_slug"],
            kalshi_fee_multiplier=D(str(multiplier)),
        )

    market_to_keys_raw: dict[tuple[str, str], set[str]] = defaultdict(set)
    for key, row in hot.items():
        market_to_keys_raw[("gemini", row.gemini_side.venue_market_id)].add(key)
        market_to_keys_raw[("kalshi", row.kalshi_side.venue_market_id)].add(key)
    market_to_keys = {marker: tuple(sorted(keys)) for marker, keys in market_to_keys_raw.items()}
    diagnostics = {
        "gemini_events": len(gemini_events),
        "kalshi_prop_events": len(kalshi_events),
        "exact_join_rows": len(joins),
        "hot_keys": len(hot),
        "gemini_symbols": len({row.gemini_side.venue_market_id for row in hot.values()}),
        "kalshi_tickers": len({row.kalshi_side.venue_market_id for row in hot.values()}),
        "ambiguous_keys_rejected": ambiguous,
        "fee_blocked_keys": fee_blocked,
        "fee_errors": fee_errors[:10],
        "read_only": True,
        "order_entry": False,
    }
    return hot, market_to_keys, diagnostics


async def _next_update(gemini: GeminiPropBooks, kalshi: EventingKalshiCrossBooks) -> BookUpdate:
    gt = asyncio.create_task(gemini.updates.get())
    kt = asyncio.create_task(kalshi.updates.get())
    done, pending = await asyncio.wait((gt, kt), return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def run_session(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        hot, market_to_keys, diagnostics = await discover_hot(http)
    if not hot:
        raise RuntimeError("no exact Gemini/Kalshi MLB prop hot keys")

    symbols = sorted({row.gemini_side.venue_market_id for row in hot.values()})
    tickers = sorted({row.kalshi_side.venue_market_id for row in hot.values()})
    gemini = GeminiPropBooks(symbols)
    kalshi = EventingKalshiCrossBooks(tickers)
    participation: dict[str, Any] = {}
    active: dict[str, dict[str, Any]] = {}
    latencies: deque[float] = deque(maxlen=10000)
    counters = defaultdict(int)
    started_ms = int(time.time() * 1000)
    game_dates = sorted({row.game_slug[-10:] for row in hot.values()})

    async def refresh_participation() -> None:
        while True:
            try:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    games = await fetch_game_boxscores(http, game_dates)
                for key, row in hot.items():
                    participation[key] = participation_proof(
                        family=row.family,
                        player_name=row.player,
                        poly_slug=row.game_slug,
                        games=games,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MLB_GK_PROP_PARTICIPATION_REFRESH_ERROR type=%s", type(exc).__name__)
            await asyncio.sleep(max(2.0, args.participation_refresh_seconds))

    tasks = [
        asyncio.create_task(gemini.run(), name="gemini_prop_books"),
        asyncio.create_task(kalshi.run(), name="kalshi_prop_books"),
        asyncio.create_task(refresh_participation(), name="mlb_participation"),
    ]
    logger.info("MLB_GK_PROP_HOT_READY %s", json.dumps({
        **diagnostics,
        "max_book_age_ms": args.max_book_age_ms,
        "max_quote_skew_ms": args.max_quote_skew_ms,
        "hot_book_age_ms": args.hot_book_age_ms,
        "hot_profit_threshold": str(args.hot_profit),
        "rule_gate": "GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
    }, separators=(",", ":"), default=str))

    last_heartbeat = time.monotonic()
    try:
        while True:
            try:
                trigger = await asyncio.wait_for(_next_update(gemini, kalshi), timeout=1.0)
            except asyncio.TimeoutError:
                trigger = None

            if trigger is not None:
                counters["triggers"] += 1
                for key in market_to_keys.get((trigger.venue, trigger.market_id), ()):
                    t0 = time.perf_counter_ns()
                    counters["evaluations"] += 1
                    row = hot[key]
                    g_raw = gemini.quote(row.gemini_side.venue_market_id, max_age_ms=args.max_book_age_ms)
                    k_quote = await kalshi.quote(row.kalshi_side.venue_market_id, max_age_ms=args.max_book_age_ms)
                    if g_raw is None or k_quote is None:
                        continue
                    g = orient_tob(g_raw, row.gemini_side.side)
                    k = orient_tob(kalshi_tob(k_quote, row.kalshi_side.venue_market_id), row.kalshi_side.side)
                    counters["comparable"] += 1

                    def kfee(price: Decimal, quantity: Decimal) -> Decimal:
                        return kalshi_quadratic_taker_fee(price, quantity, fee_multiplier=row.kalshi_fee_multiplier)

                    candidates = evaluate_pair(
                        g,
                        k,
                        gemini_fee=gemini_predictions_taker_fee,
                        kalshi_fee=kfee,
                        max_quote_skew_ms=args.max_quote_skew_ms,
                        max_quantity=ONE,
                    )
                    now_ms = int(time.time() * 1000)
                    prior = active.get(key)
                    if not candidates:
                        if prior is not None:
                            counters["closes"] += 1
                            logger.info("MLB_GK_PROP_HOT_CLOSE %s", json.dumps({
                                **prior,
                                "event": "CLOSE",
                                "closed_ms": now_ms,
                                "lifetime_ms": now_ms - int(prior["opened_ms"]),
                                "close_trigger_venue": trigger.venue,
                                "read_only": True,
                                "order_entry": False,
                            }, separators=(",", ":"), default=str))
                            active.pop(key, None)
                        latencies.append((time.perf_counter_ns() - t0) / 1_000_000)
                        continue

                    counters["positive"] += 1
                    best = max(candidates, key=lambda candidate: candidate.locked_profit)
                    proof = participation.get(key)
                    assessment = assess_prop_pair("gemini", "kalshi", row.family, proof)
                    g_age = max(0, now_ms - g.ts_ms)
                    k_age = max(0, now_ms - k.ts_ms)
                    hot_3c = bool(
                        best.locked_profit >= args.hot_profit
                        and best.quote_skew_ms <= args.max_quote_skew_ms
                        and g_age <= args.hot_book_age_ms
                        and k_age <= args.hot_book_age_ms
                    )
                    if hot_3c:
                        counters["hot_3c"] += 1
                    blockers = list(assessment.remaining_blockers)
                    if "GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED" not in blockers:
                        blockers.append("GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED")
                    blockers.append("LIVE_NOT_ARMED")
                    payload = {
                        "event": "OPEN" if prior is None else "MOVE",
                        "ts_ms": now_ms,
                        "opened_ms": now_ms if prior is None else prior["opened_ms"],
                        "settlement_key": key,
                        "family": row.family,
                        "player": row.player,
                        "gemini_symbol": row.gemini_side.venue_market_id,
                        "kalshi_ticker": row.kalshi_side.venue_market_id,
                        "direction": best.direction,
                        "quantity": str(best.quantity),
                        "net_cost": str(best.net_cost),
                        "fees": str(best.fees),
                        "locked_profit": str(best.locked_profit),
                        "roi_on_cost": str(best.roi_on_cost),
                        "quote_skew_ms": best.quote_skew_ms,
                        "gemini_age_ms": g_age,
                        "kalshi_age_ms": k_age,
                        "hot_3c": hot_3c,
                        "participation_proof": proof_dict(proof) if proof is not None else {},
                        "rule_assessment": assessment.as_dict(),
                        "blockers": blockers,
                        "execution_eligible": False,
                        "sendable_now": False,
                        "read_only": True,
                        "order_entry": False,
                    }
                    fingerprint = json.dumps({
                        "direction": payload["direction"],
                        "profit": payload["locked_profit"],
                        "cost": payload["net_cost"],
                        "skew": payload["quote_skew_ms"],
                        "hot_3c": payload["hot_3c"],
                    }, sort_keys=True, separators=(",", ":"))
                    if prior is None:
                        counters["opens"] += 1
                    elif prior.get("fingerprint") != fingerprint:
                        counters["moves"] += 1
                    if prior is None or prior.get("fingerprint") != fingerprint:
                        payload["fingerprint"] = fingerprint
                        active[key] = payload
                        logger.info("MLB_GK_PROP_HOT_%s %s", payload["event"], json.dumps(payload, separators=(",", ":"), default=str))
                        if hot_3c:
                            logger.info("MLB_GK_PROP_HOT_CANDIDATE %s", json.dumps(payload, separators=(",", ":"), default=str))
                    latencies.append((time.perf_counter_ns() - t0) / 1_000_000)

            if time.monotonic() - last_heartbeat >= args.heartbeat_seconds:
                last_heartbeat = time.monotonic()
                values = sorted(latencies)
                p95 = None if not values else values[min(len(values) - 1, max(0, int(len(values) * .95) - 1))]
                logger.info("MLB_GK_PROP_HOT_HEARTBEAT %s", json.dumps({
                    "uptime_s": round((int(time.time() * 1000) - started_ms) / 1000, 1),
                    "triggers": counters["triggers"],
                    "evaluations": counters["evaluations"],
                    "comparable_evaluations": counters["comparable"],
                    "positive_evaluations": counters["positive"],
                    "hot_3c_evaluations": counters["hot_3c"],
                    "opens": counters["opens"],
                    "moves": counters["moves"],
                    "closes": counters["closes"],
                    "active_positive_keys": len(active),
                    "trigger_to_eval_avg_ms": None if not values else round(sum(values) / len(values), 3),
                    "trigger_to_eval_p95_ms": None if p95 is None else round(p95, 3),
                    "gemini_symbols": len(symbols),
                    "gemini_connected_symbols": len(gemini.connected_symbols),
                    "gemini_book_updates": gemini.book_updates,
                    "kalshi_tickers": len(tickers),
                    "kalshi_initialized": kalshi.initialized_count,
                    "rule_gate": "GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":"), default=str))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await kalshi.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-book-age-ms", type=int, default=1000)
    parser.add_argument("--max-quote-skew-ms", type=int, default=50)
    parser.add_argument("--hot-book-age-ms", type=int, default=100)
    parser.add_argument("--hot-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--participation-refresh-seconds", type=float, default=5.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_session(args))


if __name__ == "__main__":
    main()
