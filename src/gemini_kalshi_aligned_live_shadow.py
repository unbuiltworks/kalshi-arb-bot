"""Read-only live shadow for truly aligned Gemini/Kalshi 15-minute crypto windows.

Uses Kalshi close_time as the 15-minute measurement-window endpoint, streams a
periodic Gemini depth5 snapshot (with size), pulls Kalshi depth-1, and reports both
raw complementary costs and executable-shadow candidates. No order-entry path exists.

Settlement remains INDEX_MISMATCH_RESEARCH_ONLY. Kalshi fees are intentionally not
assumed here; the runner reports a zero-fee view and a Gemini-taker-only view so an
apparent lock is never mislabeled as final executable P&L.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import time

import httpx
import websockets

from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
    zero_fee,
)
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, _gemini_meta, _gemini_symbol
from src.gemini_kalshi_live_shadow import kalshi_book_once
from src.gemini_kalshi_rest_snapshot import _kalshi_open_paced
from src.gemini_kalshi_window_identity import kalshi_15m_asset, kalshi_window_end

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_aligned_live_shadow")
GEMINI_WS = "wss://ws.gemini.com"


def _candidate_rows(candidates):
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


async def gemini_depth5_once(symbol: str, *, timeout_s: float = 5.0) -> TopOfBook:
    """Get a periodic Gemini top-of-book snapshot with displayed quantities.

    bookTicker is update-driven and can stay silent on an unchanged/illiquid book.
    depth5@100ms is a public periodic snapshot stream, so it is suitable for a
    one-shot executable-depth probe.
    """
    async with websockets.connect(GEMINI_WS, open_timeout=timeout_s) as ws:
        await ws.send(json.dumps({
            "id": "1",
            "method": "SUBSCRIBE",
            "params": [f"{symbol}@depth5@100ms"],
        }))
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no Gemini depth5 snapshot for {symbol}")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            payload = json.loads(raw)
            bids = payload.get("bids")
            asks = payload.get("asks")
            if not isinstance(bids, list) or not isinstance(asks, list):
                continue
            if not bids or not asks:
                raise ValueError(f"Gemini depth5 book is not two-sided for {symbol}")
            bid_price, bid_size = max(bids, key=lambda row: D(str(row[0])))
            ask_price, ask_size = min(asks, key=lambda row: D(str(row[0])))
            return TopOfBook(
                venue="gemini",
                market_id=symbol,
                ts_ms=int(time.time() * 1000),
                yes_bid=D(str(bid_price)),
                yes_bid_size=D(str(bid_size)),
                yes_ask=D(str(ask_price)),
                yes_ask_size=D(str(ask_size)),
            )


async def run_once(*, max_window_skew_s: float = 30.0, max_quote_skew_ms: int = 500) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_markets = await asyncio.gather(
            _gemini_active_crypto(http), _kalshi_open_paced(http)
        )

        kalshi_15m = []
        for market in kalshi_markets:
            asset = kalshi_15m_asset(market)
            end = kalshi_window_end(market)
            if asset and end:
                kalshi_15m.append((market, asset, end))

        overlaps = []
        for event in gemini_events:
            meta = _gemini_meta(event)
            if meta is None:
                continue
            asset, duration, g_end = meta
            if duration != 15:
                continue
            for market, k_asset, k_end in kalshi_15m:
                if asset != k_asset:
                    continue
                if abs((g_end - k_end).total_seconds()) <= max_window_skew_s:
                    overlaps.append((event, market, asset, g_end))

        logger.info("ALIGNED_LIVE_DISCOVERY %s", json.dumps({
            "gemini_events": len(gemini_events),
            "kalshi_markets": len(kalshi_markets),
            "aligned_overlaps": len(overlaps),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

        for event, market, asset, g_end in overlaps:
            symbol = _gemini_symbol(event)
            ticker = str(market.get("ticker") or "")
            if not symbol or not ticker:
                continue
            try:
                g, k = await asyncio.gather(
                    gemini_depth5_once(symbol),
                    kalshi_book_once(http, ticker),
                )
            except Exception as exc:
                logger.warning("ALIGNED_LIVE_BOOK_ERROR %s", json.dumps({
                    "asset": asset,
                    "gemini_symbol": symbol,
                    "kalshi_ticker": ticker,
                    "error_type": type(exc).__name__,
                    "error": str(exc) or repr(exc),
                }, separators=(",", ":")))
                continue

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

            row = {
                "asset": asset,
                "window_end_utc": g_end.isoformat(),
                "gemini_symbol": symbol,
                "kalshi_ticker": ticker,
                "gemini_yes_bid": str(g.yes_bid),
                "gemini_yes_bid_size": str(g.yes_bid_size),
                "gemini_yes_ask": str(g.yes_ask),
                "gemini_yes_ask_size": str(g.yes_ask_size),
                "kalshi_yes_bid": str(k.yes_bid),
                "kalshi_yes_bid_size": str(k.yes_bid_size),
                "kalshi_yes_ask": str(k.yes_ask),
                "kalshi_yes_ask_size": str(k.yes_ask_size),
                "quote_skew_ms": abs(g.ts_ms - k.ts_ms),
                "gemini_yes_plus_kalshi_no_cost_pre_fee": str(g.yes_ask + k.no_ask),
                "kalshi_yes_plus_gemini_no_cost_pre_fee": str(k.yes_ask + g.no_ask),
                "pre_fee_candidates": _candidate_rows(pre_fee),
                "gemini_taker_fee_only_candidates": _candidate_rows(gemini_taker_only),
                "kalshi_fee_status": "UNMODELED_DO_NOT_TRADE",
                "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
                "read_only": True,
                "order_entry": False,
            }
            logger.info("GEMINI_KALSHI_ALIGNED_LIVE_PAIR %s", json.dumps(row, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-window-skew-s", type=float, default=30.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once(
        max_window_skew_s=args.max_window_skew_s,
        max_quote_skew_ms=args.max_quote_skew_ms,
    ))


if __name__ == "__main__":
    main()
