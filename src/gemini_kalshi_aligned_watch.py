"""Continuous read-only shadow watch for aligned Gemini/Kalshi 15-minute crypto.

This watcher is designed to answer the economic question left by one-shot snapshots:
do synchronized cross-venue price locks actually appear, for how long, and with how
much displayed top-of-book size?

Safety properties:
- public market-data only
- no account credentials
- no order-entry imports or code paths
- settlement remains INDEX_MISMATCH_RESEARCH_ONLY
- repeated resting depth is NOT counted as fresh executable P&L

Gemini is streamed continuously with one public depth5@100ms WebSocket per symbol.
Kalshi public depth-1 orderbooks are polled for only the four 15-minute crypto series,
avoiding the expensive all-markets scan used during discovery work.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
from pathlib import Path
import time
from typing import Any

import httpx
import websockets

from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
    zero_fee,
)
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, _gemini_meta, _gemini_symbol
from src.gemini_kalshi_live_shadow import GEMINI_WS, KALSHI_REST, kalshi_book_once
from src.gemini_kalshi_window_identity import kalshi_15m_asset, kalshi_window_end

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_aligned_watch")

SERIES = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
}


@dataclass(frozen=True)
class Pair:
    asset: str
    window_end: Any
    gemini_symbol: str
    kalshi_ticker: str


async def _kalshi_open_15m(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch only the currently open short-duration crypto series."""

    async def fetch_series(series_ticker: str) -> list[dict[str, Any]]:
        response = await http.get(
            f"{KALSHI_REST}/markets",
            params={
                "series_ticker": series_ticker,
                "status": "open",
                "limit": 100,
                "mve_filter": "exclude",
            },
        )
        response.raise_for_status()
        return list(response.json().get("markets", []))

    pages = await asyncio.gather(*(fetch_series(series) for series in SERIES.values()))
    return [market for page in pages for market in page]


async def _discover_pairs(http: httpx.AsyncClient, max_window_skew_s: float) -> list[Pair]:
    gemini_events, kalshi_markets = await asyncio.gather(
        _gemini_active_crypto(http),
        _kalshi_open_15m(http),
    )

    kalshi_rows: list[tuple[dict[str, Any], str, Any]] = []
    for market in kalshi_markets:
        asset = kalshi_15m_asset(market)
        end = kalshi_window_end(market)
        if asset and end:
            kalshi_rows.append((market, asset, end))

    pairs: list[Pair] = []
    for event in gemini_events:
        meta = _gemini_meta(event)
        if meta is None:
            continue
        asset, duration, g_end = meta
        if duration != 15:
            continue
        symbol = _gemini_symbol(event)
        if not symbol:
            continue
        for market, k_asset, k_end in kalshi_rows:
            if asset != k_asset:
                continue
            if abs((g_end - k_end).total_seconds()) > max_window_skew_s:
                continue
            ticker = str(market.get("ticker") or "")
            if ticker:
                pairs.append(Pair(asset, g_end, symbol, ticker))

    pairs.sort(key=lambda p: (p.window_end, p.asset))
    return pairs


async def _gemini_depth_stream(
    symbol: str,
    latest: dict[str, TopOfBook],
    stop: asyncio.Event,
) -> None:
    """Maintain a fresh Gemini top-of-book snapshot for one symbol."""
    while not stop.is_set():
        try:
            async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
                await ws.send(json.dumps({
                    "id": "watch",
                    "method": "SUBSCRIBE",
                    "params": [f"{symbol}@depth5@100ms"],
                }))
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    payload = json.loads(raw)
                    bids = payload.get("bids")
                    asks = payload.get("asks")
                    if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
                        continue
                    bid_price, bid_size = max(bids, key=lambda row: D(str(row[0])))
                    ask_price, ask_size = min(asks, key=lambda row: D(str(row[0])))
                    latest[symbol] = TopOfBook(
                        venue="gemini",
                        market_id=symbol,
                        ts_ms=int(time.time() * 1000),
                        yes_bid=D(str(bid_price)),
                        yes_bid_size=D(str(bid_size)),
                        yes_ask=D(str(ask_price)),
                        yes_ask_size=D(str(ask_size)),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("ALIGNED_WATCH_GEMINI_RECONNECT %s", json.dumps({
                "symbol": symbol,
                "error_type": type(exc).__name__,
                "error": str(exc) or repr(exc),
            }, separators=(",", ":")))
            if not stop.is_set():
                await asyncio.sleep(0.5)


def _candidate_rows(candidates) -> list[dict[str, Any]]:
    return [
        {
            "direction": c.direction,
            "quantity": str(c.quantity),
            "raw_cost": str(c.raw_cost),
            "fees": str(c.fees),
            "net_cost": str(c.net_cost),
            "locked_profit": str(c.locked_profit),
            "roi": str(c.roi_on_cost),
            "quote_skew_ms": c.quote_skew_ms,
        }
        for c in candidates
    ]


async def watch(
    *,
    seconds: float,
    poll_s: float,
    max_window_skew_s: float,
    max_quote_skew_ms: int,
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    latest: dict[str, TopOfBook] = {}

    async with httpx.AsyncClient(timeout=10.0) as http:
        pairs = await _discover_pairs(http, max_window_skew_s)
        logger.info("ALIGNED_WATCH_DISCOVERY %s", json.dumps({
            "pairs": [
                {
                    "asset": p.asset,
                    "window_end_utc": p.window_end.isoformat(),
                    "gemini_symbol": p.gemini_symbol,
                    "kalshi_ticker": p.kalshi_ticker,
                }
                for p in pairs
            ],
            "pair_count": len(pairs),
            "poll_s": poll_s,
            "seconds": seconds,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
        if not pairs:
            return

        stream_tasks = [
            asyncio.create_task(_gemini_depth_stream(p.gemini_symbol, latest, stop))
            for p in pairs
        ]

        stats: dict[str, dict[str, Any]] = {
            p.asset: {
                "samples": 0,
                "fresh_samples": 0,
                "raw_positive_samples": 0,
                "pre_fee_candidate_samples": 0,
                "gemini_taker_only_candidate_samples": 0,
                "best_raw_edge_per_contract": D("0"),
                "max_pre_fee_quantity": D("0"),
                "episodes": 0,
                "in_episode": False,
                "episode_start_ms": None,
                "episode_best_edge": D("0"),
                "episode_max_qty": D("0"),
            }
            for p in pairs
        }

        deadline = time.monotonic() + seconds
        await asyncio.sleep(0.25)

        with output.open("a", encoding="utf-8") as fh:
            try:
                while time.monotonic() < deadline:
                    loop_started = time.monotonic()
                    books = await asyncio.gather(
                        *(kalshi_book_once(http, p.kalshi_ticker) for p in pairs),
                        return_exceptions=True,
                    )
                    now_ms = int(time.time() * 1000)

                    for pair, k_result in zip(pairs, books):
                        bucket = stats[pair.asset]
                        bucket["samples"] += 1
                        g = latest.get(pair.gemini_symbol)
                        if g is None:
                            continue
                        if isinstance(k_result, Exception):
                            logger.warning("ALIGNED_WATCH_KALSHI_ERROR %s", json.dumps({
                                "asset": pair.asset,
                                "ticker": pair.kalshi_ticker,
                                "error_type": type(k_result).__name__,
                                "error": str(k_result) or repr(k_result),
                            }, separators=(",", ":")))
                            continue
                        k = k_result
                        quote_skew_ms = abs(g.ts_ms - k.ts_ms)
                        fresh = quote_skew_ms <= max_quote_skew_ms
                        if fresh:
                            bucket["fresh_samples"] += 1

                        c_g_yes = g.yes_ask + k.no_ask
                        c_k_yes = k.yes_ask + g.no_ask
                        best_cost = min(c_g_yes, c_k_yes)
                        raw_edge = max(D("0"), D("1") - best_cost)
                        if raw_edge > 0:
                            bucket["raw_positive_samples"] += 1
                            bucket["best_raw_edge_per_contract"] = max(
                                bucket["best_raw_edge_per_contract"], raw_edge
                            )

                        pre_fee = evaluate_pair(
                            g,
                            k,
                            gemini_fee=zero_fee,
                            kalshi_fee=zero_fee,
                            max_quote_skew_ms=max_quote_skew_ms,
                        )
                        gemini_taker_only = evaluate_pair(
                            g,
                            k,
                            gemini_fee=gemini_predictions_taker_fee,
                            kalshi_fee=zero_fee,
                            max_quote_skew_ms=max_quote_skew_ms,
                        )

                        if pre_fee:
                            bucket["pre_fee_candidate_samples"] += 1
                            max_qty = max(c.quantity for c in pre_fee)
                            bucket["max_pre_fee_quantity"] = max(bucket["max_pre_fee_quantity"], max_qty)
                        else:
                            max_qty = D("0")
                        if gemini_taker_only:
                            bucket["gemini_taker_only_candidate_samples"] += 1

                        positive = bool(pre_fee)
                        if positive and not bucket["in_episode"]:
                            bucket["in_episode"] = True
                            bucket["episode_start_ms"] = now_ms
                            bucket["episode_best_edge"] = raw_edge
                            bucket["episode_max_qty"] = max_qty
                            bucket["episodes"] += 1
                        elif positive:
                            bucket["episode_best_edge"] = max(bucket["episode_best_edge"], raw_edge)
                            bucket["episode_max_qty"] = max(bucket["episode_max_qty"], max_qty)
                        elif bucket["in_episode"]:
                            logger.info("ALIGNED_WATCH_EPISODE %s", json.dumps({
                                "asset": pair.asset,
                                "start_ms": bucket["episode_start_ms"],
                                "end_ms": now_ms,
                                "duration_ms": now_ms - int(bucket["episode_start_ms"]),
                                "best_raw_edge_per_contract": str(bucket["episode_best_edge"]),
                                "max_displayed_pre_fee_quantity": str(bucket["episode_max_qty"]),
                                "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
                            }, separators=(",", ":")))
                            bucket["in_episode"] = False
                            bucket["episode_start_ms"] = None
                            bucket["episode_best_edge"] = D("0")
                            bucket["episode_max_qty"] = D("0")

                        row = {
                            "ts_ms": now_ms,
                            "asset": pair.asset,
                            "window_end_utc": pair.window_end.isoformat(),
                            "gemini_symbol": pair.gemini_symbol,
                            "kalshi_ticker": pair.kalshi_ticker,
                            "gemini_yes_bid": str(g.yes_bid),
                            "gemini_yes_bid_size": str(g.yes_bid_size),
                            "gemini_yes_ask": str(g.yes_ask),
                            "gemini_yes_ask_size": str(g.yes_ask_size),
                            "kalshi_yes_bid": str(k.yes_bid),
                            "kalshi_yes_bid_size": str(k.yes_bid_size),
                            "kalshi_yes_ask": str(k.yes_ask),
                            "kalshi_yes_ask_size": str(k.yes_ask_size),
                            "quote_skew_ms": quote_skew_ms,
                            "fresh": fresh,
                            "gemini_yes_plus_kalshi_no_cost_pre_fee": str(c_g_yes),
                            "kalshi_yes_plus_gemini_no_cost_pre_fee": str(c_k_yes),
                            "best_cost_pre_fee": str(best_cost),
                            "raw_edge_per_contract": str(raw_edge),
                            "pre_fee_candidates": _candidate_rows(pre_fee),
                            "gemini_taker_fee_only_candidates": _candidate_rows(gemini_taker_only),
                            "kalshi_fee_status": "UNMODELED_DO_NOT_TRADE",
                            "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
                            "read_only": True,
                            "order_entry": False,
                        }
                        fh.write(json.dumps(row, separators=(",", ":")) + "\n")

                        if pre_fee:
                            logger.info("ALIGNED_WATCH_LOCK %s", json.dumps(row, separators=(",", ":")))

                    fh.flush()
                    sleep_for = poll_s - (time.monotonic() - loop_started)
                    if sleep_for > 0:
                        await asyncio.sleep(sleep_for)
            finally:
                stop.set()
                for task in stream_tasks:
                    task.cancel()
                await asyncio.gather(*stream_tasks, return_exceptions=True)

        end_ms = int(time.time() * 1000)
        for asset, bucket in stats.items():
            if bucket["in_episode"]:
                logger.info("ALIGNED_WATCH_EPISODE %s", json.dumps({
                    "asset": asset,
                    "start_ms": bucket["episode_start_ms"],
                    "end_ms": end_ms,
                    "duration_ms": end_ms - int(bucket["episode_start_ms"]),
                    "best_raw_edge_per_contract": str(bucket["episode_best_edge"]),
                    "max_displayed_pre_fee_quantity": str(bucket["episode_max_qty"]),
                    "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
                }, separators=(",", ":")))

        summary = {
            asset: {
                "samples": bucket["samples"],
                "fresh_samples": bucket["fresh_samples"],
                "raw_positive_samples": bucket["raw_positive_samples"],
                "pre_fee_candidate_samples": bucket["pre_fee_candidate_samples"],
                "gemini_taker_only_candidate_samples": bucket["gemini_taker_only_candidate_samples"],
                "best_raw_edge_per_contract": str(bucket["best_raw_edge_per_contract"]),
                "max_displayed_pre_fee_quantity": str(bucket["max_pre_fee_quantity"]),
                "episodes": bucket["episodes"],
            }
            for asset, bucket in stats.items()
        }
        logger.info("ALIGNED_WATCH_SUMMARY %s", json.dumps({
            "by_asset": summary,
            "output": str(output),
            "kalshi_fee_status": "UNMODELED_DO_NOT_TRADE",
            "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
            "repeated_depth_counted_as_pnl": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=900.0)
    parser.add_argument("--poll-s", type=float, default=1.0)
    parser.add_argument("--max-window-skew-s", type=float, default=30.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/gemini_kalshi_aligned_watch.jsonl"),
    )
    args = parser.parse_args()
    if args.seconds <= 0 or args.poll_s <= 0:
        raise SystemExit("--seconds and --poll-s must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(watch(
        seconds=args.seconds,
        poll_s=args.poll_s,
        max_window_skew_s=args.max_window_skew_s,
        max_quote_skew_ms=args.max_quote_skew_ms,
        output=args.output,
    ))


if __name__ == "__main__":
    main()
