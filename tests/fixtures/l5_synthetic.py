"""Synthetic Polymarket payloads for the L5 tests.

Shapes mirror the public Data API (/activity) and Gamma (/markets) responses.
The numbers are invented; nothing here is real trader data.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.polymarket_l5.cache import RawCache

WINDOW_START = 1_750_000_000  # arbitrary fixed epoch, keeps tests deterministic


def market(
    condition_id: str,
    slug: str,
    title: str,
    start_ts: int,
    duration_s: int,
    *,
    closed: bool = True,
    winner: str = "Up",
    event_slug: str = "",
) -> Dict[str, Any]:
    outcomes = ["Up", "Down"]
    prices = ["1", "0"] if winner == "Up" else ["0", "1"]
    return {
        "id": condition_id[-4:],
        "conditionId": condition_id,
        "slug": slug,
        "question": title,
        # Mirrors the real Gamma shape: startDate is the listing time (much
        # earlier than the window), eventStartTime is the true window start.
        "startDate": start_ts - 86_400,
        "eventStartTime": start_ts,
        "endDate": start_ts + duration_s,
        "closed": closed,
        "outcomes": str(outcomes).replace("'", '"'),
        "outcomePrices": str(prices).replace("'", '"') if closed else '["0.5","0.5"]',
        "events": [{"slug": event_slug or slug, "title": title}],
    }


def fill(
    condition_id: str,
    timestamp: int,
    outcome: str,
    side: str,
    price: float,
    size: float,
    *,
    tx: str = "0xtx",
    slug: str = "slug",
    title: str = "title",
    event_slug: str = "event",
) -> Dict[str, Any]:
    return {
        "type": "TRADE",
        "proxyWallet": "0xabc",
        "timestamp": timestamp,
        "conditionId": condition_id,
        "asset": f"{condition_id}-{outcome}",
        "outcome": outcome,
        "outcomeIndex": 0 if outcome == "Up" else 1,
        "side": side,
        "price": price,
        "size": size,
        "usdcSize": round(price * size, 6),
        "transactionHash": tx,
        "slug": slug,
        "title": title,
        "eventSlug": event_slug,
    }


def build_cache(root, activity_pages: List[List[Dict[str, Any]]], markets: List[Dict[str, Any]]) -> RawCache:
    cache = RawCache(root)
    for page in activity_pages:
        cache.append("activity", url="https://data-api.polymarket.com/activity", params={}, status=200, payload=page)
    cache.append("markets", url="https://gamma-api.polymarket.com/markets", params={}, status=200, payload=markets)
    return cache


def standard_scenario(root) -> RawCache:
    """Two in-scope windows (BTC 5m, ETH 15m) plus two out-of-scope markets."""
    btc = "0xbtc5m"
    eth = "0xeth15m"
    hourly = "0xbtc1h"
    sol = "0xsol5m"

    markets = [
        market(btc, "bitcoin-up-or-down-5m-a", "Bitcoin Up or Down 5 minute", WINDOW_START, 300, winner="Up"),
        market(eth, "ethereum-up-or-down-15m-a", "Ethereum Up or Down 15 minute", WINDOW_START + 600, 900, winner="Down"),
        market(hourly, "bitcoin-up-or-down-hourly", "Bitcoin Up or Down hourly", WINDOW_START, 3600, winner="Up"),
        market(sol, "solana-up-or-down-5m", "Solana Up or Down 5 minute", WINDOW_START, 300, winner="Up"),
    ]

    btc_fills = [
        fill(btc, WINDOW_START + 30, "Up", "BUY", 0.45, 100, tx="0xa1", slug="bitcoin-up-or-down-5m-a"),
        fill(btc, WINDOW_START + 90, "Down", "BUY", 0.50, 100, tx="0xa2", slug="bitcoin-up-or-down-5m-a"),
        fill(btc, WINDOW_START + 200, "Up", "BUY", 0.82, 50, tx="0xa3", slug="bitcoin-up-or-down-5m-a"),
    ]
    eth_fills = [
        fill(eth, WINDOW_START + 660, "Down", "BUY", 0.40, 200, tx="0xb1", slug="ethereum-up-or-down-15m-a"),
        fill(eth, WINDOW_START + 900, "Up", "BUY", 0.55, 200, tx="0xb2", slug="ethereum-up-or-down-15m-a"),
    ]
    other_fills = [
        fill(hourly, WINDOW_START + 100, "Up", "BUY", 0.60, 10, tx="0xc1", slug="bitcoin-up-or-down-hourly"),
        fill(sol, WINDOW_START + 100, "Up", "BUY", 0.60, 10, tx="0xd1", slug="solana-up-or-down-5m"),
    ]
    return build_cache(root, [btc_fills + eth_fills + other_fills], markets)
