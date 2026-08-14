"""Read-only settlement-rule audit for any Polymarket US / Kalshi candidate pair.

This is the gate between fuzzy overlap discovery and an explicit pair whitelist.
It fetches both contracts' metadata and resolution fields side by side. It has no
order-entry code and makes no claim that the contracts are equivalent.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

POLY = "https://gateway.polymarket.us"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"


def _pick(obj: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: obj.get(key) for key in keys if key in obj}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--poly-slug", required=True)
    p.add_argument("--kalshi-ticker", required=True)
    args = p.parse_args()

    with httpx.Client(timeout=20.0) as http:
        pr = http.get(f"{POLY}/v1/markets/{args.poly_slug}")
        pr.raise_for_status()
        poly_body = pr.json()
        poly = poly_body.get("market", poly_body) if isinstance(poly_body, dict) else {}

        kr = http.get(f"{KALSHI}/markets/{args.kalshi_ticker}")
        kr.raise_for_status()
        km = kr.json().get("market", {})
        event_ticker = str(km.get("event_ticker") or "")
        er = http.get(f"{KALSHI}/events/{event_ticker}")
        er.raise_for_status()
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
            "line", "feeCoefficient", "subject", "tags",
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
