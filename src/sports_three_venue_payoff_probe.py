"""Read-only exact-game schema probe for the three-venue sports payoff solver.

Targets the two Aug 22 MLB games used in the complete-partition example and logs
only compact public contract/price fields from Gemini and Polymarket. No auth,
orders, RFQs, balances, or market creation.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from src.gemini_sports_inventory import fetch_active_sports_events, parse_team_sport_event_ticker

GEMINI = "https://api.gemini.com"
POLY = "https://gateway.polymarket.us"
TARGETS = {frozenset(("ATL", "MIL")), frozenset(("WSH", "MIA"))}


def _compact_contract(c: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "label", "ticker", "instrumentSymbol", "marketState", "price", "bestBid",
        "bestAsk", "bid", "ask", "yesBid", "yesAsk", "noBid", "noAsk",
    )
    return {k: c.get(k) for k in keep if c.get(k) is not None}


def _compact_gemini(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in events:
        parsed = parse_team_sport_event_ticker(str(e.get("ticker") or ""))
        if not parsed or parsed.get("league") != "MLB":
            continue
        pair = frozenset((parsed.get("away", ""), parsed.get("home", "")))
        if pair not in TARGETS:
            continue
        out.append({
            "ticker": e.get("ticker"),
            "title": e.get("title"),
            "away": parsed.get("away"),
            "home": parsed.get("home"),
            "market_type": parsed.get("market_type"),
            "event_public_price_fields": {
                k: e.get(k) for k in ("price", "bestBid", "bestAsk", "bid", "ask") if e.get(k) is not None
            },
            "contracts": [_compact_contract(dict(c)) for c in (e.get("contracts") or []) if isinstance(c, dict)],
        })
    return out


async def _poly_game(http: httpx.AsyncClient, slug: str) -> dict[str, Any] | None:
    r = await http.get(f"{POLY}/v1/events", params={"slug": slug, "limit": 20})
    r.raise_for_status()
    body = r.json()
    rows = body.get("events") or [] if isinstance(body, dict) else []
    for e in rows:
        if str(e.get("slug") or "") != slug:
            continue
        markets = []
        for m in e.get("markets") or []:
            if str(m.get("sportsMarketTypeV2") or "") != "SPORTS_MARKET_TYPE_MONEYLINE":
                continue
            markets.append({
                "id": m.get("id"), "slug": m.get("slug"), "title": m.get("title"),
                "bestBid": m.get("bestBid"), "bestAsk": m.get("bestAsk"),
                "marketSides": [
                    {k: s.get(k) for k in ("id", "description", "long", "price", "quote", "tradable") if s.get(k) is not None}
                    for s in (m.get("marketSides") or []) if isinstance(s, dict)
                ],
            })
        return {"slug": slug, "title": e.get("title"), "markets": markets}
    return None


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini = await fetch_active_sports_events(http)
        poly = await asyncio.gather(
            _poly_game(http, "mlb-atl-mil-2026-08-22"),
            _poly_game(http, "mlb-wsh-mia-2026-08-22"),
        )
    result = {
        "status": "THREE_VENUE_PAYOFF_SCHEMA_PROBE",
        "gemini_targets": _compact_gemini(gemini),
        "polymarket_targets": [x for x in poly if x],
        "payoff_states": ["ATL+WSH", "ATL+MIA", "MIL+WSH", "MIL+MIA"],
        "read_only": True,
        "order_entry": False,
        "rfq": False,
        "market_creation": False,
    }
    print("THREE_VENUE_PAYOFF_SCHEMA_PROBE " + json.dumps(result, separators=(",", ":"), default=str), flush=True)
    return result


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
