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
from src.weather_forecast import (
    CITY_CONFIG,
    bracket_probability,
    event_date,
    fetch_model_highs,
    forecast_distribution,
    model_eligibility,
    parse_bracket,
)

KALSHI_PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"

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
MODEL_STATUS = "MODEL_RESEARCH_ONLY"


def _to_cents(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return int(round(float(value) * 100))
        except ValueError:
            return None
    if isinstance(value, (int, float)):
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
    model_status: str
    model_eligibility: str
    series_ticker: str
    city: str
    station_hint: str | None
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
    forecast_target_date: str | None
    forecast_models: dict[str, float] | None
    forecast_mean_f: float | None
    forecast_std_f: float | None
    model_yes_probability: float | None
    model_fair_yes_cents: int | None
    q1_yes_model_ev_cents: float | None
    q1_no_model_ev_cents: float | None
    source: str = "kalshi_public_api"
    forecast_source: str = "open_meteo_public"


def _parse_observed_at(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def build_shadow_row(
    series_ticker: str,
    market: dict[str, Any],
    observed_at: str | None = None,
    forecast_context: dict[str, Any] | None = None,
) -> WeatherShadowRow:
    ticker = str(market.get("ticker") or "")
    yes_bid = _to_cents(_first(market, "yes_bid_dollars", "yes_bid"))
    yes_ask = _to_cents(_first(market, "yes_ask_dollars", "yes_ask"))
    no_bid = _to_cents(_first(market, "no_bid_dollars", "no_bid"))
    no_ask = _to_cents(_first(market, "no_ask_dollars", "no_ask"))
    yes_fee = taker_fee_cents(1, yes_ask, ticker) if yes_ask is not None else None
    no_fee = taker_fee_cents(1, no_ask, ticker) if no_ask is not None else None

    def num(*keys: str) -> float | None:
        raw = _first(market, *keys)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    observed_dt = _parse_observed_at(observed_at)
    target = event_date(market.get("event_ticker"))
    eligibility = model_eligibility(series_ticker, target, observed_dt) if series_ticker in CITY_CONFIG else "NO_CITY_CONFIG"
    bracket = parse_bracket(market.get("title"))
    model_highs = None
    mean_f = std_f = model_prob = None
    if forecast_context:
        model_highs = forecast_context.get("models") or None
        mean_f = forecast_context.get("mean_f")
        std_f = forecast_context.get("std_f")
        if mean_f is not None and std_f is not None and bracket is not None:
            model_prob = bracket_probability(float(mean_f), float(std_f), bracket)

    fair_cents = int(round(model_prob * 100)) if model_prob is not None else None
    yes_ev = no_ev = None
    # Forecast-only EV is deliberately suppressed once the local event day begins.
    # Intraday pricing must condition on the observed running high first.
    if eligibility == "FORECAST_ONLY_RESEARCH":
        if model_prob is not None and yes_ask is not None and yes_fee is not None:
            yes_ev = round(model_prob * 100.0 - yes_ask - yes_fee, 2)
        if model_prob is not None and no_ask is not None and no_fee is not None:
            no_ev = round((1.0 - model_prob) * 100.0 - no_ask - no_fee, 2)

    cfg = CITY_CONFIG.get(series_ticker, {})
    return WeatherShadowRow(
        observed_at=observed_dt.isoformat(),
        category=CATEGORY,
        classification=TRADE_CLASSIFICATION,
        settlement_status=SETTLEMENT_STATUS,
        model_status=MODEL_STATUS,
        model_eligibility=eligibility,
        series_ticker=series_ticker,
        city=WEATHER_SERIES.get(series_ticker, series_ticker),
        station_hint=cfg.get("station"),
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
        yes_depth_hint=num("yes_ask_size_fp", "yes_ask_size"),
        no_depth_hint=num("no_ask_size_fp", "no_ask_size"),
        q1_yes_taker_fee_cents=yes_fee,
        q1_no_taker_fee_cents=no_fee,
        forecast_target_date=target.isoformat() if target else None,
        forecast_models=model_highs,
        forecast_mean_f=round(float(mean_f), 2) if mean_f is not None else None,
        forecast_std_f=round(float(std_f), 2) if std_f is not None else None,
        model_yes_probability=round(float(model_prob), 6) if model_prob is not None else None,
        model_fair_yes_cents=fair_cents,
        q1_yes_model_ev_cents=yes_ev,
        q1_no_model_ev_cents=no_ev,
    )


def fetch_series_markets(series_ticker: str, status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"series_ticker": series_ticker, "status": status, "limit": limit})
    url = f"{KALSHI_PUBLIC_BASE}/markets?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-weather-shadow/1.0"})
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = json.load(response)
    markets = payload.get("markets", [])
    return markets if isinstance(markets, list) else []


def scan_weather(series: Iterable[str] = WEATHER_SERIES) -> list[WeatherShadowRow]:
    observed_at = datetime.now(timezone.utc).isoformat()
    rows: list[WeatherShadowRow] = []
    forecast_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for series_ticker in series:
        markets = fetch_series_markets(series_ticker)
        for market in markets:
            target = event_date(market.get("event_ticker"))
            context = None
            if target is not None and series_ticker in CITY_CONFIG:
                cache_key = (series_ticker, target.isoformat())
                if cache_key not in forecast_cache:
                    models = fetch_model_highs(series_ticker, target)
                    dist = forecast_distribution(models)
                    forecast_cache[cache_key] = {
                        "models": models,
                        "mean_f": dist[0] if dist else None,
                        "std_f": dist[1] if dist else None,
                    }
                context = forecast_cache[cache_key]
            rows.append(build_shadow_row(series_ticker, market, observed_at=observed_at, forecast_context=context))
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
