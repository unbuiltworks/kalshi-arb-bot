"""Gemini-only public MLB player-prop lifecycle fingerprint.

This one-shot audit intentionally does not call Kalshi.  The paired Kalshi rule
profiles are already fingerprinted in ``mlb_prop_rule_certifications``; the open
question for G/K graduation is whether Gemini's current public contract terms fully
specify participation, official source, interruptions/cancellation, corrections,
and threshold settlement.  Removing Kalshi discovery also prevents an unrelated
429 from blocking the Gemini terms audit.

No authentication and no order entry.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
from typing import Any, Mapping

import httpx

from src.gemini_mlb_prop_terms_probe import _fetch_terms, _terms_url
from src.mlb_game_side_normalization import normalize_gemini_mlb
from src.universal_three_venue_sports import fetch_gemini_mlb

TARGET_STATS = ("hits", "total_bases", "strikeouts")
STAT_TO_FAMILY = {
    "hits": "baseball_player_hits",
    "total_bases": "baseball_player_total_bases",
    "strikeouts": "baseball_player_strikeouts",
}
MAX_PER_STAT = 1


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events = await fetch_gemini_mlb(http)
        normalized = [
            side
            for side in normalize_gemini_mlb(gemini_events)
            if side.identity.market_family == "player_prop"
            and side.identity.stat in TARGET_STATS
        ]

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

        by_stat: dict[str, list[Any]] = defaultdict(list)
        for side in normalized:
            if side.venue_market_id not in [x.venue_market_id for x in by_stat[side.identity.stat]]:
                by_stat[side.identity.stat].append(side)

        selected = [side for stat in TARGET_STATS for side in by_stat.get(stat, [])[:MAX_PER_STAT]]
        urls = sorted(
            {
                _terms_url(raw_contract_by_symbol.get(side.venue_market_id, {}))
                for side in selected
                if _terms_url(raw_contract_by_symbol.get(side.venue_market_id, {}))
            }
        )
        terms_rows = await asyncio.gather(*(_fetch_terms(http, url) for url in urls))
        terms_by_url = dict(zip(urls, terms_rows))

    for side in selected:
        symbol = side.venue_market_id
        contract = raw_contract_by_symbol.get(symbol, {})
        event = raw_event_by_symbol.get(symbol, {})
        url = _terms_url(contract)
        terms = dict(terms_by_url.get(url, {"status": "NO_TERMS_URL"}))
        full_text = terms.pop("pdf_full_text", None)
        print(
            json.dumps(
                {
                    "status": "GEMINI_MLB_PROP_TERMS_FINGERPRINT",
                    "selection_source": "GEMINI_FAMILY_AUDIT_ONLY",
                    "symbol": symbol,
                    "family": STAT_TO_FAMILY.get(side.identity.stat),
                    "stat": side.identity.stat,
                    "player": side.identity.subject,
                    "threshold": None if side.identity.line is None else str(side.identity.line),
                    "event_ticker": event.get("ticker") or event.get("eventTicker"),
                    "event_title": event.get("title"),
                    "event_description": event.get("description"),
                    "contract_label": contract.get("label") or contract.get("title"),
                    "contract_description": contract.get("description"),
                    "strike": contract.get("strike"),
                    "terms_url": url,
                    "terms_fetch": terms,
                    "contract_keys": sorted(str(key) for key in contract.keys()),
                    "read_only": True,
                    "order_entry": False,
                },
                separators=(",", ":"),
                default=str,
            ),
            flush=True,
        )
        if full_text:
            print(
                json.dumps(
                    {
                        "status": "GEMINI_MLB_PROP_TERMS_PDF_TEXT",
                        "symbol": symbol,
                        "terms_url": url,
                        "pdf_full_text": full_text,
                        "read_only": True,
                        "order_entry": False,
                    },
                    separators=(",", ":"),
                    default=str,
                ),
                flush=True,
            )

    print(
        json.dumps(
            {
                "status": "GEMINI_MLB_PROP_TERMS_PROBE_SUMMARY",
                "selected_contracts": len(selected),
                "current_gemini_family_inventory": {
                    stat: len(by_stat.get(stat, [])) for stat in TARGET_STATS
                },
                "unique_terms_urls": len(urls),
                "terms_fetch_statuses": {
                    status: sum(1 for row in terms_rows if row.get("status") == status)
                    for status in sorted({str(row.get("status")) for row in terms_rows})
                },
                "pdf_parse_statuses": sorted(
                    {
                        str(row.get("pdf_parse_status"))
                        for row in terms_rows
                        if row.get("pdf_parse_status")
                    }
                ),
                "kalshi_called": False,
                "read_only": True,
                "order_entry": False,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
