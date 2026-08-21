"""Broad read-only inventory of currently live Polymarket US sports events.

Uses the documented generic /v1/events endpoint instead of a small hard-coded
league list. Primary discovery is categories=sports + live=true. A second
started/not-ended query is merged as a fallback for events the UI treats as live
before/without the live flag being populated. One-shot and order-free.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from typing import Any, Mapping

import httpx

BASE = "https://gateway.polymarket.us"


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        out = datetime.fromisoformat(text)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def _safe_market(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "slug", "marketSlug", "title", "name", "question", "active", "closed",
        "status", "startTime", "endDate", "live", "ended", "period", "bestBid",
        "bestAsk", "lastTradePrice", "liquidity", "volume", "marketType",
    )
    return {k: row.get(k) for k in keys if row.get(k) is not None}


def _event_key(raw: Mapping[str, Any]) -> str:
    return str(raw.get("id") or raw.get("ticker") or raw.get("slug") or raw.get("title") or "")


def _event_row(raw: Mapping[str, Any], now: datetime, source: str) -> dict[str, Any] | None:
    if raw.get("closed") is True or raw.get("ended") is True or raw.get("active") is False:
        return None

    start = _dt(raw.get("startTime") or raw.get("startDate") or raw.get("eventDate"))
    explicitly_live = raw.get("live") is True
    started = start is not None and start <= now
    period = str(raw.get("period") or "").strip().upper()
    in_progress_period = bool(period) and period not in {"FINAL", "ENDED", "CLOSED", "FT"}

    # Primary live flag OR defensible UI-style fallback: event has started and is
    # still active/not ended. Period is retained as corroborating metadata.
    inferred_live = explicitly_live or (started and raw.get("ended") is not True and raw.get("closed") is not True)
    if not inferred_live:
        return None

    end = _dt(raw.get("endDate") or raw.get("end_time") or raw.get("endTime"))
    hours = None if end is None else round((end - now).total_seconds() / 3600, 3)
    markets = [
        _safe_market(x) for x in (raw.get("markets") or [])
        if isinstance(x, Mapping) and x.get("closed") is not True and x.get("active") is not False
    ]
    keys = (
        "id", "ticker", "slug", "title", "subtitle", "description", "category", "subcategory",
        "seriesSlug", "live", "ended", "period", "score", "elapsed", "startTime", "startDate",
        "eventDate", "endDate", "active", "closed", "status", "liquidity", "volume", "openInterest",
    )
    out = {k: raw.get(k) for k in keys if raw.get(k) is not None}
    out.update({
        "discovery_source": source,
        "explicitly_live": explicitly_live,
        "started": started,
        "in_progress_period": in_progress_period,
        "inferred_live": inferred_live,
        "hours_to_end": hours,
        "open_markets": len(markets),
        "markets": markets,
    })
    return out


async def _get_events(http: httpx.AsyncClient, params: dict[str, Any], source: str) -> tuple[str, list[dict[str, Any]], str | None]:
    try:
        response = await http.get(f"{BASE}/v1/events", params=params)
        response.raise_for_status()
        body = response.json()
        rows = body.get("events") or body.get("data") or [] if isinstance(body, dict) else []
        return source, [dict(x) for x in rows if isinstance(x, Mapping)], None
    except Exception as exc:
        return source, [], f"{type(exc).__name__}:{str(exc)[:220]}"


async def run_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    queries = (
        ({"limit": 500, "active": "true", "closed": "false", "ended": "false", "live": "true", "categories": "sports"}, "live_flag"),
        ({"limit": 500, "active": "true", "closed": "false", "ended": "false", "startTimeMax": now_iso, "categories": "sports"}, "started_fallback"),
    )
    async with httpx.AsyncClient(timeout=15.0) as http:
        results = await asyncio.gather(*(_get_events(http, params, source) for params, source in queries))

    merged: dict[str, tuple[dict[str, Any], str]] = {}
    raw_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    for source, rows, error in results:
        raw_counts[source] = len(rows)
        if error:
            errors[source] = error
        for raw in rows:
            key = _event_key(raw)
            if not key:
                continue
            # Prefer the explicit-live payload when an event appears in both.
            if key not in merged or source == "live_flag":
                merged[key] = (raw, source)

    live_rows: list[dict[str, Any]] = []
    for raw, source in merged.values():
        row = _event_row(raw, now, source)
        if row is not None:
            live_rows.append(row)

    live_rows.sort(key=lambda x: (
        not bool(x.get("explicitly_live")),
        x.get("hours_to_end") is None,
        x.get("hours_to_end") if x.get("hours_to_end") is not None else 1e9,
        -(float(x.get("liquidity") or 0)),
    ))

    under_24 = [x for x in live_rows if x.get("hours_to_end") is not None and -1 <= float(x["hours_to_end"]) <= 24]
    under_6 = [x for x in under_24 if float(x["hours_to_end"]) <= 6]
    category_counts = Counter(str(x.get("subcategory") or x.get("seriesSlug") or x.get("category") or "unknown") for x in live_rows)
    explicit_count = sum(1 for x in live_rows if x.get("explicitly_live"))
    fallback_only = len(live_rows) - explicit_count

    return {
        "status": "POLYMARKET_LIVE_SPORTS_INVENTORY_V2",
        "as_of_utc": now.isoformat(),
        "raw_query_counts": raw_counts,
        "merged_unique_events": len(merged),
        "live_events": len(live_rows),
        "explicit_live_events": explicit_count,
        "started_fallback_only": fallback_only,
        "live_under_24h": len(under_24),
        "live_under_6h": len(under_6),
        "category_counts": dict(category_counts.most_common()),
        "ranked_live_events": live_rows[:150],
        "errors": errors,
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    print("POLYMARKET_LIVE_SPORTS_INVENTORY_V2 " + json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
