"""Read-only live market-data runner for Gemini <-> Kalshi 15-minute crypto pairs.

No order entry is present. Gemini uses the public prediction-market bookTicker
WebSocket stream; Kalshi uses the public REST orderbook endpoint. Matching still
comes from gemini_kalshi_fast_overlap and every candidate remains
INDEX_MISMATCH_RESEARCH_ONLY until settlement-basis risk is quantified.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

import httpx
import websockets

from src.gemini_kalshi_execution_shadow import TopOfBook, evaluate_pair, zero_fee
from src.gemini_kalshi_fast_overlap import (
    _gemini_active_crypto,
    _kalshi_open,
    discover_overlaps,
)

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_live_shadow")
GEMINI_WS = "wss://ws.gemini.com"
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"


def _finite_decimal(value: Any) -> Decimal | None:
    """Best-effort Decimal parse for live market-data fields.

    Gemini prediction books can be one-sided. In production a missing side may be
    represented by a non-numeric string even though the documented bookTicker
    schema describes the fields as strings. Treat such values as unavailable
    liquidity rather than crashing a read-only market-data stream.
    """
    try:
        value_d = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value_d if value_d.is_finite() else None


def parse_gemini_book_ticker(payload: dict[str, Any], *, received_ms: int | None = None) -> TopOfBook:
    """Convert a Gemini prediction-market bookTicker message to normalized TOB.

    Missing/non-numeric sides are represented with zero executable size. A
    missing bid becomes price 0/size 0; a missing ask becomes price 1/size 0.
    This preserves the executable complementary binary side without inventing
    liquidity.
    """
    if not payload.get("s"):
        raise ValueError("not a Gemini bookTicker payload")

    bid = _finite_decimal(payload.get("b"))
    bid_size = _finite_decimal(payload.get("B"))
    ask = _finite_decimal(payload.get("a"))
    ask_size = _finite_decimal(payload.get("A"))

    if (
        bid is None
        or bid_size is None
        or bid < 0
        or bid > 1
        or bid_size <= 0
    ):
        bid = D("0")
        bid_size = D("0")

    if (
        ask is None
        or ask_size is None
        or ask < 0
        or ask > 1
        or ask_size <= 0
    ):
        ask = D("1")
        ask_size = D("0")

    ts_ms = int(received_ms if received_ms is not None else time.time() * 1000)
    return TopOfBook(
        venue="gemini",
        market_id=str(payload["s"]),
        ts_ms=ts_ms,
        yes_bid=bid,
        yes_bid_size=bid_size,
        yes_ask=ask,
        yes_ask_size=ask_size,
    )


def parse_kalshi_orderbook(payload: dict[str, Any], *, ticker: str, received_ms: int | None = None) -> TopOfBook:
    """Normalize Kalshi bid-only binary book into a YES bid/ask top-of-book.

    Kalshi returns YES bids and NO bids. The best YES ask is 1 - best NO bid,
    with identical size to that NO bid level.
    """
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    yes_levels = book.get("yes_dollars") or book.get("yes_dollars_fp") or []
    no_levels = book.get("no_dollars") or book.get("no_dollars_fp") or []
    if not yes_levels or not no_levels:
        raise ValueError("Kalshi book is not two-sided")
    yes_price, yes_size = max(yes_levels, key=lambda x: D(str(x[0])))
    no_price, no_size = max(no_levels, key=lambda x: D(str(x[0])))
    ts_ms = int(received_ms if received_ms is not None else time.time() * 1000)
    return TopOfBook(
        venue="kalshi",
        market_id=ticker,
        ts_ms=ts_ms,
        yes_bid=D(str(yes_price)),
        yes_bid_size=D(str(yes_size)),
        yes_ask=D("1") - D(str(no_price)),
        yes_ask_size=D(str(no_size)),
    )


async def gemini_book_ticker_once(symbol: str, *, timeout_s: float = 5.0) -> TopOfBook:
    async with websockets.connect(GEMINI_WS, open_timeout=timeout_s) as ws:
        await ws.send(json.dumps({
            "id": "1",
            "method": "SUBSCRIBE",
            "params": [f"{symbol}@bookTicker"],
        }))
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no Gemini bookTicker for {symbol}")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            payload = json.loads(raw)
            if str(payload.get("s") or "").lower() == symbol.lower():
                return parse_gemini_book_ticker(payload, received_ms=int(time.time() * 1000))


async def kalshi_book_once(http: httpx.AsyncClient, ticker: str) -> TopOfBook:
    response = await http.get(f"{KALSHI_REST}/markets/{ticker}/orderbook", params={"depth": 1})
    response.raise_for_status()
    return parse_kalshi_orderbook(response.json(), ticker=ticker, received_ms=int(time.time() * 1000))


async def run_once(*, max_expiry_skew_s: float = 30.0, max_quote_skew_ms: int = 250) -> None:
    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini_events, kalshi_markets = await asyncio.gather(
            _gemini_active_crypto(http), _kalshi_open(http)
        )
        _, _, overlaps = discover_overlaps(
            gemini_events, kalshi_markets, max_expiry_skew_s=max_expiry_skew_s
        )
        logger.info("LIVE_SHADOW_DISCOVERY %s", json.dumps({
            "overlaps": len(overlaps),
            "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
            "read_only": True,
        }, separators=(",", ":")))

        for overlap in overlaps:
            if not overlap.gemini_instrument_symbol:
                continue
            try:
                gemini_tob, kalshi_tob = await asyncio.gather(
                    gemini_book_ticker_once(overlap.gemini_instrument_symbol),
                    kalshi_book_once(http, overlap.kalshi_ticker),
                )
            except Exception as exc:
                logger.warning("LIVE_SHADOW_BOOK_ERROR %s", json.dumps({
                    "gemini": overlap.gemini_instrument_symbol,
                    "kalshi": overlap.kalshi_ticker,
                    "error": str(exc),
                }, separators=(",", ":")))
                continue

            candidates = evaluate_pair(
                gemini_tob,
                kalshi_tob,
                gemini_fee=zero_fee,
                kalshi_fee=zero_fee,
                max_quote_skew_ms=max_quote_skew_ms,
            )
            logger.info("LIVE_SHADOW_PAIR %s", json.dumps({
                "asset": overlap.asset,
                "expiry_utc": overlap.gemini_expiry.isoformat(),
                "gemini_symbol": overlap.gemini_instrument_symbol,
                "kalshi_ticker": overlap.kalshi_ticker,
                "gemini_yes_bid": str(gemini_tob.yes_bid),
                "gemini_yes_ask": str(gemini_tob.yes_ask),
                "kalshi_yes_bid": str(kalshi_tob.yes_bid),
                "kalshi_yes_ask": str(kalshi_tob.yes_ask),
                "quote_skew_ms": abs(gemini_tob.ts_ms - kalshi_tob.ts_ms),
                "zero_fee_positive_locks": [
                    {
                        "direction": c.direction,
                        "quantity": str(c.quantity),
                        "locked_profit": str(c.locked_profit),
                        "roi": str(c.roi_on_cost),
                    }
                    for c in candidates
                ],
                "fee_status": "UNMODELED_DO_NOT_TRADE",
                "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
            }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=250)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once(
        max_expiry_skew_s=args.max_expiry_skew_s,
        max_quote_skew_ms=args.max_quote_skew_ms,
    ))


if __name__ == "__main__":
    main()
