"""Read-only weather forecast ensemble and bracket fair-value helpers.

Uses public Open-Meteo provider endpoints only. No credentials, account access,
or order-entry surface exists in this module. Forecast-derived probabilities are
research/shadow values until settlement rules and station mappings are certified.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

CITY_CONFIG = {
    "KXHIGHNY": {"city": "New York City", "lat": 40.7829, "lon": -73.9654, "timezone": "America/New_York", "station": "KNYC"},
    "KXHIGHCHI": {"city": "Chicago", "lat": 41.7868, "lon": -87.7522, "timezone": "America/Chicago", "station": "KMDW"},
    "KXHIGHMIA": {"city": "Miami", "lat": 25.7959, "lon": -80.2870, "timezone": "America/New_York", "station": "KMIA"},
    "KXHIGHAUS": {"city": "Austin", "lat": 30.1945, "lon": -97.6699, "timezone": "America/Chicago", "station": "KAUS"},
    "KXHIGHLA": {"city": "Los Angeles", "lat": 33.9425, "lon": -118.4081, "timezone": "America/Los_Angeles", "station": "KLAX"},
}

# Provider-specific public endpoints. Each defaults to that provider's seamless/best
# model family and keeps model failures independent of one another.
MODEL_ENDPOINTS = {
    "gfs": "https://api.open-meteo.com/v1/gfs",
    "ecmwf": "https://api.open-meteo.com/v1/ecmwf",
    "icon": "https://api.open-meteo.com/v1/dwd-icon",
}

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
    """Parse Kalshi integer-temperature bracket text with 0.5°F continuity bounds."""
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


def _fetch_provider_high(endpoint: str, cfg: dict[str, Any], target: date) -> float | None:
    params = urllib.parse.urlencode({
        "latitude": cfg["lat"],
        "longitude": cfg["lon"],
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": cfg["timezone"],
        "forecast_days": 3,
    })
    req = urllib.request.Request(f"{endpoint}?{params}", headers={"User-Agent": "kalshi-arb-weather-shadow/1.0"})
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = json.load(response)
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    target_s = target.isoformat()
    for i, day in enumerate(times):
        if day == target_s and i < len(highs) and highs[i] is not None:
            return float(highs[i])
    return None


def fetch_model_highs(series_ticker: str, target: date) -> dict[str, float]:
    cfg = CITY_CONFIG[series_ticker]
    out: dict[str, float] = {}
    for model, endpoint in MODEL_ENDPOINTS.items():
        try:
            value = _fetch_provider_high(endpoint, cfg, target)
        except Exception:
            value = None
        if value is not None:
            out[model] = round(value, 2)
    return out


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
    if len(values) == 1:
        disagreement = 0.0
    else:
        disagreement = math.sqrt(sum((x - mean) ** 2 for x in values) / len(values))
    base_std = math.sqrt(disagreement ** 2 + (historical_mae_f * 0.5) ** 2)
    total_std = max(1.5, math.sqrt(base_std ** 2 + uncertainty_buffer_f ** 2))
    return mean, total_std


def bracket_probability(mean_f: float, std_f: float, bracket: tuple[float | None, float | None]) -> float:
    low, high = bracket
    if low is None and high is None:
        return 1.0
    if low is None:
        return normal_cdf(float(high), mean_f, std_f)
    if high is None:
        return 1.0 - normal_cdf(float(low), mean_f, std_f)
    return max(0.0, normal_cdf(high, mean_f, std_f) - normal_cdf(low, mean_f, std_f))
