"""One-shot read-only snapshot of Polymarket's live sports board.

Source of truth for *live match state* is Polymarket's public Sports WebSocket.
The probe then enriches each live slug from Gamma so downstream combo discovery
gets event/market identifiers and market metadata without relying on a narrow
hard-coded league list. This module is safe as a Railway one-shot start command.

No authentication, order placement, market creation, or capital usage occurs.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import websockets

SPORTS_WS = "wss://sports-api.polymarket.com/ws"
GAMMA_BASE = "https://gamma-api.polymarket.com"
DEFAULT_COLLECT_SECONDS = 12.0
MAX_COLLECT_SECONDS = 30.0
MAX_ENRICH_CONCURRENCY = 12


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _flatten_messages(value: Any) -> list[dict[str, Any]]:
    """Normalize direct objects, arrays, and common envelope shapes."""
    if isinstance(value, list):
        out: list[dict[str, Any]] = []
        for item in value:
            out.extend(_flatten_messages(item))
        return out
    if not isinstance(value, dict):
        return []

    for key in ("data", "payload", "results", "events"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            rows = _flatten_messages(nested)
            if rows:
                return rows
    return [value]


def _diagnostic_sample(row: dict[str, Any]) -> dict[str, Any]:
    """Public-feed shape only; intentionally excludes arbitrary values."""
    useful = (
        "type", "event_type", "slug", "live", "ended", "status", "sport",
        "league", "gameId", "game_id", "eventId", "event_id", "period",
        "elapsed", "score", "last_update", "lastUpdate",
    )
    return {
        "keys": sorted(str(k) for k in row.keys())[:80],
        **{k: row.get(k) for k in useful if k in row},
    }


def _live_row(row: dict[str, Any]) -> dict[str, Any] | None:
    slug = str(row.get("slug") or "").strip()
    if not slug:
        return None
    if not _truthy(row.get("live")) or _truthy(row.get("ended")):
        return None
    return {
        "slug": slug,
        "live": True,
        "ended": False,
        "score": row.get("score"),
        "period": row.get("period"),
        "elapsed": row.get("elapsed"),
        "status": row.get("status"),
        "league": row.get("league") or row.get("sport"),
        "last_update": row.get("last_update") or row.get("lastUpdate"),
    }


async def _collect_live_rows(seconds: float) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    live: dict[str, dict[str, Any]] = {}
    received = 0
    parsed = 0
    pings = 0
    errors: list[str] = []
    samples: list[dict[str, Any]] = []
    started = time.monotonic()
    deadline = started + seconds

    try:
        async with websockets.connect(
            SPORTS_WS,
            ping_interval=None,
            close_timeout=3,
            open_timeout=10,
            max_size=4 * 1024 * 1024,
        ) as ws:
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(2.0, remaining))
                except asyncio.TimeoutError:
                    continue

                received += 1
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                text = str(raw).strip()
                if text.lower() == "ping":
                    pings += 1
                    await ws.send("pong")
                    continue
                if text.lower() == "pong":
                    continue

                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue

                if isinstance(message, dict) and str(message.get("type") or "").lower() == "ping":
                    pings += 1
                    await ws.send("pong")
                    if len(samples) < 12:
                        samples.append(_diagnostic_sample(message))
                    continue

                for row in _flatten_messages(message):
                    parsed += 1
                    if len(samples) < 12:
                        samples.append(_diagnostic_sample(row))
                    item = _live_row(row)
                    if item:
                        live[item["slug"]] = item
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    return live, {
        "collect_seconds": round(time.monotonic() - started, 3),
        "messages_received": received,
        "objects_parsed": parsed,
        "pings_answered": pings,
        "errors": errors,
        "diagnostic_samples": samples,
    }


async def _enrich_one(
    http: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    row: dict[str, Any],
) -> dict[str, Any]:
    slug = row["slug"]
    async with semaphore:
        try:
            response = await http.get(f"{GAMMA_BASE}/events", params={"slug": slug})
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            return {**row, "gamma_error": f"{type(exc).__name__}: {exc}", "event": None}

    candidates = body if isinstance(body, list) else body.get("events") if isinstance(body, dict) else []
    event = next((x for x in (candidates or []) if isinstance(x, dict)), None)
    if not event:
        return {**row, "gamma_error": "event_not_found", "event": None}

    open_markets: list[dict[str, Any]] = []
    for market in event.get("markets") or []:
        if not isinstance(market, dict):
            continue
        if market.get("closed") is True or market.get("active") is False:
            continue
        open_markets.append({
            "id": market.get("id"),
            "condition_id": market.get("conditionId") or market.get("condition_id"),
            "slug": market.get("slug"),
            "question": market.get("question"),
            "market_type": market.get("sportsMarketType") or market.get("marketType"),
            "outcomes": market.get("outcomes"),
            "outcome_prices": market.get("outcomePrices"),
            "clob_token_ids": market.get("clobTokenIds"),
            "accepting_orders": market.get("acceptingOrders"),
            "liquidity": market.get("liquidityNum") or market.get("liquidity"),
            "volume": market.get("volumeNum") or market.get("volume"),
            "end_date": market.get("endDate"),
        })

    return {
        **row,
        "event": {
            "id": event.get("id"),
            "ticker": event.get("ticker"),
            "slug": event.get("slug"),
            "title": event.get("title"),
            "category": event.get("category"),
            "subcategory": event.get("subcategory"),
            "start_date": event.get("startDate"),
            "end_date": event.get("endDate"),
            "active": event.get("active"),
            "closed": event.get("closed"),
            "liquidity": event.get("liquidity"),
            "volume": event.get("volume"),
            "open_markets": open_markets,
            "open_market_count": len(open_markets),
        },
        "gamma_error": None,
    }


async def main_async() -> dict[str, Any]:
    requested = float(os.getenv("POLY_LIVE_SPORTS_COLLECT_SECONDS", str(DEFAULT_COLLECT_SECONDS)))
    seconds = min(MAX_COLLECT_SECONDS, max(3.0, requested))
    live_rows, transport = await _collect_live_rows(seconds)

    semaphore = asyncio.Semaphore(MAX_ENRICH_CONCURRENCY)
    async with httpx.AsyncClient(timeout=15.0) as http:
        enriched = await asyncio.gather(
            *[_enrich_one(http, semaphore, row) for row in live_rows.values()]
        )

    enriched.sort(key=lambda x: str((x.get("event") or {}).get("title") or x.get("slug")))
    gamma_matched = sum(x.get("event") is not None for x in enriched)
    accepting = sum(
        any(m.get("accepting_orders") is not False for m in ((x.get("event") or {}).get("open_markets") or []))
        for x in enriched
    )

    return {
        "status": "POLYMARKET_LIVE_SPORTS_SNAPSHOT",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": SPORTS_WS,
        "live_event_count": len(enriched),
        "gamma_matched_count": gamma_matched,
        "events_with_open_markets": sum(((x.get("event") or {}).get("open_market_count") or 0) > 0 for x in enriched),
        "events_with_accepting_markets": accepting,
        "transport": transport,
        "events": enriched,
        "read_only": True,
        "market_creation": False,
        "order_entry": False,
        "funds_at_risk": False,
    }


def main() -> None:
    result = asyncio.run(main_async())
    print("POLYMARKET_LIVE_SPORTS_PROBE " + json.dumps(result, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
