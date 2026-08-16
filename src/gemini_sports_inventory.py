"""Public, read-only Gemini prediction-market sports inventory.

Uses Gemini's documented prediction-markets events endpoint and deterministic
sports ticker taxonomy. This module is discovery only: it does not authenticate,
subscribe to private streams, or expose any order-entry path.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
import logging
import re
from typing import Any

import httpx

GEMINI = "https://api.gemini.com"
logger = logging.getLogger("kalshi_arb.gemini_sports_inventory")

TEAM_SPORT_EVENT_RE = re.compile(
    r"^GEMI-(?P<league>[A-Z]{2,14})-(?P<date>\d{6})(?P<time>\d{4})-"
    r"(?P<away>[A-Z]{2,4})-(?P<home>[A-Z]{2,4})-(?P<market_type>[A-Z0-9]{1,8})$"
)

MLB_PROP_TYPES = {
    "PPSO": "baseball_player_strikeouts",
    "PPHITS": "baseball_player_hits",
    "PPHR": "baseball_player_home_runs",
    "PPRBI": "baseball_player_rbis",
    "PPTB": "baseball_player_total_bases",
    "PPRUNS": "baseball_player_runs",
}

NFL_PROP_TYPES = {
    "PPTD": "football_player_touchdowns",
    "PPYDS": "football_player_passing_yards",
    "PPRYDS": "football_player_rushing_yards",
    "PPRECY": "football_player_receiving_yards",
    "PPREC": "football_player_receptions",
    "PPCOMP": "football_player_completions",
}


def parse_team_sport_event_ticker(ticker: str) -> dict[str, str] | None:
    match = TEAM_SPORT_EVENT_RE.fullmatch(str(ticker or "").upper())
    return match.groupdict() if match else None


async def fetch_active_sports_events(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    limit = 500
    offset = 0
    while True:
        response = await http.get(
            f"{GEMINI}/v1/prediction-markets/events",
            params=[
                ("status", "active"),
                ("category", "Sports"),
                ("limit", str(limit)),
                ("offset", str(offset)),
            ],
        )
        response.raise_for_status()
        body = response.json()
        rows = list(body.get("data") or body.get("events") or [])
        out.extend(rows)
        pagination = body.get("pagination") or {}
        total = pagination.get("total")
        if len(rows) < limit:
            break
        offset += limit
        if isinstance(total, int) and offset >= total:
            break
        if offset >= 20000:
            logger.warning("Stopping Gemini sports pagination at defensive 20k ceiling")
            break
    return out


def _event_example(event: dict[str, Any], *, market_type: str, known_family: str | None) -> dict[str, Any]:
    return {
        "event_ticker": str(event.get("ticker") or ""),
        "title": str(event.get("title") or ""),
        "market_type": market_type,
        "known_family": known_family,
        "effectiveDate": event.get("effectiveDate"),
        "expiryDate": event.get("expiryDate"),
        "contracts": [
            {
                "label": contract.get("label"),
                "ticker": contract.get("ticker"),
                "instrumentSymbol": contract.get("instrumentSymbol"),
                "marketState": contract.get("marketState"),
            }
            for contract in (event.get("contracts") or [])[:10]
        ],
    }


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    league_counts: Counter[str] = Counter()
    market_type_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    family_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    live_pp_type_counts: Counter[str] = Counter()
    live_pp_type_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unknown_pp_type_counts: Counter[str] = Counter()
    mlb_events = 0
    mlb_contracts = 0

    for event in events:
        ticker = str(event.get("ticker") or "")
        parsed = parse_team_sport_event_ticker(ticker)
        if parsed is None:
            continue
        league = parsed["league"]
        market_type = parsed["market_type"]
        league_counts[league] += 1
        market_type_counts[f"{league}:{market_type}"] += 1

        family = None
        known_mapping: dict[str, str] = {}
        if league == "MLB":
            mlb_events += 1
            known_mapping = MLB_PROP_TYPES
            family = known_mapping.get(market_type)
            mlb_contracts += len(event.get("contracts") or [])
        elif league == "NFL":
            known_mapping = NFL_PROP_TYPES
            family = known_mapping.get(market_type)

        # PP* is Gemini's deterministic player-prop namespace. Preserve every
        # live code, including undocumented/app-only codes, until title/contract
        # evidence proves an explicit semantic mapping. Unknown codes must never
        # be silently discarded or inferred from ticker identity alone.
        if league in {"MLB", "NFL"} and market_type.startswith("PP"):
            key = f"{league}:{market_type}"
            live_pp_type_counts[key] += 1
            if family is None:
                unknown_pp_type_counts[key] += 1
            if len(live_pp_type_examples[key]) < 5:
                live_pp_type_examples[key].append(
                    _event_example(event, market_type=market_type, known_family=family)
                )

        if family:
            family_counts[family] += 1
            if len(family_examples[family]) < 5:
                family_examples[family].append(
                    _event_example(event, market_type=market_type, known_family=family)
                )

    return {
        "events": len(events),
        "league_event_counts": dict(league_counts.most_common()),
        "market_type_counts": dict(market_type_counts.most_common()),
        "supported_prop_family_counts": dict(family_counts.most_common()),
        "supported_prop_family_examples": dict(sorted(family_examples.items())),
        "live_pp_market_type_counts": dict(live_pp_type_counts.most_common()),
        "live_pp_market_type_examples": dict(sorted(live_pp_type_examples.items())),
        "unknown_pp_market_type_counts": dict(unknown_pp_type_counts.most_common()),
        "unknown_pp_codes_require_identity_audit": sorted(unknown_pp_type_counts),
        "mlb_events": mlb_events,
        "mlb_contracts": mlb_contracts,
        "classification": "INVENTORY_ONLY_UNKNOWN_CODES_PRESERVED",
        "read_only": True,
        "order_entry": False,
    }


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await fetch_active_sports_events(http)
    result = summarize(events)
    logger.info("GEMINI_SPORTS_INVENTORY %s", json.dumps(result, separators=(",", ":")))
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
