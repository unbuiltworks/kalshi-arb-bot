"""Read-only Polymarket US MLB player-prop supply / parser-drift diagnostic.

This module deliberately does *not* produce executable candidates. It answers a narrower
question needed by the MLB fast lane: when strict counterpart discovery returns zero,
did Polymarket US actually publish no player props, or did the public payload shape move
outside the strict parser's currently-known fields/types/container layout?

The audit recursively inspects public search/event JSON, inventories market-like objects,
flags records whose text looks like an MLB player prop even when their type is unknown,
and explicitly reports whether each prop-like row is compatible with the strict discovery
contract used by ``strict_mlb_prop_snapshot_v2``. Unknown/ambiguous records remain
diagnostic only and can never become executable candidates here.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from typing import Any, Iterable

import httpx

from src.strict_mlb_prop_adapters import FAMILY_SERIES

SEARCH_URL = "https://gateway.polymarket.us/v1/search"

_PROP_TERMS = re.compile(
    r"\b(hit|hits|total bases|strikeout|strikeouts|home run|home runs|runs \+ rbis|hits \+ runs \+ rbis|h\+r\+rbi|rbi|rbis)\b",
    re.IGNORECASE,
)
_THRESHOLD_TERMS = re.compile(r"\b(?:at least\s+)?\d+(?:\.5)?\+?\b", re.IGNORECASE)


def _walk_with_path(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[dict[str, Any], tuple[str, ...]]]:
    if isinstance(value, dict):
        yield value, path
        for key, child in value.items():
            yield from _walk_with_path(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_with_path(child, path + (f"[{index}]",))


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _market_like(row: dict[str, Any]) -> bool:
    identity_keys = {"id", "market_id", "instrument_id", "slug", "ticker"}
    text_keys = {"question", "title", "name", "subtitle", "description"}
    return bool(identity_keys.intersection(row)) and bool(text_keys.intersection(row))


def _direct_event_market(path: tuple[str, ...]) -> bool:
    """Return True only for the exact Search container consumed by v2 discovery.

    ``strict_mlb_prop_snapshot_v2._search_one_game`` reads only ``body.get('events')``
    and then ``event.get('markets')``.  A recursive diagnostic that merely finds a key
    named ``markets`` somewhere else (for example ``data.events[].markets[]``) is useful
    evidence that Polymarket published a prop-shaped object, but it is *not* reachable by
    the strict scanner.  Treat only the exact top-level ``events[].markets[]`` path as
    discovery-compatible so the audit cannot send us debugging identity when the real
    problem is a response-container drift.
    """
    return (
        len(path) == 4
        and path[0] == "events"
        and path[1].startswith("[")
        and path[2] == "markets"
        and path[3].startswith("[")
    )


def _strict_parser_diagnostic(
    row: dict[str, Any], *, direct_event_market: bool
) -> tuple[bool, list[str]]:
    """Explain whether v2's strict discovery would retain this row.

    This mirrors the discovery contract only: v2 iterates top-level
    ``body['events'][*]['markets']`` and requires a recognized ``sportsMarketType`` plus
    a slug. Full event/player/threshold/participation/rules checks still happen later.
    """
    blockers: list[str] = []
    if not direct_event_market:
        blockers.append("OUTSIDE_EVENT_MARKETS_CONTAINER")

    family = row.get("sportsMarketType")
    if not isinstance(family, str) or not family.strip():
        blockers.append("MISSING_SPORTS_MARKET_TYPE")
    elif family not in FAMILY_SERIES:
        blockers.append("UNRECOGNIZED_SPORTS_MARKET_TYPE")

    slug = row.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        blockers.append("MISSING_MARKET_SLUG")

    return not blockers, blockers


def audit_payload(payload: Any, *, query: str = "") -> dict[str, Any]:
    market_rows = [
        (row, path)
        for row, path in _walk_with_path(payload)
        if _market_like(row)
    ]
    type_counts: Counter[str] = Counter()
    sports_type_counts: Counter[str] = Counter()
    container_counts: Counter[str] = Counter()
    prop_like: list[dict[str, Any]] = []
    strict_compatible = 0
    parser_drift = 0
    prop_like_in_event_markets = 0
    prop_like_outside_event_markets = 0
    drift_reason_counts: Counter[str] = Counter()

    for row, path in market_rows:
        market_type = _first_text(row, "market_type", "type", "category", "template", "kind") or "<missing>"
        type_counts[market_type] += 1
        sports_type = _first_text(row, "sportsMarketType") or "<missing>"
        sports_type_counts[sports_type] += 1
        direct_event_market = _direct_event_market(path)
        container_counts["event.markets" if direct_event_market else "other"] += 1
        text = " | ".join(
            part
            for part in (
                _first_text(row, "question"),
                _first_text(row, "title", "name"),
                _first_text(row, "subtitle"),
                _first_text(row, "description"),
            )
            if part
        )
        if _PROP_TERMS.search(text) and _THRESHOLD_TERMS.search(text):
            if direct_event_market:
                prop_like_in_event_markets += 1
            else:
                prop_like_outside_event_markets += 1
            compatible, blockers = _strict_parser_diagnostic(
                row, direct_event_market=direct_event_market
            )
            if compatible:
                strict_compatible += 1
            else:
                parser_drift += 1
                drift_reason_counts.update(blockers)
            prop_like.append(
                {
                    "id": row.get("id") or row.get("market_id") or row.get("instrument_id"),
                    "slug": row.get("slug"),
                    "market_type": market_type,
                    "sports_market_type": None if sports_type == "<missing>" else sports_type,
                    "container_path": ".".join(path),
                    "direct_event_market": direct_event_market,
                    "strict_discovery_compatible": compatible,
                    "strict_discovery_blockers": blockers,
                    "schema_keys": sorted(str(key) for key in row.keys())[:80],
                    "text": text[:500],
                    "status": (
                        "STRICT_DISCOVERY_SHAPE_PRESENT_DIAGNOSTIC_ONLY"
                        if compatible
                        else "PROP_LIKE_PARSER_DRIFT_DIAGNOSTIC_ONLY"
                    ),
                }
            )

    if strict_compatible:
        interpretation = "STRICT_PARSER_COMPATIBLE_PROP_SUPPLY_OBSERVED"
    elif parser_drift:
        interpretation = "PARSER_OR_SCHEMA_DRIFT_CONFIRMED_IN_PAYLOAD"
    else:
        interpretation = "NO_PLAYER_PROP_SHAPE_OBSERVED_IN_THIS_PAYLOAD"

    return {
        "query": query,
        "market_like_rows": len(market_rows),
        "market_container_counts": dict(container_counts.most_common()),
        "market_type_counts": dict(type_counts.most_common()),
        "sports_market_type_counts": dict(sports_type_counts.most_common()),
        "prop_like_rows": len(prop_like),
        "prop_like_rows_in_event_markets": prop_like_in_event_markets,
        "prop_like_rows_outside_event_markets": prop_like_outside_event_markets,
        "strict_parser_compatible_prop_rows": strict_compatible,
        "parser_drift_prop_rows": parser_drift,
        "parser_drift_reason_counts": dict(drift_reason_counts.most_common()),
        "prop_like_samples": prop_like[:20],
        "interpretation": interpretation,
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
    }


def fetch_search(query: str, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(
            SEARCH_URL,
            params={"query": query, "limit": 100, "status": "active", "page": 1},
        )
        response.raise_for_status()
        return audit_payload(response.json(), query=query)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    queries = args or ["Gerrit Cole", "CJ Abrams", "Gunnar Henderson"]
    reports = [fetch_search(query) for query in queries]
    print(
        json.dumps(
            {
                "audit": "POLYMARKET_US_MLB_PROP_SUPPLY",
                "reports": reports,
                "read_only": True,
                "order_entry": False,
                "execution_eligible": False,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
