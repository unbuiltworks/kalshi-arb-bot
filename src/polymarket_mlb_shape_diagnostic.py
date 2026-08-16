"""Read-only shape diagnostic for current Polymarket US MLB market surfaces."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
from typing import Any

import httpx

from src.strict_mlb_prop_adapters import FAMILY_SERIES
from src.strict_mlb_prop_snapshot import POLY, _fetch_json


async def _fetch_all_props(http: httpx.AsyncClient) -> list[dict]:
    """Read the documented direct market surface, paging defensively."""
    rows: list[dict] = []
    limit = 50
    for offset in range(0, 5000, limit):
        body = await _fetch_json(
            http,
            f"{POLY}/v1/markets",
            params={
                "limit": limit,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "categories": "sports",
                "sportsMarketTypes": "SPORTS_MARKET_TYPE_PROP",
            },
        )
        page = list(body.get("markets") or [])
        rows.extend(page)
        if len(page) < limit:
            break
    return rows


async def _fetch_search_events(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Use Polymarket US's official Search resource (/v1/search).

    The app currently exposes MLB player props that are absent from both the league
    nested-market response and the filtered /v1/markets response.  Search is an
    official SDK surface returning events with nested markets, so inspect it directly.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in ("MLB", "baseball"):
        for page in range(1, 6):
            body = await _fetch_json(
                http,
                f"{POLY}/v1/search",
                params={
                    "query": query,
                    "limit": 100,
                    "status": "active",
                    "page": page,
                },
            )
            events = list(body.get("events") or [])
            for event in events:
                key = str(event.get("id") or event.get("slug") or "")
                if key and key not in seen:
                    seen.add(key)
                    out.append(dict(event))
            if len(events) < 100:
                break
    return out


def _sample(market: dict) -> dict:
    subject = market.get("subject") if isinstance(market.get("subject"), dict) else {}
    return {
        "id": market.get("id"),
        "sportsMarketType": market.get("sportsMarketType"),
        "sportsMarketTypeV2": market.get("sportsMarketTypeV2"),
        "slug": market.get("slug"),
        "question": market.get("question") or market.get("title"),
        "title": market.get("title"),
        "line": market.get("line"),
        "gameId": market.get("gameId"),
        "gameStartTime": market.get("gameStartTime"),
        "startTime": market.get("startTime"),
        "subject": subject,
        "player": market.get("player") or market.get("playerName") or subject.get("name") or subject.get("player"),
        "outcomes": market.get("outcomes"),
        "marketSides": market.get("marketSides"),
        "keys": sorted(str(k) for k in market.keys()),
    }


def _looks_like_mlb_prop(market: dict) -> bool:
    legacy = str(market.get("sportsMarketType") or "")
    v2 = str(market.get("sportsMarketTypeV2") or "").upper()
    if legacy in FAMILY_SERIES:
        return True
    if v2 in {"PROP", "SPORTS_MARKET_TYPE_PROP"}:
        return True
    text = " ".join(
        str(market.get(k) or "")
        for k in ("slug", "title", "question")
    ).lower()
    return any(token in text for token in (" total bases", " strikeout", " hits", " runs + rbis", "home run"))


async def run_once() -> dict:
    async with httpx.AsyncClient(timeout=30.0) as http:
        league_body, direct_props, search_events = await asyncio.gather(
            _fetch_json(
                http,
                f"{POLY}/v2/leagues/mlb/events",
                params={"limit": 1000, "active": "true", "closed": "false"},
            ),
            _fetch_all_props(http),
            _fetch_search_events(http),
        )

    events = list(league_body.get("events") or [])
    nested_types = Counter()
    nested_examples = []
    nested_total = 0
    for event in events:
        for market in event.get("markets") or []:
            nested_total += 1
            nested_types[str(market.get("sportsMarketType") or "<missing>")] += 1
            if len(nested_examples) < 12:
                row = _sample(market)
                row["event_startTime"] = event.get("startTime")
                row["event_slug"] = event.get("slug")
                nested_examples.append(row)

    direct_type_counts = Counter(str(m.get("sportsMarketType") or "<missing>") for m in direct_props)
    mlb_direct = [
        m for m in direct_props
        if str(m.get("slug") or "").lower().startswith("astatc-mlb-")
        or " mlb " in (" " + str(m.get("question") or m.get("title") or "").lower() + " ")
    ]
    supported_direct = [m for m in mlb_direct if str(m.get("sportsMarketType") or "") in FAMILY_SERIES]

    search_markets: list[dict[str, Any]] = []
    search_examples: list[dict[str, Any]] = []
    search_types = Counter()
    search_v2_types = Counter()
    for event in search_events:
        event_slug = str(event.get("slug") or "")
        event_title = str(event.get("title") or "")
        event_start = event.get("startTime")
        # Keep only plausible baseball/MLB events before inspecting nested props.
        event_text = f"{event_slug} {event_title}".lower()
        if "mlb" not in event_text and "baseball" not in event_text:
            continue
        for raw in event.get("markets") or []:
            market = dict(raw)
            search_types[str(market.get("sportsMarketType") or "<missing>")] += 1
            search_v2_types[str(market.get("sportsMarketTypeV2") or "<missing>")] += 1
            if not _looks_like_mlb_prop(market):
                continue
            if not market.get("gameStartTime") and event_start:
                market["eventStartTime"] = event_start
            market["_event_slug"] = event_slug
            market["_event_title"] = event_title
            market["_event_startTime"] = event_start
            search_markets.append(market)
            if len(search_examples) < 20:
                row = _sample(market)
                row["event_slug"] = event_slug
                row["event_title"] = event_title
                row["event_startTime"] = event_start
                search_examples.append(row)

    result = {
        "league_events": len(events),
        "league_nested_markets": nested_total,
        "league_sports_market_types": dict(nested_types.most_common()),
        "direct_open_sports_props": len(direct_props),
        "direct_prop_legacy_types": dict(direct_type_counts.most_common()),
        "direct_mlb_props": len(mlb_direct),
        "direct_supported_mlb_props": len(supported_direct),
        "direct_supported_by_family": dict(Counter(str(m.get("sportsMarketType")) for m in supported_direct)),
        "direct_mlb_examples": [_sample(m) for m in mlb_direct[:20]],
        "search_events": len(search_events),
        "search_mlb_prop_markets": len(search_markets),
        "search_legacy_types": dict(search_types.most_common()),
        "search_v2_types": dict(search_v2_types.most_common()),
        "search_mlb_prop_examples": search_examples,
        "league_examples": nested_examples,
        "diagnosis": (
            "SEARCH_MLB_PROPS_AVAILABLE"
            if search_markets
            else "DIRECT_MLB_PROPS_AVAILABLE_NOT_NESTED"
            if supported_direct and not any(t in FAMILY_SERIES for t in nested_types)
            else "DIRECT_AND_NESTED_SUPPORTED_PROPS_AVAILABLE"
            if supported_direct
            else "NO_SUPPORTED_MLB_PROPS_FOUND"
        ),
        "read_only": True,
        "order_entry": False,
    }
    print("POLY_MLB_SHAPE " + json.dumps(result, separators=(",", ":")))
    return result


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
