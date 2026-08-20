"""Read-only Kalshi weather market scanner.

This module intentionally contains no authentication, order placement, cancellation,
or live-arm path. It fetches public market data and normalizes it into shadow rows
for later settlement/rules certification and dashboard ingestion.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from src.fee_calc import taker_fee_cents

KALSHI_PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Legacy weather-bot knowledge, treated only as discovery hints. Each series still
# requires independent settlement certification before any live trade may occur.
WEATHER_SERIES = {
    "KXHIGHNY": "New York City",
    "KXHIGHCHI": "Chicago",
    "KXHIGHMIA": "Miami",
    "KXHIGHAUS": "Austin",
    "KXHIGHLA": "Los Angeles",
}

SETTLEMENT_STATUS = "SETTLEMENT_AUDIT_REQUIRED"
TRADE_CLASSIFICATION = "SHADOW"
CATEGORY = "weather"


def _to_cents(value: Any) -> int | None:
    """Normalize current dollar-string or legacy integer cent fields."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return int(round(float(value) * 100))
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        # Legacy yes_ask/no_ask values are cents. Decimal numeric values <=1 are
        # treated as dollars only when non-integral.
        if isinstance(value, float) and 0 <= value <= 1 and not value.is_integer():
            return int(round(value * 100))
        return int(round(value))
    return None


def _first(market: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in market and market[key] not in (None, ""):
            return market[key]
    return None


@dataclass(frozen=True)
class WeatherShadowRow:
    observed_at: str
    category: str
    classification: str
    settlement_status: str
    series_ticker: str
    city: str
    event_ticker: str | None
    market_ticker: str
    title: str | None
    subtitle: str | None
    close_time: str | None
    yes_bid_cents: int | None
    yes_ask_cents: int | None
    no_bid_cents: int | None
    no_ask_cents: int | None
    volume: float | None
    open_interest: float | None
    yes_depth_hint: float | None
    no_depth_hint: float | None
    q1_yes_taker_fee_cents: int | None
    q1_no_taker_fee_cents: int | None
    source: str = "kalshi_public_api"


def build_shadow_row(series_ticker: str, market: dict[str, Any], observed_at: str | None = None) -> WeatherShadowRow:
    ticker = str(market.get("ticker") or "")
    yes_bid = _to_cents(_first(market, "yes_bid_dollars", "yes_bid"))
    yes_ask = _to_cents(_first(market, "yes_ask_dollars", "yes_ask"))
    no_bid = _to_cents(_first(market, "no_bid_dollars", "no_bid"))
    no_ask = _to_cents(_first(market, "no_ask_dollars", "no_ask"))

    def num(*keys: str) -> float | None:
        raw = _first(market, *keys)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    return WeatherShadowRow(
        observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
        category=CATEGORY,
        classification=TRADE_CLASSIFICATION,
        settlement_status=SETTLEMENT_STATUS,
        series_ticker=series_ticker,
        city=WEATHER_SERIES.get(series_ticker, series_ticker),
        event_ticker=market.get("event_ticker"),
        market_ticker=ticker,
        title=market.get("title"),
        subtitle=market.get("subtitle"),
        close_time=market.get("close_time"),
        yes_bid_cents=yes_bid,
        yes_ask_cents=yes_ask,
        no_bid_cents=no_bid,
        no_ask_cents=no_ask,
        volume=num("volume_fp", "volume"),
        open_interest=num("open_interest_fp", "open_interest"),
        # Public market payloads do not expose full book depth. These are kept
        # as nullable hints until a dedicated read-only orderbook fetch is added.
        yes_depth_hint=num("yes_ask_size_fp", "yes_ask_size"),
        no_depth_hint=num("no_ask_size_fp", "no_ask_size"),
        q1_yes_taker_fee_cents=taker_fee_cents(1, yes_ask, ticker) if yes_ask is not None else None,
        q1_no_taker_fee_cents=taker_fee_cents(1, no_ask, ticker) if no_ask is not None else None,
    )


def fetch_series_markets(series_ticker: str, status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"series_ticker": series_ticker, "status": status, "limit": limit})
    url = f"{KALSHI_PUBLIC_BASE}/markets?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-weather-shadow/1.0"})
    with urllib.request.urlopen(req, timeout=10) as response:  # no auth, read-only GET
        payload = json.load(response)
    markets = payload.get("markets", [])
    return markets if isinstance(markets, list) else []


def scan_weather(series: Iterable[str] = WEATHER_SERIES) -> list[WeatherShadowRow]:
    observed_at = datetime.now(timezone.utc).isoformat()
    rows: list[WeatherShadowRow] = []
    for series_ticker in series:
        for market in fetch_series_markets(series_ticker):
            rows.append(build_shadow_row(series_ticker, market, observed_at=observed_at))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Kalshi weather shadow scanner")
    parser.add_argument("--series", action="append", choices=sorted(WEATHER_SERIES), help="limit scan to one or more series")
    parser.add_argument("--jsonl", help="optional JSONL output path")
    args = parser.parse_args()

    rows = scan_weather(args.series or WEATHER_SERIES.keys())
    encoded = [asdict(row) for row in rows]
    for row in encoded:
        print(json.dumps(row, sort_keys=True))

    if args.jsonl:
        with open(args.jsonl, "a", encoding="utf-8") as fh:
            for row in encoded:
                fh.write(json.dumps(row, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
