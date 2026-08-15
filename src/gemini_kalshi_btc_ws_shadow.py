"""Read-only sub-second BTC Gemini/Kalshi execution-shadow collector.

Purpose: replace the 1s Kalshi REST polling ambiguity with Kalshi's authenticated
``orderbook_delta`` market-data stream. This module has no order-entry imports or
write endpoints. Authentication is used only because Kalshi requires it for the
WebSocket handshake, even for market-data channels.

The collector:
- discovers the current aligned BTC 15-minute pair,
- verifies the live KXBTC15M fee metadata is mapped,
- streams Gemini depth5@100ms snapshots,
- reconstructs Kalshi's order book from snapshot + deltas,
- preserves safely executable directions when either venue is one-sided,
- evaluates both-venue taker-fee-positive complementary locks on every event,
- records receive timestamps and Kalshi exchange ``ts_ms`` for latency analysis,
- fails loudly if either stream dies,
- rolls automatically into the next BTC 15-minute window until ``--seconds`` ends.

Settlement remains INDEX_MISMATCH_RESEARCH_ONLY. No live trading path exists.
"""

from __future__ import annotations

import argparse
import asyncio
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
from src.gemini_kalshi_aligned_watch import Pair, _discover_pairs
from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata
from src.gemini_kalshi_live_shadow import GEMINI_WS

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_btc_ws_shadow")
KALSHI_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
KALSHI_WS_SIGN_PATH = "/trade-api/ws/v2"


class KalshiOrderBook:
    """Reconstruct one Kalshi binary market from orderbook snapshot + deltas."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.yes: dict[Decimal, Decimal] = {}
        self.no: dict[Decimal, Decimal] = {}
        self.exchange_ts_ms: int | None = None
        self.seq: int | None = None

    @staticmethod
    def _levels(rows: Any) -> dict[Decimal, Decimal]:
        out: dict[Decimal, Decimal] = {}
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            price = D(str(row[0]))
            qty = D(str(row[1]))
            if qty > 0:
                out[price] = qty
        return out

    def apply(self, payload: dict[str, Any]) -> bool:
        msg_type = payload.get("type")
        msg = payload.get("msg") or {}
        if str(msg.get("market_ticker") or "") != self.ticker:
            return False

        seq = payload.get("seq")
        if seq is not None:
            seq_i = int(seq)
            if self.seq is not None and seq_i <= self.seq:
                return False
            self.seq = seq_i

        if msg_type == "orderbook_snapshot":
            self.yes = self._levels(msg.get("yes_dollars_fp"))
            self.no = self._levels(msg.get("no_dollars_fp"))
            return True

        if msg_type != "orderbook_delta":
            return False

        side = str(msg.get("side") or "").lower()
        if side not in {"yes", "no"}:
            return False
        price_raw = msg.get("price_dollars")
        delta_raw = msg.get("delta_fp")
        if price_raw is None or delta_raw is None:
            return False
        price = D(str(price_raw))
        delta = D(str(delta_raw))
        levels = self.yes if side == "yes" else self.no
        new_qty = levels.get(price, D("0")) + delta
        if new_qty <= 0:
            levels.pop(price, None)
        else:
            levels[price] = new_qty
        if msg.get("ts_ms") is not None:
            self.exchange_ts_ms = int(msg["ts_ms"])
        return True

    def top(self, receive_ts_ms: int) -> TopOfBook | None:
        """Return safely executable TOB even if only one binary side is quoted.

        A missing YES bid is represented as price 0 with size 0; a missing NO bid
        therefore becomes a synthetic YES ask of 1 with size 0. The zero size is
        the safety property: evaluate_pair can use the opposite quoted direction
        but can never invent executable liquidity on the missing side.
        """
        if not self.yes and not self.no:
            return None

        if self.yes:
            yes_bid = max(self.yes)
            yes_bid_size = self.yes[yes_bid]
        else:
            yes_bid = D("0")
            yes_bid_size = D("0")

        if self.no:
            no_bid = max(self.no)
            yes_ask = D("1") - no_bid
            yes_ask_size = self.no[no_bid]
        else:
            yes_ask = D("1")
            yes_ask_size = D("0")

        if yes_ask < 0 or yes_ask > 1:
            return None
        return TopOfBook(
            venue="kalshi",
            market_id=self.ticker,
            ts_ms=receive_ts_ms,
            yes_bid=yes_bid,
            yes_bid_size=yes_bid_size,
            yes_ask=yes_ask,
            yes_ask_size=yes_ask_size,
        )


def _gemini_top(payload: dict[str, Any], symbol: str, receive_ts_ms: int) -> TopOfBook | None:
    """Normalize a Gemini depth frame without discarding a one-sided book."""
    bids = payload.get("bids")
    asks = payload.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        return None
    if not bids and not asks:
        return None

    if bids:
        bid_price, bid_size = max(bids, key=lambda row: D(str(row[0])))
        yes_bid = D(str(bid_price))
        yes_bid_size = D(str(bid_size))
    else:
        yes_bid = D("0")
        yes_bid_size = D("0")

    if asks:
        ask_price, ask_size = min(asks, key=lambda row: D(str(row[0])))
        yes_ask = D(str(ask_price))
        yes_ask_size = D(str(ask_size))
    else:
        yes_ask = D("1")
        yes_ask_size = D("0")

    return TopOfBook(
        venue="gemini",
        market_id=symbol,
        ts_ms=receive_ts_ms,
        yes_bid=yes_bid,
        yes_bid_size=yes_bid_size,
        yes_ask=yes_ask,
        yes_ask_size=yes_ask_size,
    )


def _ws_header_kw(headers: dict[str, str]) -> dict[str, Any]:
    """Support websockets 12.x (extra_headers) and newer releases."""
    params = inspect.signature(websockets.connect).parameters
    key = "additional_headers" if "additional_headers" in params else "extra_headers"
    return {key: headers}


def _subscription(ticker: str) -> dict[str, Any]:
    return {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_ticker": ticker,
        },
    }


async def _gemini_stream(symbol: str, queue: asyncio.Queue) -> None:
    async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
        await ws.send(json.dumps({
            "id": "btc-ws-shadow",
            "method": "SUBSCRIBE",
            "params": [f"{symbol}@depth5@100ms"],
        }))
        async for raw in ws:
            receive_ms = int(time.time() * 1000)
            payload = json.loads(raw)
            if payload.get("status") is not None:
                if int(payload.get("status")) >= 400:
                    raise RuntimeError(f"Gemini WS subscription error: {payload}")
                continue
            book = _gemini_top(payload, symbol, receive_ms)
            if book is not None:
                await queue.put(("gemini", receive_ms, None, book))
    raise RuntimeError(f"Gemini WS ended for {symbol}")


async def _kalshi_stream(ticker: str, queue: asyncio.Queue) -> None:
    private_key = load_private_key()
    headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
    book = KalshiOrderBook(ticker)
    async with websockets.connect(
        KALSHI_WS,
        open_timeout=5.0,
        **_ws_header_kw(headers),
    ) as ws:
        await ws.send(json.dumps(_subscription(ticker)))
        async for raw in ws:
            receive_ms = int(time.time() * 1000)
            payload = json.loads(raw)
            if payload.get("type") == "error":
                raise RuntimeError(f"Kalshi WS error: {payload.get('msg')}")
            if not book.apply(payload):
                continue
            top = book.top(receive_ms)
            if top is not None:
                await queue.put(("kalshi", receive_ms, book.exchange_ts_ms, top))
    raise RuntimeError(f"Kalshi WS ended for {ticker}")


def _candidate_rows(candidates) -> list[dict[str, str | int]]:
    return [
        {
            "direction": c.direction,
            "quantity": str(c.quantity),
            "net_cost": str(c.net_cost),
            "fees": str(c.fees),
            "locked_profit": str(c.locked_profit),
            "roi": str(c.roi_on_cost),
            "quote_skew_ms": c.quote_skew_ms,
        }
        for c in candidates
    ]


def _raise_if_stream_failed(tasks: dict[str, asyncio.Task]) -> None:
    for name, task in tasks.items():
        if not task.done():
            continue
        if task.cancelled():
            raise RuntimeError(f"{name} stream cancelled unexpectedly")
        exc = task.exception()
        if exc is not None:
            raise RuntimeError(f"{name} stream failed: {type(exc).__name__}: {exc}") from exc
        raise RuntimeError(f"{name} stream ended unexpectedly")


async def _current_btc_pair(http: httpx.AsyncClient) -> Pair | None:
    now = time.time()
    pairs = [
        p for p in await _discover_pairs(http, 30.0)
        if p.asset == "BTC" and p.window_end.timestamp() > now
    ]
    return pairs[0] if pairs else None


async def _capture_pair(
    *,
    pair: Pair,
    kalshi_fee,
    overall_deadline: float,
    output: Path,
    max_quote_skew_ms: int,
) -> tuple[int, int]:
    """Capture one BTC window, stopping just after its scheduled end."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
    tasks = {
        "gemini": asyncio.create_task(_gemini_stream(pair.gemini_symbol, queue)),
        "kalshi": asyncio.create_task(_kalshi_stream(pair.kalshi_ticker, queue)),
    }
    g: TopOfBook | None = None
    k: TopOfBook | None = None
    latest_k_exchange_ts: int | None = None
    samples = 0
    positive = 0

    remaining_window_s = max(0.0, pair.window_end.timestamp() - time.time() + 0.75)
    pair_deadline = min(overall_deadline, time.monotonic() + remaining_window_s)

    try:
        with output.open("a", encoding="utf-8") as fh:
            while time.monotonic() < pair_deadline:
                _raise_if_stream_failed(tasks)
                timeout = max(0.05, min(0.5, pair_deadline - time.monotonic()))
                try:
                    venue, receive_ms, exchange_ms, top = await asyncio.wait_for(
                        queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    _raise_if_stream_failed(tasks)
                    continue

                if venue == "gemini":
                    g = top
                else:
                    k = top
                    latest_k_exchange_ts = exchange_ms
                if g is None or k is None:
                    continue

                samples += 1
                candidates = evaluate_pair(
                    g,
                    k,
                    gemini_fee=gemini_predictions_taker_fee,
                    kalshi_fee=kalshi_fee,
                    max_quote_skew_ms=max_quote_skew_ms,
                )
                if candidates:
                    positive += 1

                row = {
                    "asset": "BTC",
                    "window_end_utc": pair.window_end.isoformat(),
                    "event_venue": venue,
                    "receive_ts_ms": receive_ms,
                    "gemini_receive_ts_ms": g.ts_ms,
                    "kalshi_receive_ts_ms": k.ts_ms,
                    "kalshi_exchange_ts_ms": latest_k_exchange_ts,
                    "kalshi_exchange_to_receive_ms": (
                        receive_ms - latest_k_exchange_ts
                        if latest_k_exchange_ts is not None and venue == "kalshi"
                        else None
                    ),
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
                    "both_taker_fee_candidates": _candidate_rows(candidates),
                    "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
                    "read_only": True,
                    "order_entry": False,
                }
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                fh.flush()
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    logger.info("BTC_WS_SHADOW_WINDOW_SUMMARY %s", json.dumps({
        "gemini_symbol": pair.gemini_symbol,
        "kalshi_ticker": pair.kalshi_ticker,
        "window_end_utc": pair.window_end.isoformat(),
        "samples": samples,
        "both_taker_fee_positive_event_samples": positive,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    return samples, positive


async def collect(*, seconds: float, output: Path, max_quote_skew_ms: int = 250) -> None:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    # Fail before starting a timed capture if the key is missing or malformed.
    load_private_key()

    output.parent.mkdir(parents=True, exist_ok=True)
    overall_deadline = time.monotonic() + seconds
    total_samples = 0
    total_positive = 0
    windows = 0

    async with httpx.AsyncClient(timeout=20.0) as http:
        fee_meta = await fetch_series_fee_metadata(http, "KXBTC15M")
        kalshi_fee = fee_meta.taker_fee_fn()  # fails closed for unmapped fee types

        while time.monotonic() < overall_deadline:
            pair = await _current_btc_pair(http)
            if pair is None:
                logger.info("BTC_WS_SHADOW_WAITING_FOR_PAIR")
                await asyncio.sleep(min(2.0, max(0.0, overall_deadline - time.monotonic())))
                continue

            logger.info("BTC_WS_SHADOW_DISCOVERY %s", json.dumps({
                "gemini_symbol": pair.gemini_symbol,
                "kalshi_ticker": pair.kalshi_ticker,
                "window_end_utc": pair.window_end.isoformat(),
                "fee_type": fee_meta.fee_type,
                "fee_multiplier": fee_meta.fee_multiplier,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))

            samples, positive = await _capture_pair(
                pair=pair,
                kalshi_fee=kalshi_fee,
                overall_deadline=overall_deadline,
                output=output,
                max_quote_skew_ms=max_quote_skew_ms,
            )
            windows += 1
            total_samples += samples
            total_positive += positive

    logger.info("BTC_WS_SHADOW_SUMMARY %s", json.dumps({
        "windows_captured": windows,
        "samples": total_samples,
        "both_taker_fee_positive_event_samples": total_positive,
        "output": str(output),
        "subsecond_status": "EVENT_STREAM_CAPTURED_ANALYZE_ENDPOINT_SURVIVAL_SEPARATELY",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=900.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=250)
    parser.add_argument("--output", type=Path, default=Path("logs/gemini_kalshi_btc_ws_shadow.jsonl"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(collect(
        seconds=args.seconds,
        output=args.output,
        max_quote_skew_ms=args.max_quote_skew_ms,
    ))


if __name__ == "__main__":
    main()
