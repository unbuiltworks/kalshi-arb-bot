"""Cached read-only diagnostic for Polymarket's direct active sports market surface.

The event endpoint can omit player/alternate markets. This diagnostic pages the direct
/v1/markets sports inventory, records structured field coverage, and tests whether a
row would satisfy the existing structural identity after only safe metadata aliases
(parent key and sportsMarketTypeV2 -> sportsMarketType). It never places orders.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import time
from typing import Any, Mapping

import httpx

from src.sports_poly_structural_monotone import BASE, structural_identity

_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
_CACHE_LOCK = asyncio.Lock()
TTL_SECONDS = 180.0
MAX_PAGES = 12
LIMIT = 50


def _enrich(raw: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(raw)
    if not row.get("_parent_event_key"):
        row["_parent_event_key"] = (
            row.get("eventSlug") or row.get("event_slug") or row.get("gameId")
            or row.get("game_id") or row.get("eventId") or row.get("event_id") or ""
        )
    if not row.get("sportsMarketType") and row.get("sportsMarketTypeV2"):
        row["sportsMarketType"] = row.get("sportsMarketTypeV2")
    return row


def _sample(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "slug": row.get("slug"),
        "marketType": row.get("marketType"),
        "sportsMarketType": row.get("sportsMarketType"),
        "sportsMarketTypeV2": row.get("sportsMarketTypeV2"),
        "line": row.get("line"),
        "eventSlug": row.get("eventSlug"),
        "eventId": row.get("eventId"),
        "gameId": row.get("gameId"),
        "question": row.get("question") or row.get("title"),
    }


async def _scan() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    pages_ok = 0
    failures = 0
    async with httpx.AsyncClient(timeout=12.0) as http:
        for page in range(MAX_PAGES):
            try:
                response = await http.get(
                    f"{BASE}/v1/markets",
                    params={
                        "limit": LIMIT,
                        "offset": page * LIMIT,
                        "active": "true",
                        "closed": "false",
                        "categories": "sports",
                    },
                )
                response.raise_for_status()
            except Exception:
                failures += 1
                break
            pages_ok += 1
            body = response.json() if response.content else {}
            page_rows = body.get("markets") or [] if isinstance(body, Mapping) else []
            rows.extend(dict(r) for r in page_rows if isinstance(r, Mapping))
            if len(page_rows) < LIMIT:
                break

    market_types = Counter(str(r.get("marketType") or "<missing>") for r in rows)
    sports_types = Counter(str(r.get("sportsMarketType") or "<missing>") for r in rows)
    sports_v2_types = Counter(str(r.get("sportsMarketTypeV2") or "<missing>") for r in rows)
    parent_fields = Counter()
    identities: list[dict[str, Any]] = []
    for raw in rows:
        for key in ("eventSlug", "gameId", "eventId"):
            if raw.get(key) not in (None, ""):
                parent_fields[key] += 1
        enriched = _enrich(raw)
        identity = structural_identity(enriched)
        if identity is not None:
            identities.append(enriched)

    payload = {
        "pages_requested": min(MAX_PAGES, pages_ok + failures),
        "pages_ok": pages_ok,
        "page_failures": failures,
        "markets": len(rows),
        "market_types": dict(market_types.most_common(20)),
        "sports_market_types": dict(sports_types.most_common(30)),
        "sports_market_types_v2": dict(sports_v2_types.most_common(30)),
        "parent_fields": dict(parent_fields),
        "structural_identity_rows_after_safe_aliases": len(identities),
        "structural_samples": [_sample(r) for r in identities[:8]],
        "surface_samples": [_sample(r) for r in rows[:12]],
        "read_only": True,
        "order_entry": False,
    }
    return payload


async def run_once() -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    if _CACHE is not None and now - _CACHE_AT < TTL_SECONDS:
        payload = dict(_CACHE)
        payload["cached"] = True
    else:
        async with _CACHE_LOCK:
            now = time.monotonic()
            if _CACHE is None or now - _CACHE_AT >= TTL_SECONDS:
                _CACHE = await _scan()
                _CACHE_AT = time.monotonic()
            payload = dict(_CACHE)
            payload["cached"] = False
    out = {
        "status": "NO_EXACT_DIRECT_SPORTS_DIAGNOSTIC",
        "diagnostic": payload,
        "attempts": [],
        "order_entry": False,
        "locked_execution": False,
        "realized": False,
    }
    print("SPORTS_POLY_DIRECT_SPORTS_DIAGNOSTIC " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out
