"""One-shot public WebSocket coverage probe for exact Gemini/Kalshi MLB props.

No authentication and no order entry.  The probe compares Gemini's active-events
REST quote fields with the documented public ``@bookTicker`` stream for exact
Gemini/Kalshi normal-event counterparts.  Its purpose is to determine how much real
executable top-of-book coverage can be recovered without treating reference
``prices.buy`` values as liquidity.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from src.gemini_kalshi_live_shadow import gemini_book_ticker_once
from src.gemini_kalshi_mlb_prop_shadow import _candidate_sides
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events
from src.three_venue_mlb_price_scan import _gemini_contracts
from src.three_venue_pricing_core import gemini_contract_book
from src.universal_three_venue_sports import fetch_gemini_mlb

MAX_PROBE = 40
CONCURRENCY = 12


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_events = await asyncio.gather(
            fetch_gemini_mlb(http), _fetch_kalshi_events(http)
        )
    sides, _venues, joins, _context = _candidate_sides(kalshi_events, gemini_events)
    contracts = _gemini_contracts(gemini_events)
    symbols = []
    seen = set()
    for side in sides:
        if side.identity.venue != "gemini" or side.venue_market_id in seen:
            continue
        seen.add(side.venue_market_id)
        symbols.append(side.venue_market_id)

    rest_two_sided = [s for s in symbols if gemini_contract_book(contracts.get(s, {})) is not None]
    rest_unpriced = [s for s in symbols if s not in set(rest_two_sided)]
    targets = rest_unpriced[:MAX_PROBE]
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(symbol: str) -> dict[str, Any]:
        async with sem:
            try:
                book = await gemini_book_ticker_once(symbol, timeout_s=4.0)
            except Exception as exc:
                return {"symbol": symbol, "status": "ERROR", "error": type(exc).__name__}
        return {
            "symbol": symbol,
            "status": "BOOK",
            "yes_bid": str(book.yes_bid),
            "yes_bid_size": str(book.yes_bid_size),
            "yes_ask": str(book.yes_ask),
            "yes_ask_size": str(book.yes_ask_size),
            "has_yes_buy": bool(book.yes_ask_size > 0),
            "has_no_buy": bool(book.yes_bid_size > 0),
            "two_sided": bool(book.yes_ask_size > 0 and book.yes_bid_size > 0),
        }

    rows = await asyncio.gather(*(one(symbol) for symbol in targets))
    print(json.dumps({
        "status": "GEMINI_MLB_PROP_WS_COVERAGE",
        "exact_gk_join_rows": len(joins),
        "unique_gemini_match_symbols": len(symbols),
        "rest_two_sided_symbols": len(rest_two_sided),
        "rest_unpriced_symbols": len(rest_unpriced),
        "ws_sample_requested": len(targets),
        "ws_sample_books": sum(row.get("status") == "BOOK" for row in rows),
        "ws_sample_yes_buy": sum(bool(row.get("has_yes_buy")) for row in rows),
        "ws_sample_no_buy": sum(bool(row.get("has_no_buy")) for row in rows),
        "ws_sample_two_sided": sum(bool(row.get("two_sided")) for row in rows),
        "rows": rows,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
