"""Read-only weather forecast ensemble and bracket fair-value helpers.

Uses public Open-Meteo and NWS endpoints only. No credentials, account access,
or order-entry surface exists in this module. Forecast-derived probabilities are
research/shadow values until settlement rules and station mappings are certified.

Kalshi daily-temperature contracts use local standard time. We therefore construct
forecast and observation windows in fixed standard time rather than civil/DST days.
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

CITY_CONFIG = {
    "KXHIGHNY": {"city": "New York City", "lat": 40.7829, "lon": -73.9654, "timezone": "America/New_York", "station": "KNYC", "standard_utc_offset_hours": -5},
    "KXHIGHCHI": {"city": "Chicago", "lat": 41.7868, "lon": -87.7522, "timezone": "America/Chicago", "station": "KMDW", "standard_utc_offset_hours": -6},
    "KXHIGHMIA": {"city": "Miami", "lat": 25.7959, "lon": -80.2870, "timezone": "America/New_York", "station": "KMIA", "standard_utc_offset_hours": -5},
    "KXHIGHAUS": {"city": "Austin", "lat": 30.1945, "lon": -97.6699, "timezone": "America/Chicago", "station": "KAUS", "standard_utc_offset_hours": -6},
    "KXHIGHLA": {"city": "Los Angeles", "lat": 33.9425, "lon": -118.4081, "timezone": "America/Los_Angeles", "station": "KLAX", "standard_utc_offset_hours": -8},
}
MODEL_ENDPOINTS = {
    "gfs": "https://api.open-meteo.com/v1/gfs",
    "ecmwf": "https://api.open-meteo.com/v1/ecmwf",
    "icon": "https://api.open-meteo.com/v1/dwd-icon",
}
NWS_BASE = "https://api.weather.gov"
DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)")
MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
BOUNDED_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°")
ABOVE_RE = re.compile(r">\s*(-?\d+(?:\.\d+)?)\s*°")
BELOW_RE = re.compile(r"<\s*(-?\d+(?:\.\d+)?)\s*°")


def event_date(event_ticker: str | None) -> date | None:
    if not event_ticker:
        return None
    m = DATE_RE.search(event_ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    month = MONTHS.get(mon)
    if month is None:
        return None
    try:
        return date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None


def parse_bracket(title: str | None) -> tuple[float | None, float | None] | None:
    if not title:
        return None
    m = BOUNDED_RE.search(title)
    if m:
        low, high = map(float, m.groups())
        return low - 0.5, high + 0.5
    m = ABOVE_RE.search(title)
    if m:
        return float(m.group(1)) + 0.5, None
    m = BELOW_RE.search(title)
    if m:
        return None, float(m.group(1)) - 0.5
    return None


def climate_window_utc(series_ticker: str, target: date) -> tuple[datetime, datetime]:
    cfg = CITY_CONFIG[series_ticker]
    offset_h = int(cfg["standard_utc_offset_hours"])
    local_standard_midnight = datetime.combine(target, time.min)
    start = (local_standard_midnight - timedelta(hours=offset_h)).replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def local_calendar_date(series_ticker: str, observed_at: datetime | None = None) -> date:
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(CITY_CONFIG[series_ticker]["timezone"])).date()


def model_eligibility(series_ticker: str, target: date | None, observed_at: datetime | None = None) -> str:
    if target is None:
        return "NO_TARGET_DATE"
    local_day = local_calendar_date(series_ticker, observed_at)
    if target < local_day:
        return "PAST_EVENT_RESEARCH_ONLY"
    if target == local_day:
        return "INTRADAY_OBSERVATION_CONDITIONED"
    return "FORECAST_ONLY_RESEARCH"


def _parse_utc_hour(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _fetch_provider_high(endpoint: str, cfg: dict[str, Any], series_ticker: str, target: date) -> float | None:
    start, end = climate_window_utc(series_ticker, target)
    params = urllib.parse.urlencode({
        "latitude": cfg["lat"], "longitude": cfg["lon"], "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit", "timezone": "UTC",
        "start_date": start.date().isoformat(), "end_date": end.date().isoformat(),
    })
    req = urllib.request.Request(f"{endpoint}?{params}", headers={"User-Agent": "kalshi-arb-weather-shadow/1.0"})
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = json.load(response)
    hourly = payload.get("hourly") or {}
    times, temps = hourly.get("time") or [], hourly.get("temperature_2m") or []
    vals: list[float] = []
    for i, raw_ts in enumerate(times):
        if i >= len(temps) or temps[i] is None:
            continue
        try:
            ts = _parse_utc_hour(str(raw_ts))
        except ValueError:
            continue
        if start <= ts < end:
            vals.append(float(temps[i]))
    return max(vals) if vals else None


def fetch_model_highs(series_ticker: str, target: date) -> dict[str, float]:
    cfg = CITY_CONFIG[series_ticker]
    out: dict[str, float] = {}
    for model, endpoint in MODEL_ENDPOINTS.items():
        try:
            value = _fetch_provider_high(endpoint, cfg, series_ticker, target)
        except Exception:
            value = None
        if value is not None:
            out[model] = round(value, 2)
    return out


def fetch_observed_running_high(series_ticker: str, target: date, observed_at: datetime | None = None) -> float | None:
    """Highest NWS station observation so far inside the standard-time event window."""
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    start, end = climate_window_utc(series_ticker, target)
    stop = min(end, now)
    if stop <= start:
        return None
    station = CITY_CONFIG[series_ticker]["station"]
    params = urllib.parse.urlencode({"start": start.isoformat().replace("+00:00", "Z"), "end": stop.isoformat().replace("+00:00", "Z"), "limit": 500})
    req = urllib.request.Request(f"{NWS_BASE}/stations/{station}/observations?{params}", headers={"User-Agent": "kalshi-arb-weather-shadow/1.0 (research)"})
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = json.load(response)
    highs: list[float] = []
    for feature in payload.get("features") or []:
        c = (((feature or {}).get("properties") or {}).get("temperature") or {}).get("value")
        if c is None:
            continue
        try:
            highs.append(float(c) * 9.0 / 5.0 + 32.0)
        except (TypeError, ValueError):
            continue
    return round(max(highs), 2) if highs else None


def normal_cdf(x: float, mean: float, std: float) -> float:
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / (std * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def forecast_distribution(model_highs: dict[str, float], historical_mae_f: float = 3.0, uncertainty_buffer_f: float = 1.5) -> tuple[float, float] | None:
    values = list(model_highs.values())
    if not values:
        return None
    mean = sum(values) / len(values)
    disagreement = 0.0 if len(values) == 1 else math.sqrt(sum((x - mean) ** 2 for x in values) / len(values))
    base_std = math.sqrt(disagreement ** 2 + (historical_mae_f * 0.5) ** 2)
    return mean, max(1.5, math.sqrt(base_std ** 2 + uncertainty_buffer_f ** 2))


def bracket_probability(mean_f: float, std_f: float, bracket: tuple[float | None, float | None], observed_high_f: float | None = None) -> float:
    """Probability of a final daily maximum bracket, optionally conditioned on running high."""
    low, high = bracket
    floor = observed_high_f
    denominator = 1.0
    if floor is not None:
        denominator = max(1e-9, 1.0 - normal_cdf(floor, mean_f, std_f))
    effective_low = low
    if floor is not None:
        effective_low = floor if effective_low is None else max(effective_low, floor)
    if high is not None and effective_low is not None and effective_low >= high:
        return 0.0
    if effective_low is None and high is None:
        numerator = 1.0
    elif effective_low is None:
        numerator = normal_cdf(float(high), mean_f, std_f)
    elif high is None:
        numerator = 1.0 - normal_cdf(float(effective_low), mean_f, std_f)
    else:
        numerator = max(0.0, normal_cdf(float(high), mean_f, std_f) - normal_cdf(float(effective_low), mean_f, std_f))
    return min(1.0, max(0.0, numerator / denominator)) if floor is not None else numerator
