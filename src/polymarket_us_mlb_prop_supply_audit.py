"""Read-only Polymarket US MLB player-prop supply / parser-drift diagnostic.

This module deliberately does *not* produce executable candidates. It answers a narrower
question needed by the MLB fast lane: when strict counterpart discovery returns zero,
did Polymarket US actually publish no player props, or did the public payload shape move
outside the strict parser's currently-known fields/types?

The audit recursively inspects public search/event JSON, inventories market-like objects,
and flags records whose text looks like an MLB player prop even when their type is unknown.
Unknown/ambiguous records remain diagnostic only.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from typing import Any, Iterable

import httpx

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


def audit_payload(payload: Any, *, query: str = "") -> dict[str, Any]:
    market_rows = [row for row in _walk(payload) if _market_like(row)]
    type_counts: Counter[str] = Counter()
    prop_like: list[dict[str, Any]] = []

    for row in market_rows:
        market_type = _first_text(row, "market_type", "type", "category", "template", "kind") or "<missing>"
        type_counts[market_type] += 1
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
            prop_like.append(
                {
                    "id": row.get("id") or row.get("market_id") or row.get("instrument_id"),
                    "slug": row.get("slug"),
                    "market_type": market_type,
                    "text": text[:500],
                    "status": "PROP_LIKE_DIAGNOSTIC_ONLY",
                }
            )

    return {
        "query": query,
        "market_like_rows": len(market_rows),
        "market_type_counts": dict(type_counts.most_common()),
        "prop_like_rows": len(prop_like),
        "prop_like_samples": prop_like[:20],
        "interpretation": (
            "PARSER_OR_SCHEMA_DRIFT_POSSIBLE"
            if prop_like
            else "NO_PLAYER_PROP_SHAPE_OBSERVED_IN_THIS_PAYLOAD"
        ),
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
