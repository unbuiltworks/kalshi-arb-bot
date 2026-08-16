"""Read-only price snapshot for truly aligned Gemini/Kalshi 15-minute windows."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, _gemini_meta, _gemini_symbol
from src.gemini_kalshi_live_shadow import KALSHI_REST, parse_kalshi_orderbook
from src.gemini_kalshi_rest_snapshot import _kalshi_open_paced, gemini_rest_prices
from src.gemini_kalshi_window_identity import kalshi_15m_asset, kalshi_window_end, kalshi_expected_resolution

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_aligned_snapshot")


async def run_once(max_skew_s: float = 30.0) -> None:
    started_ms = int(time.time() * 1000)
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

        overlaps: list[tuple[dict[str, Any], dict[str, Any], str, Any, Any]] = []
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
                if abs((g_end - k_end).total_seconds()) <= max_skew_s:
                    overlaps.append((event, market, asset, g_end, k_end))

        priced = []
        for event, market, asset, g_end, k_end in overlaps:
            symbol = _gemini_symbol(event)
            if not symbol:
                continue
            try:
                g_bid, g_ask = gemini_rest_prices(event, symbol)
                ticker = str(market.get("ticker") or "")
                response = await http.get(f"{KALSHI_REST}/markets/{ticker}/orderbook", params={"depth": 1})
                response.raise_for_status()
                k = parse_kalshi_orderbook(response.json(), ticker=ticker, received_ms=int(time.time() * 1000))
            except Exception as exc:
                logger.warning("ALIGNED_BOOK_ERROR %s", json.dumps({"asset": asset, "error": str(exc)}))
                continue

            g_yes_k_no = g_ask + (D("1") - k.yes_bid)
            k_yes_g_no = k.yes_ask + (D("1") - g_bid)
            best_cost = min(g_yes_k_no, k_yes_g_no)
            row = {
                "asset": asset,
                "gemini_event": event.get("ticker"),
                "gemini_symbol": symbol,
                "kalshi_ticker": market.get("ticker"),
                "window_end_utc": g_end.isoformat(),
                "kalshi_close_time": market.get("close_time"),
                "kalshi_expected_resolution": (kalshi_expected_resolution(market).isoformat() if kalshi_expected_resolution(market) else None),
                "gemini_yes_bid": str(g_bid),
                "gemini_yes_ask": str(g_ask),
                "kalshi_yes_bid": str(k.yes_bid),
                "kalshi_yes_ask": str(k.yes_ask),
                "gemini_yes_plus_kalshi_no_cost_pre_fee": str(g_yes_k_no),
                "kalshi_yes_plus_gemini_no_cost_pre_fee": str(k_yes_g_no),
                "best_complementary_cost_pre_fee": str(best_cost),
                "pre_fee_price_lock": bool(best_cost < 1),
                "gross_edge_per_contract_pre_fee": str(max(D("0"), D("1") - best_cost)),
                "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
                "fee_status": "UNMODELED_DO_NOT_TRADE",
                "depth_status": "GEMINI_REST_SIZE_UNAVAILABLE",
            }
            priced.append(row)
            logger.info("GEMINI_KALSHI_ALIGNED_PAIR %s", json.dumps(row, separators=(",", ",")))

        logger.info("GEMINI_KALSHI_ALIGNED_SUMMARY %s", json.dumps({
            "gemini_events": len(gemini_events),
            "kalshi_markets": len(kalshi_markets),
            "kalshi_15m_candidates": len(kalshi_15m),
            "true_same_window_overlaps": len(overlaps),
            "priced_pairs": len(priced),
            "pre_fee_price_locks": sum(1 for row in priced if row["pre_fee_price_lock"]),
            "best_gross_edge_pre_fee": max((row["gross_edge_per_contract_pre_fee"] for row in priced), default=None),
            "elapsed_ms": int(time.time() * 1000) - started_ms,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
