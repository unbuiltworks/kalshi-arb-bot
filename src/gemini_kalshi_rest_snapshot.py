"""Read-only REST-only snapshot for Gemini <-> Kalshi 15-minute crypto overlaps.

This is intentionally deploy-friendly: no credentials, no websocket dependency, no
order entry. Gemini event payloads expose current bestBid/bestAsk; Kalshi public
orderbooks expose bid depth. The output is a discovery/price snapshot only and is
not executable-P&L evidence because Gemini REST does not expose top-level size in
the same response and fees/basis risk still need separate modeling.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, discover_overlaps
from src.gemini_kalshi_live_shadow import KALSHI_REST, parse_kalshi_orderbook

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_rest_snapshot")


def gemini_rest_prices(event: dict[str, Any], instrument_symbol: str) -> tuple[Decimal, Decimal]:
    """Return documented Gemini REST best bid/ask for one contract."""
    for contract in event.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        symbol = str(contract.get("instrumentSymbol") or contract.get("instrument_symbol") or "")
        if symbol != instrument_symbol:
            continue
        prices = contract.get("prices") or {}
        bid = prices.get("bestBid")
        ask = prices.get("bestAsk")
        if bid is None or ask is None:
            raise ValueError(f"Gemini REST prices missing for {instrument_symbol}")
        return D(str(bid)), D(str(ask))
    raise ValueError(f"Gemini contract not found: {instrument_symbol}")


async def _kalshi_open_paced(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch open Kalshi markets without bursting into the public REST rate limit."""
    rows: list[dict[str, Any]] = []
    cursor = ""
    page = 0
    while True:
        params: dict[str, Any] = {"status": "open", "limit": 1000, "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor

        response: httpx.Response | None = None
        for attempt in range(6):
            response = await http.get(f"{KALSHI_REST}/markets", params=params)
            if response.status_code != 429:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(8.0, 0.75 * (2 ** attempt))
            except ValueError:
                delay = min(8.0, 0.75 * (2 ** attempt))
            logger.warning("KALSHI_429 page=%s attempt=%s sleep_s=%.2f", page, attempt + 1, delay)
            await asyncio.sleep(delay)

        assert response is not None
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload.get("markets", []))
        cursor = str(payload.get("cursor") or "")
        page += 1
        if not cursor:
            return rows

        # The unpaced scanner can cross Kalshi's anonymous REST threshold after
        # ~15-20 pages. A small inter-page delay is cheap for a 5-minute snapshot
        # and keeps this lane independent of credentials.
        await asyncio.sleep(0.35)


async def run_once(*, max_expiry_skew_s: float = 30.0) -> None:
    started_ms = int(time.time() * 1000)
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_markets = await asyncio.gather(
            _gemini_active_crypto(http), _kalshi_open_paced(http)
        )
        _, _, overlaps = discover_overlaps(
            gemini_events, kalshi_markets, max_expiry_skew_s=max_expiry_skew_s
        )
        by_ticker = {str(event.get("ticker") or ""): event for event in gemini_events}

        rows: list[dict[str, Any]] = []
        for overlap in overlaps:
            event = by_ticker.get(overlap.gemini_event_ticker)
            if not event or not overlap.gemini_instrument_symbol:
                continue
            try:
                g_bid, g_ask = gemini_rest_prices(event, overlap.gemini_instrument_symbol)
                response = await http.get(
                    f"{KALSHI_REST}/markets/{overlap.kalshi_ticker}/orderbook",
                    params={"depth": 1},
                )
                response.raise_for_status()
                k_tob = parse_kalshi_orderbook(
                    response.json(), ticker=overlap.kalshi_ticker,
                    received_ms=int(time.time() * 1000),
                )
            except Exception as exc:
                logger.warning("REST_SNAPSHOT_BOOK_ERROR %s", json.dumps({
                    "gemini": overlap.gemini_instrument_symbol,
                    "kalshi": overlap.kalshi_ticker,
                    "error": str(exc),
                }, separators=(",", ":")))
                continue

            # Two complementary-lock directions, before fees. These are price-only
            # diagnostics, not trade signals: Gemini REST size is not included here.
            g_yes_k_no = g_ask + (D("1") - k_tob.yes_bid)
            k_yes_g_no = k_tob.yes_ask + (D("1") - g_bid)
            row = {
                "asset": overlap.asset,
                "expiry_utc": overlap.gemini_expiry.isoformat(),
                "gemini_event": overlap.gemini_event_ticker,
                "gemini_symbol": overlap.gemini_instrument_symbol,
                "kalshi_ticker": overlap.kalshi_ticker,
                "gemini_yes_bid": str(g_bid),
                "gemini_yes_ask": str(g_ask),
                "kalshi_yes_bid": str(k_tob.yes_bid),
                "kalshi_yes_ask": str(k_tob.yes_ask),
                "gemini_yes_plus_kalshi_no_cost_pre_fee": str(g_yes_k_no),
                "kalshi_yes_plus_gemini_no_cost_pre_fee": str(k_yes_g_no),
                "pre_fee_price_lock": bool(g_yes_k_no < 1 or k_yes_g_no < 1),
                "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
                "fee_status": "UNMODELED_DO_NOT_TRADE",
                "depth_status": "GEMINI_REST_SIZE_UNAVAILABLE",
            }
            rows.append(row)
            logger.info("GEMINI_KALSHI_REST_PAIR %s", json.dumps(row, separators=(",", ":")))

        logger.info("GEMINI_KALSHI_REST_SUMMARY %s", json.dumps({
            "gemini_events": len(gemini_events),
            "kalshi_markets": len(kalshi_markets),
            "same_window_overlaps": len(overlaps),
            "priced_pairs": len(rows),
            "pre_fee_price_locks": sum(1 for r in rows if r["pre_fee_price_lock"]),
            "elapsed_ms": int(time.time() * 1000) - started_ms,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once(max_expiry_skew_s=args.max_expiry_skew_s))


if __name__ == "__main__":
    main()
