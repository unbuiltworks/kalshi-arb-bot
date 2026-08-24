"""One-shot read-only Polymarket live sports snapshot from Gamma.

Gamma is used for the initial board because event objects contain live/ended,
score, period, elapsed and gameStatus. This avoids undercounting caused by using
the change-driven Sports WebSocket as if it were an initial snapshot feed.

Discovery only: no auth, order placement, market creation, or capital usage.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

GAMMA = "https://gamma-api.polymarket.com"
SPORTS_TAG_ID = 100639
PAGE = 100
MAX_PAGES = 15
LOOKBACK_HOURS = 24


def _truthy(v: Any) -> bool:
    return v is True or str(v or "").strip().lower() in {"true", "1", "yes"}


def _jsonish(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    try:
        return json.loads(v)
    except Exception:
        return v


def _market(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": m.get("id"), "condition_id": m.get("conditionId") or m.get("condition_id"),
        "slug": m.get("slug"), "question": m.get("question"),
        "market_type": m.get("sportsMarketType") or m.get("marketType"), "line": m.get("line"),
        "outcomes": _jsonish(m.get("outcomes")), "outcome_prices": _jsonish(m.get("outcomePrices")),
        "clob_token_ids": _jsonish(m.get("clobTokenIds")), "accepting_orders": m.get("acceptingOrders"),
        "best_bid": m.get("bestBid"), "best_ask": m.get("bestAsk"), "last_trade_price": m.get("lastTradePrice"),
        "liquidity": m.get("liquidityNum") or m.get("liquidity"), "volume": m.get("volumeNum") or m.get("volume"),
        "end_date": m.get("endDate"),
    }


def _sport_label(e: dict[str, Any]) -> str:
    labels = [str(t.get("label") or t.get("slug") or "") for t in (e.get("tags") or []) if isinstance(t, dict)]
    for label in labels:
        low = label.lower()
        if any(x in low for x in ("table tennis", "tennis", "golf", "esports", "football", "baseball", "basketball", "soccer", "hockey", "mma", "ufc", "darts", "cricket")):
            return label
    return str(e.get("subcategory") or e.get("seriesSlug") or e.get("category") or "sports")


async def _fetch_page(http: httpx.AsyncClient, offset: int, now: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start_min = (now - timedelta(hours=LOOKBACK_HOURS)).isoformat().replace("+00:00", "Z")
    start_max = now.isoformat().replace("+00:00", "Z")
    params = {
        "active": "true", "closed": "false", "tag_id": SPORTS_TAG_ID, "related_tags": "true",
        "start_date_min": start_min, "start_date_max": start_max, "limit": PAGE, "offset": offset,
    }
    try:
        r = await http.get(f"{GAMMA}/events", params=params)
        status = r.status_code
        if status != 200:
            return [], {"offset": offset, "status": status, "rows": 0, "error": r.text[:180]}
        body = r.json()
    except Exception as exc:
        return [], {"offset": offset, "status": None, "rows": 0, "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
    if isinstance(body, list):
        rows = [x for x in body if isinstance(x, dict)]; shape = "list"; keys: list[str] = []
    elif isinstance(body, dict):
        raw = body.get("events") or body.get("data") or body.get("results") or []
        rows = [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
        shape = "dict"; keys = sorted(str(k) for k in body.keys())[:30]
    else:
        rows, shape, keys = [], type(body).__name__, []
    return rows, {"offset": offset, "status": status, "shape": shape, "top_keys": keys, "rows": len(rows)}


async def main_async() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    diagnostics: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0) as http:
        offset = 0
        for _ in range(MAX_PAGES):
            rows, diag = await _fetch_page(http, offset, now)
            diagnostics.append(diag)
            if not rows:
                break
            events.extend(rows)
            offset += len(rows)
            if len(rows) < PAGE:
                break

    live = [e for e in events if _truthy(e.get("live")) and not _truthy(e.get("ended")) and e.get("closed") is not True]
    live.sort(key=lambda e: (-float(e.get("volume24hr") or 0), str(e.get("title") or "")))
    out_events: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for e in live:
        sport = _sport_label(e); counts[sport] += 1
        markets = [_market(m) for m in (e.get("markets") or []) if isinstance(m, dict) and m.get("closed") is not True and m.get("active") is not False]
        out_events.append({
            "id": e.get("id"), "game_id": e.get("gameId"), "slug": e.get("slug"), "title": e.get("title"),
            "sport": sport, "live": e.get("live"), "ended": e.get("ended"), "score": e.get("score"),
            "period": e.get("period"), "elapsed": e.get("elapsed"), "game_status": e.get("gameStatus"),
            "start_time": e.get("startTime") or e.get("eventStartTime") or e.get("startDate"), "end_date": e.get("endDate"),
            "volume24hr": e.get("volume24hr"), "liquidity": e.get("liquidity"), "open_market_count": len(markets),
            "accepting_market_count": sum(m.get("accepting_orders") is not False for m in markets), "markets": markets,
        })

    sample_state = [{
        "id": e.get("id"), "game_id": e.get("gameId"), "title": e.get("title"), "live": e.get("live"),
        "ended": e.get("ended"), "game_status": e.get("gameStatus"), "period": e.get("period"),
        "start_time": e.get("startTime") or e.get("startDate"),
    } for e in events[:20]]
    return {
        "status": "POLYMARKET_LIVE_GAMMA_SNAPSHOT", "observed_at": now.isoformat(),
        "sports_tag_id": SPORTS_TAG_ID, "lookback_hours": LOOKBACK_HOURS,
        "active_sports_events_scanned": len(events), "live_event_count": len(out_events),
        "category_counts": dict(counts.most_common()),
        "events_with_open_markets": sum(e["open_market_count"] > 0 for e in out_events),
        "events_with_accepting_markets": sum(e["accepting_market_count"] > 0 for e in out_events),
        "diagnostics": diagnostics, "sample_state": sample_state, "events": out_events[:150],
        "read_only": True, "order_entry": False, "market_creation": False, "funds_at_risk": False,
    }


def main() -> None:
    print("POLYMARKET_LIVE_GAMMA_SNAPSHOT " + json.dumps(asyncio.run(main_async()), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
