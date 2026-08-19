"""One-shot public audit probe for exact Gemini MLB player-prop contract terms.

The probe starts from the independently matched Gemini<->Kalshi MLB prop universe,
then inspects the *actual Gemini raw contracts* behind those matches. It prints only
public contract metadata and, when the per-contract terms URL is publicly readable,
a compact text excerpt and response metadata.

No authentication and no order entry.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

from src.gemini_kalshi_mlb_prop_shadow import _candidate_sides
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events
from src.three_venue_mlb_price_scan import _gemini_contracts
from src.universal_three_venue_sports import fetch_gemini_mlb

TARGET_STATS = ("hits", "total_bases", "strikeouts")
MAX_PER_STAT = 3
MAX_TERMS_CHARS = 12000


def _terms_url(contract: Mapping[str, Any]) -> str:
    for key in (
        "termsAndConditionsUrl",
        "terms_and_conditions_url",
        "termsUrl",
        "rulesUrl",
    ):
        value = str(contract.get(key) or "").strip()
        if value:
            return value
    return ""


def _safe_public_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def _compact_text(text: str, limit: int = MAX_TERMS_CHARS) -> str:
    return " ".join(text.split())[:limit]


async def _fetch_terms(http: httpx.AsyncClient, url: str) -> dict[str, Any]:
    if not _safe_public_url(url):
        return {"status": "NO_PUBLIC_URL"}
    try:
        response = await http.get(url, follow_redirects=True)
    except Exception as exc:
        return {"status": "FETCH_ERROR", "error_type": type(exc).__name__}
    content_type = str(response.headers.get("content-type") or "").lower()
    row: dict[str, Any] = {
        "status": "OK" if response.is_success else "HTTP_ERROR",
        "http_status": response.status_code,
        "content_type": content_type,
        "final_url": str(response.url),
        "content_length": len(response.content),
    }
    if response.is_success and (
        "text/" in content_type
        or "json" in content_type
        or "html" in content_type
        or "xml" in content_type
    ):
        row["text_excerpt"] = _compact_text(response.text)
    else:
        row["binary_or_unparsed"] = True
    return row


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_events = await asyncio.gather(
            fetch_gemini_mlb(http), _fetch_kalshi_events(http)
        )
        sides, _venues, joins, _context = _candidate_sides(kalshi_events, gemini_events)
        contracts = _gemini_contracts(gemini_events)

        raw_event_by_symbol: dict[str, Mapping[str, Any]] = {}
        raw_contract_by_symbol: dict[str, Mapping[str, Any]] = {}
        for event in gemini_events:
            for contract in event.get("contracts") or []:
                if not isinstance(contract, Mapping):
                    continue
                symbol = str(
                    contract.get("instrumentSymbol")
                    or contract.get("ticker")
                    or contract.get("contractTicker")
                    or ""
                )
                if symbol:
                    raw_event_by_symbol[symbol] = event
                    raw_contract_by_symbol[symbol] = contract

        join_by_symbol: dict[str, dict[str, Any]] = {}
        for row in joins:
            symbol = str(row.get("gemini_market_id") or "")
            if symbol:
                join_by_symbol[symbol] = row

        symbols_by_stat: dict[str, list[str]] = defaultdict(list)
        for side in sides:
            if side.identity.venue != "gemini":
                continue
            stat = str(side.identity.stat or "")
            if stat not in TARGET_STATS:
                continue
            symbol = side.venue_market_id
            if symbol not in symbols_by_stat[stat]:
                symbols_by_stat[stat].append(symbol)

        selected: list[str] = []
        for stat in TARGET_STATS:
            selected.extend(symbols_by_stat.get(stat, [])[:MAX_PER_STAT])

        urls = sorted({
            _terms_url(raw_contract_by_symbol.get(symbol, {}))
            for symbol in selected
            if _terms_url(raw_contract_by_symbol.get(symbol, {}))
        })
        terms_rows = await asyncio.gather(*(_fetch_terms(http, url) for url in urls))
        terms_by_url = dict(zip(urls, terms_rows))

    for symbol in selected:
        contract = raw_contract_by_symbol.get(symbol, {})
        event = raw_event_by_symbol.get(symbol, {})
        join = join_by_symbol.get(symbol, {})
        url = _terms_url(contract)
        print(json.dumps({
            "status": "GEMINI_MLB_PROP_TERMS_FINGERPRINT",
            "symbol": symbol,
            "family": join.get("family"),
            "stat": join.get("stat"),
            "player": join.get("player"),
            "threshold": join.get("threshold"),
            "kalshi_ticker": join.get("kalshi_ticker"),
            "start_skew_ms": join.get("start_skew_ms"),
            "event_ticker": event.get("ticker") or event.get("eventTicker"),
            "event_title": event.get("title"),
            "event_description": event.get("description"),
            "sports_market": event.get("sportsMarket"),
            "contract_label": contract.get("label") or contract.get("title"),
            "contract_description": contract.get("description"),
            "strike": contract.get("strike"),
            "terms_url": url,
            "terms_fetch": terms_by_url.get(url, {"status": "NO_TERMS_URL"}),
            "contract_keys": sorted(str(key) for key in contract.keys()),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str), flush=True)

    print(json.dumps({
        "status": "GEMINI_MLB_PROP_TERMS_PROBE_SUMMARY",
        "exact_join_rows": len(joins),
        "selected_contracts": len(selected),
        "selected_by_stat": {
            stat: min(MAX_PER_STAT, len(symbols_by_stat.get(stat, [])))
            for stat in TARGET_STATS
        },
        "unique_terms_urls": len(urls),
        "terms_fetch_statuses": {
            status: sum(1 for row in terms_rows if row.get("status") == status)
            for status in sorted({str(row.get("status")) for row in terms_rows})
        },
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
