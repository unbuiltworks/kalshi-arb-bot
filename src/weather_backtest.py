"""Rule-aware weather forecast backtest using public settled Kalshi outcomes.

This is research-only. It reconstructs a conservative archived forecast snapshot and
scores the existing bracket-probability model against Kalshi's finalized YES/NO
outcomes. Crucially, rows are grouped by the settlement source encoded in the market
rules so NWS-era contracts are never mixed silently with current TWC-era contracts.

No authentication, account access, or order entry exists in this module.
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from src.weather_forecast import (
    CITY_CONFIG,
    bracket_probability,
    climate_window_utc,
    event_date,
    forecast_distribution,
    parse_bracket,
)
from src.weather_shadow import WEATHER_SERIES, settlement_identity

KALSHI_PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SINGLE_RUN_BASE = "https://single-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_MODELS = {
    "gfs": "gfs_seamless",
    "ecmwf": "ecmwf_ifs025",
    "icon": "icon_seamless",
}


@dataclass(frozen=True)
class BacktestRow:
    event_date: str
    series_ticker: str
    city: str
    market_ticker: str
    title: str | None
    settlement_source: str
    settlement_location_key: str | None
    result: str
    decision_time_utc: str
    run_time_utc: str
    model_highs_f: dict[str, float]
    forecast_mean_f: float
    forecast_std_f: float
    predicted_yes_probability: float
    actual_yes: int
    brier: float
    log_loss: float


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-weather-backtest/1.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)
    return payload if isinstance(payload, dict) else {}


def fetch_settled_markets(series_ticker: str, min_settled_ts: int | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    """Fetch recent finalized markets, following Kalshi cursor pagination."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": "settled",
            "limit": max(1, min(1000, int(limit))),
        }
        if min_settled_ts is not None:
            params["min_settled_ts"] = int(min_settled_ts)
        if cursor:
            params["cursor"] = cursor
        payload = _get_json(f"{KALSHI_PUBLIC_BASE}/markets?{urllib.parse.urlencode(params)}")
        markets = payload.get("markets") or []
        if isinstance(markets, list):
            out.extend(m for m in markets if isinstance(m, dict))
        next_cursor = payload.get("cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = str(next_cursor)
    return out


def conservative_run_time(decision_time: datetime, availability_lag_hours: int = 6) -> datetime:
    """Latest 6-hour global-model run conservatively available by decision time."""
    dt = decision_time.astimezone(timezone.utc) - timedelta(hours=max(0, availability_lag_hours))
    hour = (dt.hour // 6) * 6
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


def decision_time_for(target: date, decision_hour_utc: int = 19) -> datetime:
    """Decision snapshot on the UTC day immediately before the contract's target date."""
    previous = target - timedelta(days=1)
    return datetime.combine(previous, time(hour=max(0, min(23, decision_hour_utc))), tzinfo=timezone.utc)


def _archived_model_high(series_ticker: str, target: date, run_time: datetime, model: str) -> float | None:
    cfg = CITY_CONFIG[series_ticker]
    start, end = climate_window_utc(series_ticker, target)
    # The run horizon is relative to initialization; request enough days to include target.
    horizon_days = max(2, min(10, (end.date() - run_time.date()).days + 2))
    params = urllib.parse.urlencode({
        "latitude": cfg["lat"],
        "longitude": cfg["lon"],
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "UTC",
        "models": model,
        "run": run_time.strftime("%Y-%m-%dT%H:%M"),
        "forecast_days": horizon_days,
    })
    payload = _get_json(f"{SINGLE_RUN_BASE}?{params}")
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    vals: list[float] = []
    for i, raw_ts in enumerate(times):
        if i >= len(temps) or temps[i] is None:
            continue
        try:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts = ts.astimezone(timezone.utc)
            value = float(temps[i])
        except (TypeError, ValueError):
            continue
        if start <= ts < end:
            vals.append(value)
    return max(vals) if vals else None


def fetch_archived_model_highs(series_ticker: str, target: date, run_time: datetime) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, model in ARCHIVE_MODELS.items():
        try:
            value = _archived_model_high(series_ticker, target, run_time, model)
        except Exception:
            value = None
        if value is not None:
            out[name] = round(float(value), 2)
    return out


def _log_loss(probability: float, actual: int) -> float:
    p = min(1.0 - 1e-9, max(1e-9, float(probability)))
    return -(actual * math.log(p) + (1 - actual) * math.log(1.0 - p))


def build_backtest_row(
    series_ticker: str,
    market: dict[str, Any],
    model_highs: dict[str, float],
    decision_time: datetime,
    run_time: datetime,
) -> BacktestRow | None:
    target = event_date(market.get("event_ticker"))
    bracket = parse_bracket(market.get("title"))
    result = str(market.get("result") or "").lower()
    if target is None or bracket is None or result not in {"yes", "no"}:
        return None
    dist = forecast_distribution(model_highs)
    if dist is None:
        return None
    mean_f, std_f = dist
    probability = bracket_probability(mean_f, std_f, bracket)
    actual = 1 if result == "yes" else 0
    source, location_key, _status = settlement_identity(market)
    return BacktestRow(
        event_date=target.isoformat(),
        series_ticker=series_ticker,
        city=WEATHER_SERIES.get(series_ticker, series_ticker),
        market_ticker=str(market.get("ticker") or ""),
        title=market.get("title"),
        settlement_source=source,
        settlement_location_key=location_key,
        result=result,
        decision_time_utc=decision_time.isoformat(),
        run_time_utc=run_time.isoformat(),
        model_highs_f=dict(model_highs),
        forecast_mean_f=round(mean_f, 4),
        forecast_std_f=round(std_f, 4),
        predicted_yes_probability=round(probability, 8),
        actual_yes=actual,
        brier=round((probability - actual) ** 2, 8),
        log_loss=round(_log_loss(probability, actual), 8),
    )


def backtest(
    series: Iterable[str] = WEATHER_SERIES,
    days: int = 30,
    decision_hour_utc: int = 19,
    availability_lag_hours: int = 6,
) -> list[BacktestRow]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    min_settled_ts = int(cutoff.timestamp())
    rows: list[BacktestRow] = []
    forecast_cache: dict[tuple[str, str, str], dict[str, float]] = {}
    for series_ticker in series:
        if series_ticker not in CITY_CONFIG:
            continue
        for market in fetch_settled_markets(series_ticker, min_settled_ts=min_settled_ts):
            target = event_date(market.get("event_ticker"))
            if target is None:
                continue
            decision_time = decision_time_for(target, decision_hour_utc)
            run_time = conservative_run_time(decision_time, availability_lag_hours)
            key = (series_ticker, target.isoformat(), run_time.isoformat())
            if key not in forecast_cache:
                forecast_cache[key] = fetch_archived_model_highs(series_ticker, target, run_time)
            row = build_backtest_row(series_ticker, market, forecast_cache[key], decision_time, run_time)
            if row is not None:
                rows.append(row)
    return rows


def summarize(rows: Iterable[BacktestRow]) -> dict[str, Any]:
    buckets: dict[tuple[str, str], list[BacktestRow]] = defaultdict(list)
    all_rows = list(rows)
    for row in all_rows:
        buckets[(row.settlement_source, row.series_ticker)].append(row)

    def metrics(items: list[BacktestRow]) -> dict[str, Any]:
        n = len(items)
        if not n:
            return {"n": 0}
        return {
            "n": n,
            "mean_brier": round(sum(r.brier for r in items) / n, 6),
            "mean_log_loss": round(sum(r.log_loss for r in items) / n, 6),
            "yes_rate": round(sum(r.actual_yes for r in items) / n, 6),
            "mean_predicted_yes": round(sum(r.predicted_yes_probability for r in items) / n, 6),
        }

    return {
        "classification": "RESEARCH_BACKTEST",
        "order_entry": False,
        "rows": len(all_rows),
        "overall": metrics(all_rows),
        "by_settlement_source_and_series": {
            f"{source}:{series}": metrics(items)
            for (source, series), items in sorted(buckets.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rule-aware archived weather model backtest")
    parser.add_argument("--series", action="append", choices=sorted(WEATHER_SERIES))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--decision-hour-utc", type=int, default=19)
    parser.add_argument("--availability-lag-hours", type=int, default=6)
    parser.add_argument("--jsonl")
    args = parser.parse_args()
    rows = backtest(
        args.series or WEATHER_SERIES.keys(),
        days=args.days,
        decision_hour_utc=args.decision_hour_utc,
        availability_lag_hours=args.availability_lag_hours,
    )
    print(json.dumps({"event": "WEATHER_BACKTEST_SUMMARY", **summarize(rows)}, sort_keys=True))
    if args.jsonl:
        with open(args.jsonl, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(asdict(row), sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
