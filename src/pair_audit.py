"""Read-only audit for a Polymarket US / Kalshi sports-total pair.

Prints exact structured event identity, timestamps, provider IDs, and resolution
rules. It never creates, modifies, or cancels orders.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

POLY_GATEWAY = "https://gateway.polymarket.us"
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"


def compact_poly_event(event: dict[str, Any], market_slug: str) -> dict[str, Any]:
    target = next(
        (m for m in (event.get("markets") or []) if str(m.get("slug")) == market_slug),
        {},
    )
    return {
        "event": {
            "id": event.get("id"),
            "ticker": event.get("ticker"),
            "slug": event.get("slug"),
            "title": event.get("title"),
            "eventDate": event.get("eventDate"),
            "startTime": event.get("startTime"),
            "gameId": event.get("gameId"),
            "rescheduledFromGameId": event.get("rescheduledFromGameId"),
            "sportradarGameId": event.get("sportradarGameId"),
            "live": event.get("live"),
            "ended": event.get("ended"),
        },
        "market": {
            key: target.get(key)
            for key in (
                "id",
                "slug",
                "question",
                "title",
                "subtitle",
                "description",
                "line",
                "marketType",
                "sportsMarketType",
                "gameStartTime",
                "feeCoefficient",
                "spreadTotalSuffix",
                "active",
                "closed",
                "rules",
                "resolutionRules",
                "resolutionSource",
                "settlementSource",
            )
            if key in target
        },
    }


def compact_kalshi(market: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    keep_market = (
        "ticker",
        "event_ticker",
        "title",
        "subtitle",
        "yes_sub_title",
        "no_sub_title",
        "status",
        "strike_type",
        "floor_strike",
        "cap_strike",
        "functional_strike",
        "occurrence_datetime",
        "open_time",
        "close_time",
        "expected_expiration_time",
        "expiration_time",
        "rules_primary",
        "rules_secondary",
        "early_close_condition",
        "settlement_timer_seconds",
        "primary_participant_key",
        "is_provisional",
    )
    keep_event = (
        "event_ticker",
        "series_ticker",
        "title",
        "sub_title",
        "category",
        "mutually_exclusive",
        "strike_date",
        "strike_period",
        "product_metadata",
        "settlement_sources",
        "fee_type_override",
        "fee_multiplier_override",
    )
    return {
        "market": {k: market.get(k) for k in keep_market if k in market},
        "event": {k: event.get(k) for k in keep_event if k in event},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poly-slug", required=True)
    parser.add_argument("--kalshi-ticker", required=True)
    parser.add_argument("--league", default="mlb")
    args = parser.parse_args()

    with httpx.Client(timeout=15.0) as http:
        events = http.get(
            f"{POLY_GATEWAY}/v2/leagues/{args.league}/events",
            params={"limit": 1000, "active": "true", "closed": "false"},
        )
        events.raise_for_status()
        poly_event = next(
            (
                event
                for event in events.json().get("events", [])
                if any(
                    str(market.get("slug")) == args.poly_slug
                    for market in (event.get("markets") or [])
                )
            ),
            None,
        )
        if poly_event is None:
            raise SystemExit(f"Polymarket slug not found: {args.poly_slug}")

        km = http.get(f"{KALSHI_REST}/markets/{args.kalshi_ticker}")
        km.raise_for_status()
        kal_market = km.json().get("market", {})
        event_ticker = kal_market.get("event_ticker")
        ke = http.get(f"{KALSHI_REST}/events/{event_ticker}")
        ke.raise_for_status()
        kal_event = ke.json().get("event", {})

    print(json.dumps({
        "polymarket": compact_poly_event(poly_event, args.poly_slug),
        "kalshi": compact_kalshi(kal_market, kal_event),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
