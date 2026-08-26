"""Read-only quote-health census for strict Polymarket MLB monotone pairs.

This exists to distinguish a true lack of >=3c candidates from incomplete book
retrieval. It is deliberately isolated from order entry and runs with bounded
concurrency plus retry/backoff on HTTP 429 responses.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from src.sports_poly_dynamic_monotone import discover_current_pairs, score_pairs
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book

BASE = "https://gateway.polymarket.us"
CONCURRENCY = 4
RETRY_DELAYS = (0.25, 0.5, 1.0)


async def run_once() -> dict[str, Any]:
    feed, pairs = await discover_current_pairs()
    wanted = sorted({slug for pair in pairs for slug in pair})
    books: dict[str, TopBook] = {}
    health: dict[str, int] = {
        "requested_books": len(wanted),
        "successful_books": 0,
        "http_429s": 0,
        "retries": 0,
        "hard_failures": 0,
        "invalid_books": 0,
    }

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=6.0) as http:
        async def one(slug: str) -> None:
            async with sem:
                for attempt in range(len(RETRY_DELAYS) + 1):
                    if attempt:
                        health["retries"] += 1
                        await asyncio.sleep(RETRY_DELAYS[attempt - 1])
                    try:
                        response = await http.get(f"{BASE}/v1/markets/{slug}/book")
                    except Exception:
                        if attempt == len(RETRY_DELAYS):
                            health["hard_failures"] += 1
                        continue
                    if response.status_code == 429:
                        health["http_429s"] += 1
                        if attempt == len(RETRY_DELAYS):
                            health["hard_failures"] += 1
                        continue
                    if response.status_code != 200:
                        health["hard_failures"] += 1
                        return
                    try:
                        book = poly_top_book(response.json())
                    except Exception:
                        book = None
                    if book is None:
                        health["invalid_books"] += 1
                        return
                    books[slug] = book
                    health["successful_books"] += 1
                    return

        await asyncio.gather(*(one(slug) for slug in wanted))

    scored = score_pairs(feed, books, pairs)
    requested = health["requested_books"]
    valid_pct = (100.0 * health["successful_books"] / requested) if requested else 100.0
    payload: dict[str, Any] = {
        "strict_markets": len(feed),
        "strict_pairs": len(pairs),
        **health,
        "valid_quote_pct": round(valid_pct, 2),
        "candidate_count_gte_3c": len(scored),
        "best_locked_at_bid": str(max((r["locked_profit"] for r in scored), default=0)),
        "best_quote_locked": str(max((r["quote_locked_profit"] for r in scored), default=0)),
        "best_maker_gap_to_ask": str(min((r["maker_gap_to_ask"] for r in scored), default=0)),
        "read_only": True,
        "order_entry": False,
    }
    print("SPORTS_POLY_QUOTE_HEALTH " + json.dumps(payload, separators=(",", ":")), flush=True)
    return payload


if __name__ == "__main__":
    asyncio.run(run_once())
