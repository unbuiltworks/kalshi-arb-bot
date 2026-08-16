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
    """Use official /v1/search and include player-name probes from the live app."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    # MLB/baseball find the general slate. The named-player queries are deliberate
    # diagnostics based on currently visible app props and help expose the player-
    # prop event shape even when broad search ranking omits those events.
    for query in ("MLB", "baseball", "Roman Anthony", "Rafael Devers", "Seth Lugo"):
        for page in range(1, 6):
            body = await _fetch_json(
                http,
                f"{POLY}/v1/search",
                params={"query": query, "limit": 100, "status": "active", "page": page},
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


def _looks_like_player_prop(market: dict) -> bool:
    legacy = str(market.get("sportsMarketType") or "")
    slug = str(market.get("slug") or "").lower()
    if legacy in FAMILY_SERIES or legacy.startswith("baseball_player_"):
        return True
    # Historical player props use astatc; retain this as a strong structural signal.
    if slug.startswith("astatc-mlb-"):
        return True
    # Current app player prop slugs may change prefix, but still usually encode MLB
    # plus a player/stat threshold. Avoid treating generic game/inning PROP markets
    # as player props solely because sportsMarketTypeV2 says PROP.
    text = " ".join(str(market.get(k) or "") for k in ("slug", "title", "question")).lower()
    has_player_stat = any(token in text for token in (
        " total bases", " strikeout", " pitching strikeout", " hits + runs + rbi",
        " hits+runs+rbis", " home run", " hits",
    ))
    has_threshold = market.get("line") not in (None, "") or any(t in slug for t in ("gte", "pt", "plus"))
    return "mlb" in slug and has_player_stat and has_threshold


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
    mlb_direct = [m for m in direct_props if _looks_like_player_prop(m)]
    supported_direct = [m for m in mlb_direct if str(m.get("sportsMarketType") or "") in FAMILY_SERIES]

    player_search_markets: list[dict[str, Any]] = []
    player_search_examples: list[dict[str, Any]] = []
    search_types = Counter()
    search_v2_types = Counter()
    for event in search_events:
        event_slug = str(event.get("slug") or "")
        event_title = str(event.get("title") or "")
        event_start = event.get("startTime")
        for raw in event.get("markets") or []:
            market = dict(raw)
            search_types[str(market.get("sportsMarketType") or "<missing>")] += 1
            search_v2_types[str(market.get("sportsMarketTypeV2") or "<missing>")] += 1
            if not _looks_like_player_prop(market):
                continue
            if not market.get("gameStartTime") and event_start:
                market["eventStartTime"] = event_start
            market["_event_slug"] = event_slug
            market["_event_title"] = event_title
            market["_event_startTime"] = event_start
            player_search_markets.append(market)
            if len(player_search_examples) < 30:
                row = _sample(market)
                row["event_slug"] = event_slug
                row["event_title"] = event_title
                row["event_startTime"] = event_start
                player_search_examples.append(row)

    result = {
        "league_events": len(events),
        "league_nested_markets": nested_total,
        "league_sports_market_types": dict(nested_types.most_common()),
        "direct_open_sports_props": len(direct_props),
        "direct_prop_legacy_types": dict(direct_type_counts.most_common()),
        "direct_mlb_player_props": len(mlb_direct),
        "direct_supported_mlb_player_props": len(supported_direct),
        "direct_mlb_player_examples": [_sample(m) for m in mlb_direct[:20]],
        "search_events": len(search_events),
        "search_player_prop_markets": len(player_search_markets),
        "search_legacy_types": dict(search_types.most_common()),
        "search_v2_types": dict(search_v2_types.most_common()),
        "search_player_prop_examples": player_search_examples,
        "league_examples": nested_examples,
        "diagnosis": (
            "SEARCH_PLAYER_PROPS_AVAILABLE" if player_search_markets
            else "DIRECT_PLAYER_PROPS_AVAILABLE" if mlb_direct
            else "NO_PLAYER_PROPS_FOUND_ON_TESTED_API_SURFACES"
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
