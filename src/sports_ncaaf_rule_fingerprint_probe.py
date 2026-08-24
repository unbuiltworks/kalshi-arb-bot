"""Read-only rule fingerprint for persistent NCAAF Gemini/Kalshi moneyline pairs.

Fetches current public Gemini contract terms plus Kalshi nested-market rule fields for
representative exact counterpart rows. No auth and no order entry.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx

from src.gemini_mlb_prop_terms_probe import _fetch_terms, _terms_url

GEMINI = "https://api.gemini.com"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
TARGETS = (
    ("GEMI-NCAAF-2609052300-IDST-USU-M-IDST", "KXNCAAFGAME-26SEP05IDSTUSU-IDST"),
    ("GEMI-NCAAF-2609051930-TULN-DUK-M-TULN", "KXNCAAFGAME-26SEP05TULNDUKE-TULN"),
    ("GEMI-NCAAF-2609052300-ARST-MEM-M-ARST", "KXNCAAFGAME-26SEP05ARSTMEM-ARST"),
)


def compact(value: Any, limit: int = 16000) -> Any:
    if isinstance(value, str):
        return " ".join(value.split())[:limit]
    return value


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gr = await http.get(f"{GEMINI}/v1/prediction-markets/events", params={
            "status": "active", "sport": "american_football", "limit": 500, "offset": 0,
        })
        gr.raise_for_status()
        gevents = [x for x in (gr.json().get("data") or gr.json().get("events") or []) if isinstance(x, Mapping)]
        g_event_by_symbol: dict[str, Mapping[str, Any]] = {}
        g_contract_by_symbol: dict[str, Mapping[str, Any]] = {}
        for event in gevents:
            for contract in event.get("contracts") or []:
                if not isinstance(contract, Mapping):
                    continue
                symbol = str(contract.get("instrumentSymbol") or contract.get("ticker") or "")
                if symbol:
                    g_event_by_symbol[symbol] = event
                    g_contract_by_symbol[symbol] = contract

        kr = await http.get(f"{KALSHI}/events", params={
            "status": "open", "series_ticker": "KXNCAAFGAME", "limit": 200,
            "with_nested_markets": "true",
        })
        kr.raise_for_status()
        k_market_by_ticker: dict[str, Mapping[str, Any]] = {}
        for event in kr.json().get("events") or []:
            if not isinstance(event, Mapping):
                continue
            for market in event.get("markets") or []:
                if isinstance(market, Mapping) and market.get("ticker"):
                    k_market_by_ticker[str(market.get("ticker"))] = market

        urls = sorted({
            _terms_url(g_contract_by_symbol.get(gsym, {}))
            for gsym, _ in TARGETS
            if _terms_url(g_contract_by_symbol.get(gsym, {}))
        })
        terms_rows = await asyncio.gather(*(_fetch_terms(http, url) for url in urls))
        terms_by_url = dict(zip(urls, terms_rows))

    for gsym, kticker in TARGETS:
        event = g_event_by_symbol.get(gsym, {})
        contract = g_contract_by_symbol.get(gsym, {})
        market = k_market_by_ticker.get(kticker, {})
        url = _terms_url(contract)
        terms = dict(terms_by_url.get(url, {"status": "NO_TERMS_URL"}))
        full_text = terms.pop("pdf_full_text", None)
        print(json.dumps({
            "status": "SPORTS_NCAAF_RULE_FINGERPRINT",
            "gemini_symbol": gsym,
            "kalshi_ticker": kticker,
            "gemini_event_ticker": event.get("ticker") or event.get("eventTicker"),
            "gemini_event_title": event.get("title"),
            "gemini_event_description": compact(event.get("description")),
            "gemini_sports_market": event.get("sportsMarket"),
            "gemini_contract_label": contract.get("label") or contract.get("title"),
            "gemini_contract_description": compact(contract.get("description")),
            "gemini_terms_url": url,
            "gemini_terms_fetch": terms,
            "kalshi_title": market.get("title"),
            "kalshi_rules_primary": compact(market.get("rules_primary")),
            "kalshi_rules_secondary": compact(market.get("rules_secondary")),
            "kalshi_expected_expiration_time": market.get("expected_expiration_time"),
            "kalshi_latest_expiration_time": market.get("latest_expiration_time"),
            "kalshi_settlement_timer_seconds": market.get("settlement_timer_seconds"),
            "kalshi_can_close_early": market.get("can_close_early"),
            "read_only": True,
            "order_entry": False,
        }, separators=(",",":"), default=str), flush=True)
        if full_text:
            print(json.dumps({
                "status": "SPORTS_NCAAF_GEMINI_TERMS_TEXT",
                "gemini_symbol": gsym,
                "gemini_terms_url": url,
                "pdf_full_text": compact(full_text),
                "read_only": True,
                "order_entry": False,
            }, separators=(",",":"), default=str), flush=True)

    print(json.dumps({
        "status": "SPORTS_NCAAF_RULE_FINGERPRINT_SUMMARY",
        "targets": len(TARGETS),
        "unique_gemini_terms_urls": len(urls),
        "gemini_contracts_found": sum(1 for gsym, _ in TARGETS if gsym in g_contract_by_symbol),
        "kalshi_markets_found": sum(1 for _, kticker in TARGETS if kticker in k_market_by_ticker),
        "read_only": True,
        "order_entry": False,
    }, separators=(",",":")), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
