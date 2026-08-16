"""Hardened read-only HOT layer for Gemini <-> Kalshi 15-minute crypto.

This service is intentionally research-only. Gemini and Kalshi settle against
different crypto reference indexes, so even a positive complementary quote is NOT
certified risk-free arbitrage. It must never authorize an order while
``settlement_status`` remains ``INDEX_MISMATCH_RESEARCH_ONLY``.

Architecture:
- REST only for pair discovery at 15-minute rollover and live fee metadata;
- Gemini bookTicker WebSocket + Kalshi orderbook_delta WebSocket for market data;
- one-sided books preserve only the executable direction (zero size on missing leg);
- Kalshi sequence gaps force a reconnect/snapshot rebuild;
- every accepted venue update immediately reevaluates cached opposing books;
- q=1..5 is evaluated explicitly, with <=$5 capital and both taker fees;
- bounded queues, reconnect/drop telemetry, and trigger->evaluation latency;
- no order-manager, probe transport, or order-entry imports.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
import inspect
import json
import logging
from pathlib import Path
import time
from typing import Any

import httpx
import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_aligned_watch import Pair
from src.gemini_kalshi_btc_ws_shadow import (
    KALSHI_WS,
    KALSHI_WS_SIGN_PATH,
    KalshiOrderBook,
    _ws_header_kw,
)
from src.gemini_kalshi_execution_shadow import (
    ShadowCandidate,
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import (
    KalshiSeriesFeeMetadata,
    fetch_series_fee_metadata,
)
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker
from src.gemini_kalshi_multi_ws_shadow import ASSET_SERIES, PairDiscoveryCache

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_multi_ws_hot")
SETTLEMENT_STATUS = "INDEX_MISMATCH_RESEARCH_ONLY"
MAX_QUEUE = 20_000
HEARTBEAT_S = 10.0


class SequenceGap(RuntimeError):
    pass


class GuardedKalshiOrderBook(KalshiOrderBook):
    """Single-market book that rejects gaps instead of silently corrupting state."""

    def apply(self, payload: dict[str, Any]) -> bool:
        typ = str(payload.get("type") or "")
        msg = payload.get("msg") or {}
        if str(msg.get("market_ticker") or "") != self.ticker:
            return False
        if typ not in {"orderbook_snapshot", "orderbook_delta"}:
            return False

        seq = payload.get("seq")
        if seq is not None and self.seq is not None:
            seq_i = int(seq)
            if seq_i <= self.seq:
                return False
            if seq_i != self.seq + 1:
                raise SequenceGap(
                    f"orderbook sequence gap ticker={self.ticker} "
                    f"previous={self.seq} current={seq_i}"
                )
        return super().apply(payload)


@dataclass
class AssetStats:
    asset: str
    events: int = 0
    gemini_events: int = 0
    kalshi_events: int = 0
    evaluations: int = 0
    positive_evaluations: int = 0
    threshold_evaluations: int = 0
    dropped_events: int = 0
    gemini_reconnects: int = 0
    kalshi_reconnects: int = 0
    kalshi_sequence_gaps: int = 0
    windows: int = 0
    _latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=10_000))

    def observe_latency(self, ms: float) -> None:
        self._latency_ms.append(max(0.0, ms))

    def payload(self, queue: asyncio.Queue) -> dict[str, Any]:
        values = sorted(self._latency_ms)
        if values:
            n = len(values)
            p95 = values[min(n - 1, max(0, int(0.95 * n) - 1))]
            avg = sum(values) / n
            mx = values[-1]
        else:
            n = 0
            p95 = avg = mx = None
        return {
            "asset": self.asset,
            "events": self.events,
            "gemini_events": self.gemini_events,
            "kalshi_events": self.kalshi_events,
            "evaluations": self.evaluations,
            "positive_evaluations": self.positive_evaluations,
            "threshold_evaluations": self.threshold_evaluations,
            "dropped_events": self.dropped_events,
            "gemini_reconnects": self.gemini_reconnects,
            "kalshi_reconnects": self.kalshi_reconnects,
            "kalshi_sequence_gaps": self.kalshi_sequence_gaps,
            "windows": self.windows,
            "queue_size": queue.qsize(),
            "trigger_to_eval_samples": n,
            "trigger_to_eval_avg_ms": round(avg, 3) if avg is not None else None,
            "trigger_to_eval_p95_ms": round(p95, 3) if p95 is not None else None,
            "trigger_to_eval_max_ms": round(mx, 3) if mx is not None else None,
            "settlement_status": SETTLEMENT_STATUS,
            "read_only": True,
            "order_entry": False,
        }


@dataclass
class ActiveEpisode:
    direction: str
    start_ms: int
    last_ms: int
    events: int
    best_profit: Decimal
    best_capital: Decimal
    best_q: int


def _enqueue(
    queue: asyncio.Queue,
    item: tuple[str, int, int | None, TopOfBook, int],
    stats: AssetStats,
) -> None:
    try:
        queue.put_nowait(item)
        return
    except asyncio.QueueFull:
        pass
    # Each queued item is a full top-of-book state. If the consumer ever falls this
    # far behind, discard the oldest wake-up rather than backpressuring a venue WS.
    # The following full state repairs the cache; quote-skew gating still fails closed.
    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        pass
    stats.dropped_events += 1
    queue.put_nowait(item)


def _kalshi_subscription(ticker: str) -> dict[str, Any]:
    return {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_ticker": ticker,
            # Explicit legacy two-scale mode: NO levels are NO prices, matching the
            # reciprocal ask math used by KalshiOrderBook.top().
            "use_yes_price": False,
        },
    }


async def _gemini_stream(
    symbol: str,
    queue: asyncio.Queue,
    stats: AssetStats,
) -> None:
    backoff = 0.25
    first = True
    while True:
        try:
            async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
                await ws.send(json.dumps({
                    "id": f"hot-{stats.asset.lower()}",
                    "method": "SUBSCRIBE",
                    "params": [f"{symbol}@bookTicker"],
                }))
                backoff = 0.25
                async for raw in ws:
                    receive_ms = int(time.time() * 1000)
                    mono_ns = time.perf_counter_ns()
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"Gemini WS subscription error: {payload}")
                        continue
                    if str(payload.get("s") or "").lower() != symbol.lower():
                        continue
                    try:
                        top = parse_gemini_book_ticker(payload, received_ms=receive_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
                    stats.events += 1
                    stats.gemini_events += 1
                    _enqueue(queue, ("gemini", receive_ms, None, top, mono_ns), stats)
            raise RuntimeError(f"Gemini WS ended for {symbol}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not first:
                stats.gemini_reconnects += 1
            first = False
            logger.warning(
                "CRYPTO_HOT_GEMINI_RECONNECT %s",
                json.dumps({
                    "asset": stats.asset,
                    "symbol": symbol,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                }, separators=(",", ":")),
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)


async def _kalshi_stream(
    ticker: str,
    queue: asyncio.Queue,
    stats: AssetStats,
) -> None:
    private_key = load_private_key()
    backoff = 0.25
    first = True
    while True:
        try:
            headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
            book = GuardedKalshiOrderBook(ticker)
            async with websockets.connect(
                KALSHI_WS,
                open_timeout=5.0,
                **_ws_header_kw(headers),
            ) as ws:
                await ws.send(json.dumps(_kalshi_subscription(ticker)))
                backoff = 0.25
                async for raw in ws:
                    receive_ms = int(time.time() * 1000)
                    mono_ns = time.perf_counter_ns()
                    payload = json.loads(raw)
                    if payload.get("type") == "error":
                        raise RuntimeError(f"Kalshi WS error: {payload.get('msg')}")
                    try:
                        changed = book.apply(payload)
                    except SequenceGap:
                        stats.kalshi_sequence_gaps += 1
                        raise
                    if not changed:
                        continue
                    top = book.top(receive_ms)
                    if top is None:
                        continue
                    stats.events += 1
                    stats.kalshi_events += 1
                    _enqueue(
                        queue,
                        ("kalshi", receive_ms, book.exchange_ts_ms, top, mono_ns),
                        stats,
                    )
            raise RuntimeError(f"Kalshi WS ended for {ticker}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not first:
                stats.kalshi_reconnects += 1
            first = False
            logger.warning(
                "CRYPTO_HOT_KALSHI_RECONNECT %s",
                json.dumps({
                    "asset": stats.asset,
                    "ticker": ticker,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                    "sequence_gaps": stats.kalshi_sequence_gaps,
                }, separators=(",", ":")),
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)


def _small_q_candidates(
    g: TopOfBook,
    k: TopOfBook,
    *,
    kalshi_fee,
    max_quote_skew_ms: int,
    capital_limit: Decimal,
) -> list[tuple[int, ShadowCandidate]]:
    out: list[tuple[int, ShadowCandidate]] = []
    for q in range(1, 6):
        qd = D(q)
        candidates = evaluate_pair(
            g,
            k,
            gemini_fee=gemini_predictions_taker_fee,
            kalshi_fee=kalshi_fee,
            max_quote_skew_ms=max_quote_skew_ms,
            max_quantity=qd,
        )
        for candidate in candidates:
            # evaluate_pair caps quantity; require full integer q to be displayed.
            if candidate.quantity != qd:
                continue
            if candidate.net_cost > capital_limit:
                continue
            out.append((q, candidate))
    return out


def _candidate_payload(q: int, c: ShadowCandidate) -> dict[str, Any]:
    return {
        "q": q,
        "direction": c.direction,
        "capital": str(c.net_cost),
        "locked_quote_profit": str(c.locked_profit),
        "fees": str(c.fees),
        "roi": str(c.roi_on_cost),
        "quote_skew_ms": c.quote_skew_ms,
    }


def _close_missing_episodes(
    *,
    active: dict[str, ActiveEpisode],
    qualifying: dict[str, tuple[int, ShadowCandidate]],
    now_ms: int,
    asset: str,
    pair: Pair,
) -> None:
    for direction in list(active):
        if direction in qualifying:
            continue
        ep = active.pop(direction)
        logger.info(
            "CRYPTO_HOT_EPISODE_CLOSE %s",
            json.dumps({
                "asset": asset,
                "direction": direction,
                "start_ms": ep.start_ms,
                "end_ms": now_ms,
                "duration_ms": max(0, now_ms - ep.start_ms),
                "positive_events": ep.events,
                "best_locked_quote_profit": str(ep.best_profit),
                "best_capital": str(ep.best_capital),
                "best_q": ep.best_q,
                "gemini_symbol": pair.gemini_symbol,
                "kalshi_ticker": pair.kalshi_ticker,
                "settlement_status": SETTLEMENT_STATUS,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")),
        )


def _update_episodes(
    *,
    active: dict[str, ActiveEpisode],
    qualifying: dict[str, tuple[int, ShadowCandidate]],
    now_ms: int,
    asset: str,
    pair: Pair,
    trigger_venue: str,
) -> None:
    for direction, (q, c) in qualifying.items():
        ep = active.get(direction)
        if ep is None:
            active[direction] = ActiveEpisode(
                direction=direction,
                start_ms=now_ms,
                last_ms=now_ms,
                events=1,
                best_profit=c.locked_profit,
                best_capital=c.net_cost,
                best_q=q,
            )
            logger.info(
                "CRYPTO_HOT_CANDIDATE_RESEARCH_ONLY %s",
                json.dumps({
                    "asset": asset,
                    "trigger_venue": trigger_venue,
                    "gemini_symbol": pair.gemini_symbol,
                    "kalshi_ticker": pair.kalshi_ticker,
                    **_candidate_payload(q, c),
                    "settlement_status": SETTLEMENT_STATUS,
                    "trade_authorized": False,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )
            continue
        ep.last_ms = now_ms
        ep.events += 1
        if c.locked_profit > ep.best_profit:
            ep.best_profit = c.locked_profit
            ep.best_capital = c.net_cost
            ep.best_q = q


async def _capture_pair(
    *,
    asset: str,
    pair: Pair,
    kalshi_fee,
    output: Path,
    max_quote_skew_ms: int,
    capital_limit: Decimal,
    min_profit: Decimal,
    stats: AssetStats,
    overall_deadline: float | None,
) -> None:
    queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
    tasks = [
        asyncio.create_task(_gemini_stream(pair.gemini_symbol, queue, stats)),
        asyncio.create_task(_kalshi_stream(pair.kalshi_ticker, queue, stats)),
    ]
    g: TopOfBook | None = None
    k: TopOfBook | None = None
    latest_k_exchange_ts: int | None = None
    active: dict[str, ActiveEpisode] = {}
    next_heartbeat = time.monotonic() + HEARTBEAT_S
    remaining_window_s = max(0.0, pair.window_end.timestamp() - time.time() + 0.75)
    pair_deadline = time.monotonic() + remaining_window_s
    if overall_deadline is not None:
        pair_deadline = min(pair_deadline, overall_deadline)

    stats.windows += 1
    logger.info(
        "CRYPTO_HOT_READY_FEED %s",
        json.dumps({
            "asset": asset,
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "window_end_utc": pair.window_end.isoformat(),
            "max_quote_skew_ms": max_quote_skew_ms,
            "capital_limit": str(capital_limit),
            "min_profit": str(min_profit),
            "settlement_status": SETTLEMENT_STATUS,
            "trade_authorized": False,
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
                    venue, receive_ms, exchange_ms, top, trigger_mono_ns = await asyncio.wait_for(
                        queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    continue

                if venue == "gemini":
                    g = top
                else:
                    k = top
                    latest_k_exchange_ts = exchange_ms
                if g is None or k is None:
                    continue

                stats.evaluations += 1
                all_small = _small_q_candidates(
                    g,
                    k,
                    kalshi_fee=kalshi_fee,
                    max_quote_skew_ms=max_quote_skew_ms,
                    capital_limit=capital_limit,
                )
                if all_small:
                    stats.positive_evaluations += 1

                # For each direction, keep the highest absolute quote-lock profit
                # among full integer q=1..5 under the capital cap.
                best_by_direction: dict[str, tuple[int, ShadowCandidate]] = {}
                for q, c in all_small:
                    prior = best_by_direction.get(c.direction)
                    if prior is None or c.locked_profit > prior[1].locked_profit:
                        best_by_direction[c.direction] = (q, c)
                qualifying = {
                    direction: item
                    for direction, item in best_by_direction.items()
                    if item[1].locked_profit >= min_profit
                }
                if qualifying:
                    stats.threshold_evaluations += 1

                eval_done_ns = time.perf_counter_ns()
                stats.observe_latency((eval_done_ns - trigger_mono_ns) / 1_000_000)
                _close_missing_episodes(
                    active=active,
                    qualifying=qualifying,
                    now_ms=receive_ms,
                    asset=asset,
                    pair=pair,
                )
                _update_episodes(
                    active=active,
                    qualifying=qualifying,
                    now_ms=receive_ms,
                    asset=asset,
                    pair=pair,
                    trigger_venue=venue,
                )

                row = {
                    "asset": asset,
                    "window_end_utc": pair.window_end.isoformat(),
                    "event_venue": venue,
                    "receive_ts_ms": receive_ms,
                    "gemini_receive_ts_ms": g.ts_ms,
                    "kalshi_receive_ts_ms": k.ts_ms,
                    "kalshi_exchange_ts_ms": latest_k_exchange_ts,
                    "kalshi_exchange_to_receive_ms": (
                        k.ts_ms - latest_k_exchange_ts
                        if latest_k_exchange_ts is not None
                        else None
                    ),
                    "gemini_symbol": pair.gemini_symbol,
                    "kalshi_ticker": pair.kalshi_ticker,
                    "small_q_candidates": [
                        _candidate_payload(q, c) for q, c in all_small
                    ],
                    "trigger_to_eval_ms": round(
                        (eval_done_ns - trigger_mono_ns) / 1_000_000, 3
                    ),
                    "settlement_status": SETTLEMENT_STATUS,
                    "read_only": True,
                    "order_entry": False,
                }
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")

                if time.monotonic() >= next_heartbeat:
                    logger.info(
                        "CRYPTO_HOT_SUMMARY %s",
                        json.dumps(stats.payload(queue), separators=(",", ":")),
                    )
                    next_heartbeat = time.monotonic() + HEARTBEAT_S
            fh.flush()
    finally:
        # Close any still-positive episode at the window/session boundary.
        _close_missing_episodes(
            active=active,
            qualifying={},
            now_ms=int(time.time() * 1000),
            asset=asset,
            pair=pair,
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def collect_asset(
    *,
    http: httpx.AsyncClient,
    discovery: PairDiscoveryCache,
    asset: str,
    output_dir: Path,
    max_quote_skew_ms: int,
    capital_limit: Decimal,
    min_profit: Decimal,
    fee_meta: KalshiSeriesFeeMetadata,
    overall_deadline: float | None,
) -> None:
    stats = AssetStats(asset)
    kalshi_fee = fee_meta.taker_fee_fn()  # fails closed on unknown fee type
    while overall_deadline is None or time.monotonic() < overall_deadline:
        pair = await discovery.current_pair(http, asset)
        if pair is None:
            logger.info("CRYPTO_HOT_WAITING_FOR_PAIR asset=%s", asset)
            await asyncio.sleep(0.5)
            continue
        output = output_dir / f"crypto_hot_{asset.lower()}.jsonl"
        await _capture_pair(
            asset=asset,
            pair=pair,
            kalshi_fee=kalshi_fee,
            output=output,
            max_quote_skew_ms=max_quote_skew_ms,
            capital_limit=capital_limit,
            min_profit=min_profit,
            stats=stats,
            overall_deadline=overall_deadline,
        )


async def collect_all(
    *,
    assets: list[str],
    seconds: float,
    output_dir: Path,
    max_quote_skew_ms: int,
    capital_limit: Decimal,
    min_profit: Decimal,
) -> None:
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    load_private_key()

    assets = [a.upper() for a in assets]
    unknown = [a for a in assets if a not in ASSET_SERIES]
    if unknown:
        raise ValueError(f"unsupported assets={unknown}")
    if capital_limit <= 0 or min_profit < 0:
        raise ValueError("invalid economics thresholds")

    overall_deadline = None if seconds <= 0 else time.monotonic() + seconds
    async with httpx.AsyncClient(timeout=20.0) as http:
        fee_meta: dict[str, KalshiSeriesFeeMetadata] = {}
        for asset in assets:
            meta = await fetch_series_fee_metadata(http, ASSET_SERIES[asset])
            # Calling taker_fee_fn here is an explicit startup gate.
            meta.taker_fee_fn()
            fee_meta[asset] = meta
            logger.info(
                "CRYPTO_HOT_FEE_GATE %s",
                json.dumps(meta.as_dict(), separators=(",", ":")),
            )
            await asyncio.sleep(0.15)

        discovery = PairDiscoveryCache(ttl_s=1.0)
        await asyncio.gather(*[
            collect_asset(
                http=http,
                discovery=discovery,
                asset=asset,
                output_dir=output_dir,
                max_quote_skew_ms=max_quote_skew_ms,
                capital_limit=capital_limit,
                min_profit=min_profit,
                fee_meta=fee_meta[asset],
                overall_deadline=overall_deadline,
            )
            for asset in assets
        ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL", "XRP"])
    parser.add_argument("--seconds", type=float, default=0.0, help="0 = run continuously")
    parser.add_argument("--max-quote-skew-ms", type=int, default=100)
    parser.add_argument("--capital-limit", type=Decimal, default=D("5"))
    parser.add_argument("--min-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/crypto-hot"))
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    asyncio.run(collect_all(
        assets=args.assets,
        seconds=args.seconds,
        output_dir=args.output_dir,
        max_quote_skew_ms=args.max_quote_skew_ms,
        capital_limit=args.capital_limit,
        min_profit=args.min_profit,
    ))


if __name__ == "__main__":
    main()
