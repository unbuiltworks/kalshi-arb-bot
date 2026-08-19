"""Read-only inventory of collateral-return metadata on open Kalshi events.

No nested markets are requested, so this can page broadly without pulling order
books. It exists only to discover exchange-provided collateral group types rather
than guessing enum values from market names.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
from typing import Any, Mapping

import httpx

from src.universal_three_venue_sports import KALSHI


def _retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        raw = Decimal(str(response.headers.get("retry-after") or "0"))
        if raw > 0:
            return float(raw)
    except Exception:
        pass
    return min(8.0, 0.4 * (2 ** attempt))


async def _get(http: httpx.AsyncClient, params: dict[str, Any]) -> dict[str, Any]:
    last: httpx.Response | None = None
    for attempt in range(7):
        response = await http.get(f"{KALSHI}/events", params=params)
        last = response
        if response.status_code == 429:
            await asyncio.sleep(max(0.25, _retry_after(response, attempt)))
            continue
        if 500 <= response.status_code < 600 and attempt < 6:
            await asyncio.sleep(min(4.0, 0.25 * (2 ** attempt)))
            continue
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, Mapping) else {}
    assert last is not None
    last.raise_for_status()
    return {}


async def probe() -> dict[str, Any]:
    type_counts: Counter[str] = Counter()
    combo_counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {}
    cursor = ""
    pages = 0
    seen = 0
    cursor_exhausted = False
    async with httpx.AsyncClient(timeout=30.0) as http:
        for _ in range(150):
            params: dict[str, Any] = {
                "limit": 200,
                "status": "open",
                "with_nested_markets": "false",
            }
            if cursor:
                params["cursor"] = cursor
            body = await _get(http, params)
            rows = [dict(row) for row in body.get("events") or [] if isinstance(row, Mapping)]
            pages += 1
            seen += len(rows)
            for event in rows:
                typ = str(event.get("collateral_return_type") or "NONE").strip().upper() or "NONE"
                mutual = bool(event.get("mutually_exclusive"))
                type_counts[typ] += 1
                combo_counts[f"{typ}|mutual={mutual}"] += 1
                bucket = examples.setdefault(typ, [])
                if len(bucket) < 8:
                    bucket.append({
                        "event_ticker": str(event.get("event_ticker") or ""),
                        "series_ticker": str(event.get("series_ticker") or ""),
                        "category": str(event.get("category") or ""),
                        "title": str(event.get("title") or "")[:180],
                        "mutually_exclusive": mutual,
                    })
            cursor = str(body.get("cursor") or "")
            if not cursor:
                cursor_exhausted = True
                break
            await asyncio.sleep(0.08)

    return {
        "mode": "KALSHI_COLLATERAL_TYPE_INVENTORY",
        "open_events_seen": seen,
        "pages": pages,
        "cursor_exhausted": cursor_exhausted,
        "collateral_return_type_counts": dict(type_counts.most_common()),
        "type_mutual_counts": dict(combo_counts.most_common()),
        "examples": examples,
        "read_only": True,
        "authenticated": False,
        "order_entry": False,
    }


def main() -> None:
    print(
        "KALSHI_COLLATERAL_TYPE_INVENTORY "
        + json.dumps(asyncio.run(probe()), sort_keys=True, separators=(",", ":"), default=str)
    )


if __name__ == "__main__":
    main()
