"""One-shot read-only probe for the live 49ers + Braves combo candidate.

Discovers the exact Polymarket US and Kalshi component markets and prints only
safe market identity / top-of-book metadata. It never creates markets or orders.
Railway one-shot entrypoint: sports_kp_live_pair_probe.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx
from polymarket_us import PolymarketUS

from src.auth import KalshiClient


def _walk(obj: Any):
    if isinstance(obj, Mapping):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _text(row: Mapping[str, Any]) -> str:
    keys = ("title", "name", "question", "description", "slug", "marketSlug", "eventSlug", "ticker", "event_ticker", "market_ticker")
    return " ".join(str(row.get(k) or "") for k in keys).lower()


def _safe_poly(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: row.get(k)
        for k in (
            "id", "slug", "marketSlug", "eventSlug", "title", "name", "question",
            "state", "status", "active", "closed", "startDate", "endDate",
            "minimumTradeQty", "orderPriceMinTickSize",
        )
        if row.get(k) is not None
    }


def _safe_kalshi_event(event: Mapping[str, Any]) -> dict[str, Any]:
    markets = []
    for m in event.get("markets") or []:
        if not isinstance(m, Mapping):
            continue
        markets.append({
            k: m.get(k)
            for k in (
                "ticker", "title", "subtitle", "yes_sub_title", "no_sub_title", "status",
                "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars",
                "yes_bid", "yes_ask", "no_bid", "no_ask", "volume", "open_interest",
                "close_time", "expected_expiration_time",
            )
            if m.get(k) is not None
        })
    return {
        "event_ticker": event.get("event_ticker"),
        "title": event.get("title"),
        "subtitle": event.get("subtitle"),
        "category": event.get("category"),
        "markets": markets,
    }


def _poly_search(query: str) -> dict[str, Any]:
    client = PolymarketUS(timeout=8.0)
    try:
        body = client.search.query({"query": query})
        rows = []
        seen = set()
        for row in _walk(body):
            text = _text(row)
            if not text:
                continue
            if any(term in text for term in ("49ers", "san francisco", "chargers", "braves", "atlanta", "brewers", "milwaukee")):
                safe = _safe_poly(row)
                sig = json.dumps(safe, sort_keys=True, default=str)
                if safe and sig not in seen:
                    seen.add(sig)
                    rows.append(safe)
        bbo = []
        slugs = []
        for row in rows:
            slug = str(row.get("marketSlug") or row.get("slug") or "")
            if slug and slug not in slugs:
                slugs.append(slug)
        for slug in slugs[:25]:
            try:
                raw = client.markets.bbo(slug)
                bbo.append({"slug": slug, "bbo": raw})
            except Exception as exc:
                bbo.append({"slug": slug, "error": f"{type(exc).__name__}:{str(exc)[:160]}"})
        return {"query": query, "matches": rows[:50], "bbo": bbo}
    finally:
        client.close()


async def _kalshi_series(client: KalshiClient, series: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(8):
        params: dict[str, Any] = {"limit": 200, "status": "open", "series_ticker": series, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        resp = await client.get("/events", params=params)
        resp.raise_for_status()
        body = resp.json()
        out.extend([dict(x) for x in body.get("events") or [] if isinstance(x, Mapping)])
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
    return out


async def main_async() -> None:
    poly_results = await asyncio.gather(
        asyncio.to_thread(_poly_search, "San Francisco 49ers Los Angeles Chargers"),
        asyncio.to_thread(_poly_search, "Atlanta Braves Milwaukee Brewers"),
        asyncio.to_thread(_poly_search, "San Francisco 49ers Atlanta Braves"),
    )

    kalshi = KalshiClient()
    try:
        nfl, mlb = await asyncio.gather(
            _kalshi_series(kalshi, "KXNFLGAME"),
            _kalshi_series(kalshi, "KXMLBGAME"),
        )
    finally:
        await kalshi.close()

    kalshi_matches = []
    for event in nfl + mlb:
        text = json.dumps(event, default=str).lower()
        if (("49ers" in text or "san francisco" in text) and ("chargers" in text or "los angeles" in text)) or (("braves" in text or "atlanta" in text) and ("brewers" in text or "milwaukee" in text)):
            kalshi_matches.append(_safe_kalshi_event(event))

    result = {
        "status": "SPORTS_KP_LIVE_PAIR_PROBE",
        "target": "49ers_yes + braves_yes",
        "polymarket": poly_results,
        "kalshi": kalshi_matches,
        "read_only": True,
        "market_creation": False,
        "order_entry": False,
    }
    print(json.dumps(result, separators=(",", ":"), default=str))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
