"""
Market discovery — fetch open markets and filter for arb candidates.

Strategy: use the /events endpoint to find active events, then collect
their individual market tickers. Events with many markets and high volume
are the best arb candidates (bracket-style pricing creates mispricings).
"""

import logging

from config.settings import MAX_MARKETS
from src.auth import KalshiClient

logger = logging.getLogger("kalshi_arb.markets")


async def discover_markets(client: KalshiClient) -> list[str]:
    """
    Fetch open events with nested markets, rank by liquidity, return top
    market tickers.

    Returns:
        list of market ticker strings
    """
    # Collect (ticker, volume, event_market_count) for ranking
    candidates: list[tuple[str, float, int]] = []
    cursor: str | None = None
    max_pages = 20

    for _ in range(max_pages):
        params: dict = {
            "status": "open",
            "limit": 100,
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor

        resp = await client.get("/events", params=params)
        resp.raise_for_status()
        data = resp.json()

        events = data.get("events", [])
        if not events:
            break

        for event in events:
            markets = event.get("markets", [])
            n_markets = len(markets)
            for m in markets:
                ticker = m.get("ticker", "")
                if not ticker:
                    continue
                # Use the _fp fields (string) for volume
                vol = float(m.get("volume_fp", "0") or "0")
                # Also check if there's any book activity
                bid = float(m.get("yes_bid_dollars", "0") or "0")
                ask = float(m.get("yes_ask_dollars", "0") or "0")
                has_book = bid > 0 or ask > 0
                # Score: volume + bonus for having a live book + bonus for multi-market events
                score = vol + (1000 if has_book else 0) + (n_markets * 10)
                candidates.append((ticker, score, n_markets))

        cursor = data.get("cursor")
        if not cursor:
            break

        # Early exit if we have plenty
        with_book = [c for c in candidates if c[1] >= 1000]
        if len(with_book) >= MAX_MARKETS * 3:
            break

    # Sort by score descending, take top N
    candidates.sort(key=lambda x: x[1], reverse=True)
    result = [ticker for ticker, _, _ in candidates[:MAX_MARKETS]]

    if result:
        logger.info(
            "Discovered %d candidate markets, tracking top %d (top score: %.0f)",
            len(candidates), len(result), candidates[0][1] if candidates else 0,
        )
    else:
        logger.warning("No markets discovered")

    return result
