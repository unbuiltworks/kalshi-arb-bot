"""Broad read-only inventory of currently live Polymarket US sports events.

Polymarket US GET /v1/events does not document a `live` query filter. Instead,
page the started/open/not-ended sports universe and classify live state from the
returned event `live` field and nested `eventState.live` field. This mirrors the
US event model without hard-coded leagues or global-Polymarket assumptions.

Discovery only: no authentication, market creation, order entry, or capital use.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from typing import Any, Mapping

import httpx

BASE = "https://gateway.polymarket.us"
PAGE = 100
MAX_PAGES = 50


def _truthy(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _state(event: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = event.get("eventState")
    return raw if isinstance(raw, Mapping) else {}


def _live_reason(event: Mapping[str, Any]) -> str | None:
    state = _state(event)
    ended = _truthy(event.get("ended")) or _truthy(state.get("ended"))
    if ended or event.get("closed") is True or event.get("active") is False or event.get("hidden") is True:
        return None

    if _truthy(event.get("live")):
        return "event.live"
    if _truthy(state.get("live")):
        return "eventState.live"

    status = str(event.get("gameStatus") or event.get("status") or "").strip().lower().replace("_", "")
    if status in {"live", "running", "inprogress", "inplay", "started"}:
        return "gameStatus"
    return None


def _market_row(market: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "slug", "title", "question", "sportsMarketType", "sportsMarketTypeV2",
        "line", "gameId", "gameStartTime", "startTime", "endDate", "active", "closed",
        "acceptingOrders", "bestBid", "bestAsk", "lastTradePrice", "liquidity", "volume",
        "openInterest", "status",
    )
    out = {key: market.get(key) for key in keys if market.get(key) is not None}
    for key in ("outcomes", "marketSides", "clobTokenIds"):
        if market.get(key) is not None:
            out[key] = _jsonish(market.get(key))
    return out


def _event_row(event: Mapping[str, Any], reason: str) -> dict[str, Any]:
    state = _state(event)
    markets = [
        _market_row(market)
        for market in (event.get("markets") or [])
        if isinstance(market, Mapping)
        and market.get("closed") is not True
        and market.get("active") is not False
    ]
    return {
        "id": event.get("id"),
        "ticker": event.get("ticker"),
        "slug": event.get("slug"),
        "title": event.get("title"),
        "category": event.get("category"),
        "subcategory": event.get("subcategory"),
        "series_slug": event.get("seriesSlug"),
        "game_id": event.get("gameId") or state.get("gameId"),
        "sportradar_game_id": event.get("sportradarGameId") or state.get("sportradarGameId"),
        "start_time": event.get("startTime") or event.get("startDate") or event.get("eventDate"),
        "end_date": event.get("endDate"),
        "live": event.get("live"),
        "event_state_live": state.get("live"),
        "ended": event.get("ended"),
        "event_state_ended": state.get("ended"),
        "game_status": event.get("gameStatus") or event.get("status"),
        "score": event.get("score") if event.get("score") is not None else state.get("score"),
        "period": event.get("period") if event.get("period") is not None else state.get("period"),
        "elapsed": event.get("elapsed") if event.get("elapsed") is not None else state.get("elapsed"),
        "live_reason": reason,
        "liquidity": event.get("liquidity"),
        "volume": event.get("volume"),
        "volume24hr": event.get("volume24hr"),
        "open_interest": event.get("openInterest"),
        "open_market_count": len(markets),
        "accepting_market_count": sum(m.get("acceptingOrders") is not False for m in markets),
        "markets": markets,
    }


async def _fetch_page(
    http: httpx.AsyncClient,
    *,
    offset: int,
    now_iso: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "limit": PAGE,
        "offset": offset,
        "active": "true",
        "closed": "false",
        "ended": "false",
        "categories": "sports",
        "startTimeMax": now_iso,
        "orderBy": "startTime",
        "orderDirection": "desc",
    }
    try:
        response = await http.get(f"{BASE}/v1/events", params=params)
        status = response.status_code
        response.raise_for_status()
        body = response.json()
        rows = body.get("events") or [] if isinstance(body, dict) else []
        rows = [dict(row) for row in rows if isinstance(row, Mapping)]
        return rows, {
            "offset": offset,
            "status": status,
            "rows": len(rows),
            "body_keys": sorted(str(key) for key in body.keys())[:30] if isinstance(body, dict) else [],
        }
    except Exception as exc:
        return [], {
            "offset": offset,
            "status": None,
            "rows": 0,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }


async def run_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    diagnostics: list[dict[str, Any]] = []
    raw_events: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0) as http:
        for page in range(MAX_PAGES):
            rows, diag = await _fetch_page(http, offset=page * PAGE, now_iso=now_iso)
            diagnostics.append(diag)
            if diag.get("error"):
                break
            for row in rows:
                key = str(row.get("id") or row.get("gameId") or row.get("slug") or "")
                if key and key not in seen:
                    seen.add(key)
                    raw_events.append(row)
            if len(rows) < PAGE:
                break

    live_rows: list[dict[str, Any]] = []
    for event in raw_events:
        reason = _live_reason(event)
        if reason:
            live_rows.append(_event_row(event, reason))

    live_rows.sort(key=lambda row: (
        str(row.get("series_slug") or row.get("subcategory") or ""),
        str(row.get("title") or row.get("slug") or ""),
    ))

    series_counts = Counter(
        str(row.get("series_slug") or row.get("subcategory") or row.get("category") or "unknown")
        for row in live_rows
    )
    reason_counts = Counter(str(row.get("live_reason") or "unknown") for row in live_rows)

    return {
        "status": "POLYMARKET_US_LIVE_SPORTS_INVENTORY_V3",
        "observed_at": now.isoformat(),
        "started_open_not_ended_sports_scanned": len(raw_events),
        "live_event_count": len(live_rows),
        "live_reason_counts": dict(reason_counts.most_common()),
        "series_counts": dict(series_counts.most_common()),
        "events_with_open_markets": sum(row["open_market_count"] > 0 for row in live_rows),
        "events_with_accepting_markets": sum(row["accepting_market_count"] > 0 for row in live_rows),
        "diagnostics": diagnostics,
        "events": live_rows[:250],
        "read_only": True,
        "market_creation": False,
        "order_entry": False,
        "funds_at_risk": False,
    }


def main() -> None:
    print("POLYMARKET_US_LIVE_SPORTS_INVENTORY_V3 " + json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
