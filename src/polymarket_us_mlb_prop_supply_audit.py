"""Read-only Polymarket US MLB player-prop supply / parser-drift diagnostic.

This module deliberately does *not* produce executable candidates. It answers a narrower
question needed by the MLB fast lane: when strict counterpart discovery returns zero,
did Polymarket US actually publish no player props, or did the public payload shape move
outside the strict parser's currently-known fields/types?

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


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


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


def _strict_parser_diagnostic(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """Explain whether v2's strict discovery would retain this row.

    This is intentionally narrower than full counterpart eligibility. It mirrors the
    discovery contract only: v2 iterates ``event['markets']`` and requires a recognized
    ``sportsMarketType``. Full event/player/threshold/rules checks still happen later.
    """
    blockers: list[str] = []
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
    market_rows = [row for row in _walk(payload) if _market_like(row)]
    type_counts: Counter[str] = Counter()
    sports_type_counts: Counter[str] = Counter()
    prop_like: list[dict[str, Any]] = []
    strict_compatible = 0
    parser_drift = 0
    drift_reason_counts: Counter[str] = Counter()

    for row in market_rows:
        market_type = _first_text(row, "market_type", "type", "category", "template", "kind") or "<missing>"
        type_counts[market_type] += 1
        sports_type = _first_text(row, "sportsMarketType") or "<missing>"
        sports_type_counts[sports_type] += 1
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
            compatible, blockers = _strict_parser_diagnostic(row)
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
        "market_type_counts": dict(type_counts.most_common()),
        "sports_market_type_counts": dict(sports_type_counts.most_common()),
        "prop_like_rows": len(prop_like),
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
