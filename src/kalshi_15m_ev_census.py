"""Read-only Kalshi BTC 15-minute venue-vs-spot EV capacity census.

This is the production measurement path when no economically equivalent
Polymarket-US 15-minute BTC contract exists.  It deliberately does not require a
cross-venue pair and it contains no order-entry code.

Measured surfaces:
- a volatility/time-aware proxy probability for the current KXBTC15M contract,
- exact two-sided Kalshi WebSocket touch depth,
- current Kalshi taker fees at q=1/10/50/100/250/500,
- positive-EV episode duration and survival through latency thresholds,
- public-print book refill at 100/250/500/1000/5000 ms,
- low-frequency REST round-trip latency,
- official Kalshi expiration_value minus the common spot proxy final-60 average.

The spot model remains a labelled Coinbase/Kraken proxy.  Positive proxy EV is
not realized profit and is not a settlement lock.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import math
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.eventing_books import EventingKalshiCrossBooks
from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata
from src.pk_crypto_ev_census import (
    BBO,
    SIZE_BUCKETS,
    SpotTape,
    _best_score,
    _size_score,
    kalshi_proxy_fair,
    spot_loop,
)

D = Decimal
logger = logging.getLogger("kalshi_arb.kalshi_15m_ev_census")

KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
KALSHI_SERIES = "KXBTC15M"
REFILL_DELAYS_MS = (100, 250, 500, 1000, 5000)
LATENCY_THRESHOLDS_MS = (10, 25, 50, 80, 100, 150, 250)


@dataclass(frozen=True)
class MarketWindow:
    ticker: str
    close: datetime
    strike: float
    rules_primary: str
    rules_secondary: str


@dataclass
class ActiveEpisode:
    started_ns: int
    direction: str
    start_ev_per_contract: Decimal
    max_ev_per_contract: Decimal
    start_depth: Decimal


class EpisodeTracker:
    """Track how long a positive executable q-bucket survives."""

    def __init__(self) -> None:
        self.active: dict[int, ActiveEpisode] = {}
        self.closed = 0
        self.survived = {ms: 0 for ms in LATENCY_THRESHOLDS_MS}
        self.total_duration_ms = 0.0

    def update(
        self,
        *,
        ticker: str,
        rows: list[dict[str, Any]],
        bbo: BBO,
        now_ns: int,
    ) -> None:
        by_q = {int(row["q"]): row for row in rows}
        for q in SIZE_BUCKETS:
            row = by_q.get(int(q))
            direction = None if row is None else row.get("best_direction")
            raw_ev = None if row is None else row.get("best_total_ev")
            positive = raw_ev is not None and D(str(raw_ev)) > 0 and direction in {"YES", "NO"}
            existing = self.active.get(int(q))

            if positive:
                ev_pc = D(str(row["best_ev_per_contract"]))
                depth = bbo.yes_ask_depth if direction == "YES" else bbo.no_ask_depth
                if existing is None or existing.direction != direction:
                    if existing is not None:
                        self._close(ticker=ticker, q=int(q), episode=existing, ended_ns=now_ns, reason="DIRECTION_FLIP")
                    self.active[int(q)] = ActiveEpisode(
                        started_ns=now_ns,
                        direction=str(direction),
                        start_ev_per_contract=ev_pc,
                        max_ev_per_contract=ev_pc,
                        start_depth=depth,
                    )
                else:
                    existing.max_ev_per_contract = max(existing.max_ev_per_contract, ev_pc)
            elif existing is not None:
                self._close(ticker=ticker, q=int(q), episode=existing, ended_ns=now_ns, reason="EDGE_GONE_OR_DEPTH_LOST")
                self.active.pop(int(q), None)

    def close_all(self, *, ticker: str, reason: str) -> None:
        now_ns = time.perf_counter_ns()
        for q, episode in list(self.active.items()):
            self._close(ticker=ticker, q=q, episode=episode, ended_ns=now_ns, reason=reason)
        self.active.clear()

    def _close(self, *, ticker: str, q: int, episode: ActiveEpisode, ended_ns: int, reason: str) -> None:
        duration_ms = max(0.0, (ended_ns - episode.started_ns) / 1e6)
        self.closed += 1
        self.total_duration_ms += duration_ms
        survival = {str(ms): duration_ms >= ms for ms in LATENCY_THRESHOLDS_MS}
        for ms in LATENCY_THRESHOLDS_MS:
            if duration_ms >= ms:
                self.survived[ms] += 1
        logger.info("K15_EV_EPISODE %s", json.dumps({
            "ticker": ticker,
            "q": q,
            "direction": episode.direction,
            "duration_ms": round(duration_ms, 3),
            "start_ev_per_contract": str(episode.start_ev_per_contract),
            "max_ev_per_contract": str(episode.max_ev_per_contract),
            "start_depth": str(episode.start_depth),
            "survived_latency_ms": survival,
            "reason": reason,
            "classification": "PROXY_EV_EPISODE_NOT_REALIZED_PROFIT",
        }, separators=(",", ":")))


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _basis_bp(official: float | None, proxy: float | None) -> float | None:
    if official is None or proxy is None or official <= 0 or proxy <= 0:
        return None
    return (official / proxy - 1.0) * 10000.0


async def discover_market(http: httpx.AsyncClient) -> MarketWindow | None:
    r = await http.get(
        f"{KALSHI_REST}/markets",
        params={"series_ticker": KALSHI_SERIES, "status": "open", "limit": 100, "mve_filter": "exclude"},
    )
    r.raise_for_status()
    now = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for row in r.json().get("markets") or []:
        close = _dt(row.get("close_time"))
        strike = _float(row.get("floor_strike"))
        if close is None or strike is None or strike <= 0 or close <= now - timedelta(seconds=2):
            continue
        candidates.append((close, row))
    if not candidates:
        return None
    close, row = min(candidates, key=lambda x: x[0])
    return MarketWindow(
        ticker=str(row.get("ticker") or ""),
        close=close,
        strike=float(row.get("floor_strike")),
        rules_primary=str(row.get("rules_primary") or ""),
        rules_secondary=str(row.get("rules_secondary") or ""),
    )


def _bbo_from_quote(quote: Any) -> BBO:
    # In the two-scale Kalshi store: NO ask = 1 - best YES bid, therefore
    # YES bid = 1 - NO ask.  Its displayed depth is the NO-ask depth field.
    yes_bid = D("1") - D(str(quote.no_ask))
    return BBO(
        yes_bid=yes_bid,
        yes_bid_depth=D(str(quote.no_depth)),
        yes_ask=D(str(quote.yes_ask)),
        yes_ask_depth=D(str(quote.yes_depth)),
        received_ms=int(quote.received_ms),
        state="open",
    )


class KalshiTradeStream:
    """Authenticated WS session carrying only the public trade channel."""

    def __init__(self, ticker: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.ticker = ticker
        self.queue = queue
        self.api_key_id = os.getenv("KALSHI_API_KEY_ID", API_KEY_ID)
        self.private_key = load_private_key()
        self.running = False
        self.ws: Any = None
        self.seen_order: deque[str] = deque(maxlen=10000)
        self.seen: set[str] = set()

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.private_key, "GET", urlparse(KALSHI_WS).path, self.api_key_id)

    async def run(self) -> None:
        self.running = True
        backoff = 0.25
        while self.running:
            try:
                async with websockets.connect(
                    KALSHI_WS,
                    additional_headers=self._headers(),
                    ping_interval=20,
                ) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {"channels": ["trade"], "market_tickers": [self.ticker]},
                    }))
                    logger.info("K15_EV_TRADE_WS_SUBSCRIBED %s", json.dumps({"ticker": self.ticker}, separators=(",", ":")))
                    backoff = 0.25
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "trade":
                            body = msg.get("msg") or {}
                            trade_id = str(body.get("trade_id") or "")
                            if trade_id and trade_id in self.seen:
                                continue
                            if trade_id:
                                if len(self.seen_order) == self.seen_order.maxlen:
                                    old = self.seen_order.popleft()
                                    self.seen.discard(old)
                                self.seen_order.append(trade_id)
                                self.seen.add(trade_id)
                            try:
                                self.queue.put_nowait(body)
                            except asyncio.QueueFull:
                                logger.warning("K15_EV_TRADE_QUEUE_FULL ticker=%s", self.ticker)
                        elif msg.get("type") == "error":
                            raise RuntimeError(f"Kalshi trade WS error: {msg.get('msg')}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("K15_EV_TRADE_WS_RESET ticker=%s error=%s retry_s=%.2f", self.ticker, exc, backoff)
            finally:
                self.ws = None
            if self.running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def close(self) -> None:
        self.running = False
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass


def _quote_snapshot(quote: Any | None) -> dict[str, Any] | None:
    if quote is None:
        return None
    return {
        "yes_ask": str(quote.yes_ask),
        "yes_ask_depth": str(quote.yes_depth),
        "no_ask": str(quote.no_ask),
        "no_ask_depth": str(quote.no_depth),
        "book_age_ms": int(time.time() * 1000) - int(quote.received_ms),
        "exchange_ts_ms": quote.exchange_ts_ms,
    }


async def refill_probe(books: EventingKalshiCrossBooks, ticker: str, trade: dict[str, Any]) -> None:
    baseline = await books.quote(ticker, max_age_ms=2000)
    snapshots: list[dict[str, Any]] = []
    previous = 0
    started = time.perf_counter_ns()
    for delay in REFILL_DELAYS_MS:
        await asyncio.sleep((delay - previous) / 1000.0)
        previous = delay
        quote = await books.quote(ticker, max_age_ms=2000)
        snapshots.append({"delay_ms": delay, "book": _quote_snapshot(quote)})
    logger.info("K15_EV_REFILL %s", json.dumps({
        "ticker": ticker,
        "trade": {
            "trade_id": trade.get("trade_id"),
            "count_fp": trade.get("count_fp"),
            "yes_price_dollars": trade.get("yes_price_dollars"),
            "no_price_dollars": trade.get("no_price_dollars"),
            "taker_outcome_side": trade.get("taker_outcome_side") or trade.get("taker_side"),
            "taker_book_side": trade.get("taker_book_side"),
            "exchange_ts_ms": trade.get("ts_ms"),
        },
        "baseline_at_trade_callback": _quote_snapshot(baseline),
        "snapshots": snapshots,
        "elapsed_ms": round((time.perf_counter_ns() - started) / 1e6, 3),
        "interpretation": "PUBLIC_PRINT_REFILL_PROXY_NOT_OWN_IMPACT",
    }, separators=(",", ":")))


async def trade_consumer(books: EventingKalshiCrossBooks, ticker: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
    tasks: set[asyncio.Task[Any]] = set()
    try:
        while True:
            trade = await queue.get()
            task = asyncio.create_task(refill_probe(books, ticker, trade))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def settlement_watch(window: MarketWindow, tape: SpotTape) -> None:
    await asyncio.sleep(max(0.0, window.close.timestamp() - time.time() + 1.0))
    deadline = time.monotonic() + 1800.0
    async with httpx.AsyncClient(timeout=5.0) as http:
        while time.monotonic() < deadline:
            try:
                r = await http.get(f"{KALSHI_REST}/markets/{window.ticker}")
                r.raise_for_status()
                row = r.json().get("market") or {}
                result = str(row.get("result") or "").lower()
                expiration_value = _float(row.get("expiration_value"))
                if result in {"yes", "no"} and expiration_value is not None:
                    close_ms = int(window.close.timestamp() * 1000)
                    final60 = tape.integral(close_ms - 60_000, close_ms)
                    proxy_final60 = None if final60 is None else final60 / 60.0
                    basis = _basis_bp(expiration_value, proxy_final60)
                    proxy_outcome = None if proxy_final60 is None else ("yes" if proxy_final60 >= window.strike else "no")
                    official_margin_bp = (expiration_value / window.strike - 1.0) * 10000.0
                    proxy_margin_bp = None if proxy_final60 is None else (proxy_final60 / window.strike - 1.0) * 10000.0
                    logger.info("K15_EV_SETTLEMENT_BASIS %s", json.dumps({
                        "ticker": window.ticker,
                        "strike": window.strike,
                        "official_result": result,
                        "official_expiration_value": expiration_value,
                        "official_value_source": "KALSHI_MARKET_EXPIRATION_VALUE",
                        "common_spot_proxy_final60": proxy_final60,
                        "proxy_to_official_basis_bp": basis,
                        "official_margin_to_strike_bp": official_margin_bp,
                        "proxy_margin_to_strike_bp": proxy_margin_bp,
                        "proxy_outcome": proxy_outcome,
                        "proxy_outcome_disagrees": None if proxy_outcome is None else proxy_outcome != result,
                        "near_official_5bp": abs(official_margin_bp) <= 5,
                        "near_official_10bp": abs(official_margin_bp) <= 10,
                        "near_official_20bp": abs(official_margin_bp) <= 20,
                        "classification": "KALSHI_OFFICIAL_INDEX_VS_COMMON_SPOT_PROXY_BASIS",
                    }, separators=(",", ":")))
                    return
            except Exception as exc:
                logger.debug("K15 settlement poll error: %s", exc)
            await asyncio.sleep(2.0)
    logger.warning("K15_EV_SETTLEMENT_TIMEOUT ticker=%s", window.ticker)


async def rest_latency_loop(ticker_getter: Any, *, every_s: float) -> None:
    async with httpx.AsyncClient(timeout=3.0) as http:
        while True:
            ticker = ticker_getter()
            if ticker:
                started = time.perf_counter_ns()
                ok = False
                status = None
                try:
                    r = await http.get(f"{KALSHI_REST}/markets/{ticker}")
                    status = r.status_code
                    ok = r.status_code < 400
                except Exception:
                    pass
                logger.info("K15_EV_REST_LATENCY %s", json.dumps({
                    "ticker": ticker,
                    "roundtrip_ms": round((time.perf_counter_ns() - started) / 1e6, 3),
                    "ok": ok,
                    "status": status,
                }, separators=(",", ":")))
            await asyncio.sleep(every_s)


async def run(args: argparse.Namespace) -> None:
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tape = SpotTape()
    spot_task = asyncio.create_task(spot_loop(tape, poll_ms=args.spot_poll_ms))
    active: MarketWindow | None = None
    books: EventingKalshiCrossBooks | None = None
    books_task: asyncio.Task[Any] | None = None
    trades: KalshiTradeStream | None = None
    trades_task: asyncio.Task[Any] | None = None
    consumer_task: asyncio.Task[Any] | None = None
    settlement_tasks: set[asyncio.Task[Any]] = set()
    trade_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
    episodes = EpisodeTracker()
    last_discovery = 0.0
    last_log = 0.0
    last_fair_refresh = 0.0
    cached_fair: float | None = None
    total_samples = 0
    positive_samples = {int(q): 0 for q in SIZE_BUCKETS}
    positive_ev_sum = {int(q): D("0") for q in SIZE_BUCKETS}

    def current_ticker() -> str | None:
        return None if active is None else active.ticker

    latency_task = asyncio.create_task(rest_latency_loop(current_ticker, every_s=args.rest_latency_s))

    async with httpx.AsyncClient(timeout=5.0) as http:
        fee_meta = await fetch_series_fee_metadata(http, KALSHI_SERIES)
        fee_fn = fee_meta.taker_fee_fn()
        logger.info("K15_EV_START %s", json.dumps({
            "classification": "KALSHI_VS_SPOT_PROXY_EV_NOT_CROSS_VENUE_LOCK",
            "series": KALSHI_SERIES,
            "spot_proxy": "MEDIAN_COINBASE_KRAKEN",
            "fair_model": "VOLATILITY_AND_TIME_AWARE_FINAL60_PROBABILITY",
            "size_buckets": SIZE_BUCKETS,
            "latency_thresholds_ms": LATENCY_THRESHOLDS_MS,
            "fee": fee_meta.as_dict(),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

        try:
            while True:
                now_mono = time.monotonic()
                now_utc = datetime.now(timezone.utc)
                if active is None or now_mono - last_discovery >= args.discovery_s or now_utc > active.close + timedelta(seconds=2):
                    last_discovery = now_mono
                    candidate = await discover_market(http)
                    if candidate is not None and (active is None or candidate.ticker != active.ticker):
                        previous = active
                        if active is not None:
                            episodes.close_all(ticker=active.ticker, reason="WINDOW_ROLLOVER")
                        if trades is not None:
                            await trades.close()
                        if books is not None:
                            await books.close()
                        for task in (books_task, trades_task, consumer_task):
                            if task is not None:
                                task.cancel()
                        await asyncio.gather(*(t for t in (books_task, trades_task, consumer_task) if t is not None), return_exceptions=True)

                        active = candidate
                        cached_fair = None
                        books = EventingKalshiCrossBooks([active.ticker], ws_url=KALSHI_WS)
                        trades = KalshiTradeStream(active.ticker, trade_queue)
                        books_task = asyncio.create_task(books.run())
                        trades_task = asyncio.create_task(trades.run())
                        consumer_task = asyncio.create_task(trade_consumer(books, active.ticker, trade_queue))
                        logger.info("K15_EV_WINDOW %s", json.dumps({
                            "ticker": active.ticker,
                            "close": active.close.isoformat(),
                            "strike": active.strike,
                            "rules_primary": active.rules_primary,
                            "rules_secondary": active.rules_secondary,
                        }, separators=(",", ":")))
                        if previous is not None:
                            task = asyncio.create_task(settlement_watch(previous, tape))
                            settlement_tasks.add(task)
                            task.add_done_callback(settlement_tasks.discard)

                if active is None or books is None:
                    if now_mono - last_log >= 10.0:
                        logger.info("K15_EV_NO_OPEN_MARKET read_only=true")
                        last_log = now_mono
                    await asyncio.sleep(0.1)
                    continue

                quote = await books.quote(active.ticker, max_age_ms=2000)
                if quote is None:
                    await asyncio.sleep(args.sample_ms / 1000.0)
                    continue
                bbo = _bbo_from_quote(quote)

                if now_mono - last_fair_refresh >= args.fair_refresh_ms / 1000.0:
                    cached_fair = kalshi_proxy_fair(tape, active.close, active.strike, int(time.time() * 1000))
                    last_fair_refresh = now_mono
                if cached_fair is None:
                    await asyncio.sleep(args.sample_ms / 1000.0)
                    continue

                rows = _size_score(
                    venue="kalshi",
                    ticker=active.ticker,
                    fair=cached_fair,
                    bbo=bbo,
                    kalshi_fee_fn=fee_fn,
                )
                now_ns = time.perf_counter_ns()
                episodes.update(ticker=active.ticker, rows=rows, bbo=bbo, now_ns=now_ns)
                total_samples += 1
                for row in rows:
                    if row.get("best_total_ev") is not None and D(str(row["best_total_ev"])) > 0:
                        q = int(row["q"])
                        positive_samples[q] += 1
                        positive_ev_sum[q] += D(str(row["best_total_ev"]))

                if now_mono - last_log >= args.log_s:
                    best_by_q = {str(row["q"]): {
                        "direction": row.get("best_direction"),
                        "total_ev": row.get("best_total_ev"),
                        "ev_per_contract": row.get("best_ev_per_contract"),
                    } for row in rows}
                    logger.info("K15_EV_SUMMARY %s", json.dumps({
                        "ticker": active.ticker,
                        "samples": total_samples,
                        "spot": None if tape.latest is None else tape.latest.price,
                        "spot_age_ms": None if tape.latest is None else int(time.time() * 1000) - tape.latest.ts_ms,
                        "sigma_log_per_sqrt_second": tape.sigma_log_per_sqrt_second(),
                        "proxy_fair_yes": cached_fair,
                        "yes_ask": str(bbo.yes_ask),
                        "yes_ask_depth": str(bbo.yes_ask_depth),
                        "no_ask": str(bbo.no_ask),
                        "no_ask_depth": str(bbo.no_ask_depth),
                        "book_age_ms": int(time.time() * 1000) - bbo.received_ms,
                        "best_by_q": best_by_q,
                        "positive_sample_count_by_q": positive_samples,
                        "positive_total_ev_sum_by_q": {str(q): str(v) for q, v in positive_ev_sum.items()},
                        "closed_episodes": episodes.closed,
                        "episode_survival_count": {str(k): v for k, v in episodes.survived.items()},
                        "read_only": True,
                    }, separators=(",", ":")))
                    last_log = now_mono

                if args.output:
                    with output.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({
                            "ts_ms": int(time.time() * 1000),
                            "ticker": active.ticker,
                            "spot": None if tape.latest is None else tape.latest.price,
                            "proxy_fair_yes": cached_fair,
                            "yes_ask": str(bbo.yes_ask),
                            "yes_ask_depth": str(bbo.yes_ask_depth),
                            "no_ask": str(bbo.no_ask),
                            "no_ask_depth": str(bbo.no_ask_depth),
                            "scores": rows,
                        }, separators=(",", ":")) + "\n")

                await asyncio.sleep(args.sample_ms / 1000.0)
        finally:
            if active is not None:
                episodes.close_all(ticker=active.ticker, reason="SHUTDOWN")
            for task in (spot_task, latency_task, books_task, trades_task, consumer_task):
                if task is not None:
                    task.cancel()
            if trades is not None:
                await trades.close()
            if books is not None:
                await books.close()
            for task in settlement_tasks:
                task.cancel()
            await asyncio.gather(
                *(t for t in (spot_task, latency_task, books_task, trades_task, consumer_task) if t is not None),
                *settlement_tasks,
                return_exceptions=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Kalshi BTC 15m venue-vs-spot EV capacity census")
    parser.add_argument("--sample-ms", type=int, default=10)
    parser.add_argument("--fair-refresh-ms", type=int, default=100)
    parser.add_argument("--spot-poll-ms", type=int, default=250)
    parser.add_argument("--discovery-s", type=float, default=30.0)
    parser.add_argument("--rest-latency-s", type=float, default=10.0)
    parser.add_argument("--log-s", type=float, default=10.0)
    parser.add_argument("--output", default="/data/kalshi_15m_ev_census.jsonl")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
