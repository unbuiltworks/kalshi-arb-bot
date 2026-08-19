"""One-shot public audit probe for current Gemini MLB player-prop contract terms.

Prefer exact Gemini<->Kalshi matched contracts, but fall back to current Gemini-only
contracts for a stat family when the paired venue does not list one at this instant.
For public PDF terms, extract text plus a keyword-focused fingerprint so lifecycle
semantics can be audited against the paired Kalshi series.

No authentication and no order entry.
"""
from __future__ import annotations

import asyncio
from hashlib import sha256
from io import BytesIO
import json
from collections import defaultdict
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from src.gemini_kalshi_mlb_prop_shadow import _candidate_sides
from src.mlb_game_side_normalization import normalize_gemini_mlb
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events
from src.universal_three_venue_sports import fetch_gemini_mlb

TARGET_STATS = ("hits", "total_bases", "strikeouts")
STAT_TO_FAMILY = {
    "hits": "baseball_player_hits",
    "total_bases": "baseball_player_total_bases",
    "strikeouts": "baseball_player_strikeouts",
}
MAX_PER_STAT = 1
MAX_TERMS_CHARS = 18000
RULE_KEYWORDS = (
    "source agency", "official", "appear", "active", "start", "plate appearance",
    "batter faced", "postpon", "reschedul", "suspend", "cancel", "expiration",
    "correction", "revised", "final", "market outcome review", "fair price",
    "discretion", "forfeit", "withdraw", "disqualif", "settle", "resolve",
)


def _terms_url(contract: Mapping[str, Any]) -> str:
    for key in ("termsAndConditionsUrl", "terms_and_conditions_url", "termsUrl", "rulesUrl"):
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


def _pdf_fingerprint(content: bytes) -> dict[str, Any]:
    try:
        reader = PdfReader(BytesIO(content))
        pages = [str(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        return {"pdf_parse_status": "ERROR", "pdf_parse_error_type": type(exc).__name__}
    full = "\n".join(pages)
    lines = [" ".join(line.split()) for line in full.splitlines() if line.strip()]
    chosen: list[str] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        if not any(keyword in lowered for keyword in RULE_KEYWORDS):
            continue
        for near in range(max(0, index - 1), min(len(lines), index + 2)):
            candidate = lines[near]
            if candidate not in chosen:
                chosen.append(candidate)
    return {
        "pdf_parse_status": "OK",
        "pdf_pages": len(pages),
        "pdf_sha256": sha256(content).hexdigest(),
        "pdf_extracted_chars": len(full),
        "pdf_rule_excerpt": _compact_text("\n".join(chosen)),
        "pdf_full_text": _compact_text(full),
    }


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
    if response.is_success and "pdf" in content_type:
        row.update(_pdf_fingerprint(response.content))
    elif response.is_success and any(token in content_type for token in ("text/", "json", "html", "xml")):
        row["text_excerpt"] = _compact_text(response.text)
    else:
        row["binary_or_unparsed"] = True
    return row


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_events = await asyncio.gather(fetch_gemini_mlb(http), _fetch_kalshi_events(http))
        matched_sides, _venues, joins, _context = _candidate_sides(kalshi_events, gemini_events)
        all_gemini_sides = [
            side for side in normalize_gemini_mlb(gemini_events)
            if side.identity.market_family == "player_prop" and side.identity.stat in TARGET_STATS
        ]

        raw_event_by_symbol: dict[str, Mapping[str, Any]] = {}
        raw_contract_by_symbol: dict[str, Mapping[str, Any]] = {}
        for event in gemini_events:
            for contract in event.get("contracts") or []:
                if not isinstance(contract, Mapping):
                    continue
                symbol = str(contract.get("instrumentSymbol") or contract.get("ticker") or contract.get("contractTicker") or "")
                if symbol:
                    raw_event_by_symbol[symbol] = event
                    raw_contract_by_symbol[symbol] = contract

        join_by_symbol = {
            str(row.get("gemini_market_id")): row
            for row in joins
            if str(row.get("gemini_market_id") or "")
        }
        matched_by_stat: dict[str, list[str]] = defaultdict(list)
        for side in matched_sides:
            if side.identity.venue != "gemini" or side.identity.stat not in TARGET_STATS:
                continue
            if side.venue_market_id not in matched_by_stat[side.identity.stat]:
                matched_by_stat[side.identity.stat].append(side.venue_market_id)

        all_by_stat: dict[str, list[str]] = defaultdict(list)
        normalized_meta: dict[str, dict[str, Any]] = {}
        for side in all_gemini_sides:
            stat = side.identity.stat
            symbol = side.venue_market_id
            if symbol not in all_by_stat[stat]:
                all_by_stat[stat].append(symbol)
            normalized_meta[symbol] = {
                "family": STAT_TO_FAMILY.get(stat),
                "stat": stat,
                "player": side.identity.subject,
                "threshold": None if side.identity.line is None else str(side.identity.line),
            }

        selected: list[str] = []
        selection_source: dict[str, str] = {}
        for stat in TARGET_STATS:
            preferred = matched_by_stat.get(stat, [])
            fallback = [symbol for symbol in all_by_stat.get(stat, []) if symbol not in preferred]
            rows = (preferred + fallback)[:MAX_PER_STAT]
            for symbol in rows:
                selected.append(symbol)
                selection_source[symbol] = "EXACT_GK_MATCH" if symbol in preferred else "GEMINI_FAMILY_AUDIT_ONLY"

        urls = sorted({_terms_url(raw_contract_by_symbol.get(symbol, {})) for symbol in selected if _terms_url(raw_contract_by_symbol.get(symbol, {}))})
        terms_rows = await asyncio.gather(*(_fetch_terms(http, url) for url in urls))
        terms_by_url = dict(zip(urls, terms_rows))

    for symbol in selected:
        contract = raw_contract_by_symbol.get(symbol, {})
        event = raw_event_by_symbol.get(symbol, {})
        join = join_by_symbol.get(symbol, {})
        fallback = normalized_meta.get(symbol, {})
        url = _terms_url(contract)
        terms = dict(terms_by_url.get(url, {"status": "NO_TERMS_URL"}))
        full_text = terms.pop("pdf_full_text", None)
        print(json.dumps({
            "status": "GEMINI_MLB_PROP_TERMS_FINGERPRINT",
            "selection_source": selection_source.get(symbol),
            "symbol": symbol,
            "family": join.get("family") or fallback.get("family"),
            "stat": join.get("stat") or fallback.get("stat"),
            "player": join.get("player") or fallback.get("player"),
            "threshold": join.get("threshold") or fallback.get("threshold"),
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
            "terms_fetch": terms,
            "contract_keys": sorted(str(key) for key in contract.keys()),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str), flush=True)
        if full_text:
            print(json.dumps({
                "status": "GEMINI_MLB_PROP_TERMS_PDF_TEXT",
                "symbol": symbol,
                "terms_url": url,
                "pdf_full_text": full_text,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str), flush=True)

    print(json.dumps({
        "status": "GEMINI_MLB_PROP_TERMS_PROBE_SUMMARY",
        "exact_join_rows": len(joins),
        "selected_contracts": len(selected),
        "selected_by_stat": {stat: sum(1 for symbol in selected if normalized_meta.get(symbol, {}).get("stat") == stat or join_by_symbol.get(symbol, {}).get("stat") == stat) for stat in TARGET_STATS},
        "current_gemini_family_inventory": {stat: len(all_by_stat.get(stat, [])) for stat in TARGET_STATS},
        "exact_match_inventory": {stat: len(matched_by_stat.get(stat, [])) for stat in TARGET_STATS},
        "unique_terms_urls": len(urls),
        "terms_fetch_statuses": {status: sum(1 for row in terms_rows if row.get("status") == status) for status in sorted({str(row.get("status")) for row in terms_rows})},
        "pdf_parse_statuses": sorted({str(row.get("pdf_parse_status")) for row in terms_rows if row.get("pdf_parse_status")}),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
