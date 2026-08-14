"""Read-only Polymarket US credential and sports-data probe.

Never submits, modifies, or cancels orders. Authenticated API is used only for
balances; sports market discovery uses the public gateway.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import httpx
from polymarket_us import PolymarketUS

GATEWAY = "https://gateway.polymarket.us"


def client_from_env() -> PolymarketUS:
    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret_key = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret_key:
        raise SystemExit("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required")
    return PolymarketUS(key_id=key_id, secret_key=secret_key, timeout=10.0)


def _compact_market(market: dict) -> dict:
    return {
        "id": market.get("id"),
        "slug": market.get("slug"),
        "question": market.get("question"),
        "title": market.get("title"),
        "subtitle": market.get("subtitle"),
        "line": market.get("line"),
        "sportsMarketType": market.get("sportsMarketType"),
        "marketType": market.get("marketType"),
        "bestBidQuote": market.get("bestBidQuote"),
        "bestAskQuote": market.get("bestAskQuote"),
        "marketSides": [
            {
                "identifier": side.get("identifier"),
                "description": side.get("description"),
                "long": side.get("long"),
                "price": side.get("price"),
                "quote": side.get("quote"),
                "tradable": side.get("tradable"),
            }
            for side in (market.get("marketSides", []) or [])
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Polymarket US API probe")
    parser.add_argument("--league", action="append", default=[], help="League slug, e.g. nba or mlb")
    args = parser.parse_args()

    client = client_from_env()
    try:
        balances = client.account.balances()
        print(json.dumps({"authenticated": True, "balances": balances}, default=str))
    finally:
        client.close()

    leagues = args.league or ["nba", "mlb"]
    with httpx.Client(timeout=10.0) as http:
        for league in leagues:
            response = http.get(f"{GATEWAY}/v2/leagues/{league}/events", params={"limit": 100})
            response.raise_for_status()
            payload = response.json()
            events = payload.get("events", []) if isinstance(payload, dict) else []
            market_types = Counter()
            samples = []
            total_like = []
            for event in events:
                for market in event.get("markets", []) or []:
                    typ = str(market.get("sportsMarketType") or "<none>")
                    market_types[typ] += 1
                    compact = _compact_market(market)
                    if len(samples) < 12:
                        samples.append({"event": event.get("title"), **compact})
                    joined = " ".join(str(market.get(k) or "") for k in ("sportsMarketType", "marketType", "question", "title", "subtitle")).lower()
                    if "total" in joined or "over" in joined or "under" in joined:
                        total_like.append({"event": event.get("title"), **compact})
            print(json.dumps({
                "league": league,
                "event_count": len(events),
                "market_type_counts": dict(market_types),
                "sample_markets": samples,
                "total_like_count": len(total_like),
                "total_like": total_like[:30],
            }, default=str))


if __name__ == "__main__":
    main()
