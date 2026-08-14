"""Read-only settlement-rule audit for any Polymarket US / Kalshi candidate pair.

This is the gate between fuzzy overlap discovery and an explicit pair whitelist.
It fetches both contracts' metadata and resolution fields side by side. It has no
order-entry code and makes no claim that the contracts are equivalent.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx

POLY = "https://gateway.polymarket.us"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"


def _pick(obj: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: obj.get(key) for key in keys if key in obj}


def _get(http: httpx.Client, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
    delay = 1.0
    for _ in range(6):
        response = http.get(url, params=params)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("retry-after")
        try:
            wait_s = max(delay, float(retry_after or 0))
        except ValueError:
            wait_s = delay
        time.sleep(wait_s)
        delay = min(delay * 2, 15.0)
    raise RuntimeError(f"Repeated rate limit fetching {url}")


def _find_poly_market(http: httpx.Client, slug: str) -> dict[str, Any]:
    # Polymarket US exposes generic market discovery through the paginated listing;
    # sports additionally have league event endpoints, but this auditor must work
    # across politics, culture, macro, climate, crypto, etc.
    page_size = 500
    for offset in range(0, 50_000, page_size):
        response = _get(
            http,
            f"{POLY}/v1/markets",
            params={
                "limit": page_size,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "archived": "false",
            },
        )
        rows = response.json().get("markets", [])
        for market in rows:
            if str(market.get("slug") or "") == slug:
                return market
        if len(rows) < page_size:
            break
    raise SystemExit(f"Polymarket active market slug not found: {slug}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--poly-slug", required=True)
    p.add_argument("--kalshi-ticker", required=True)
    args = p.parse_args()

    with httpx.Client(timeout=20.0) as http:
        poly = _find_poly_market(http, args.poly_slug)

        kr = _get(http, f"{KALSHI}/markets/{args.kalshi_ticker}")
        km = kr.json().get("market", {})
        event_ticker = str(km.get("event_ticker") or "")
        er = _get(http, f"{KALSHI}/events/{event_ticker}")
        ke = er.json().get("event", {})

        series = {}
        series_ticker = str(ke.get("series_ticker") or "")
        if series_ticker:
            sr = http.get(f"{KALSHI}/series/{series_ticker}")
            if sr.status_code == 200:
                series = sr.json().get("series", {})

    out = {
        "status": "AUDIT_ONLY_NOT_VERIFIED",
        "polymarket": _pick(poly, (
            "id", "slug", "ticker", "category", "question", "title", "subtitle",
            "description", "rules", "resolutionRules", "resolutionSource",
            "settlementSource", "startDate", "endDate", "eventDate", "startTime",
            "active", "closed", "archived", "marketType", "sportsMarketType",
            "line", "feeCoefficient", "subject", "tags", "outcomes", "outcomePrices",
            "volume", "volumeNum", "liquidity", "liquidityNum",
        )),
        "kalshi": {
            "market": _pick(km, (
                "ticker", "event_ticker", "title", "subtitle", "yes_sub_title",
                "no_sub_title", "status", "strike_type", "floor_strike", "cap_strike",
                "functional_strike", "occurrence_datetime", "open_time", "close_time",
                "expected_expiration_time", "expiration_time", "rules_primary",
                "rules_secondary", "early_close_condition", "settlement_timer_seconds",
                "primary_participant_key", "is_provisional",
            )),
            "event": _pick(ke, (
                "event_ticker", "series_ticker", "title", "sub_title", "category",
                "mutually_exclusive", "strike_date", "strike_period", "product_metadata",
                "settlement_sources", "fee_type_override", "fee_multiplier_override",
            )),
            "series": _pick(series, (
                "ticker", "title", "category", "frequency", "fee_type", "fee_multiplier",
                "additional_prohibitions", "settlement_sources", "contract_url",
            )),
        },
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
