"""Isolated discovery patch for the HR/TB/K live canary.

Keeps the production scanners untouched.  It adds the newer KXMLBHR Home Runs
series to the strict in-process identity tables and replaces the hot scanner's cold
Kalshi discovery call with a sequential, bounded-retry fetch of only HR/TB/K.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

import src.gemini_kalshi_mlb_prop_hot_shadow as hot
import src.gemini_kalshi_mlb_prop_shadow as discovery
import src.strict_mlb_prop_adapters as adapters

HOME_RUNS = "baseball_player_home_runs"
SELECTED_SERIES = ("KXMLBHR", "KXMLBTB", "KXMLBKS")
KALSHI = "https://external-api.kalshi.com/trade-api/v2"


def _install_home_runs() -> None:
    adapters.FAMILY_SERIES[HOME_RUNS] = "KXMLBHR"  # type: ignore[index]
    adapters.FAMILY_LABELS[HOME_RUNS] = ("home runs",)  # type: ignore[index]
    adapters.KALSHI_STAT_TO_FAMILY["home runs"] = HOME_RUNS
    adapters.KALSHI_PLAYER_LINE_RE = re.compile(
        r"^(?P<player>.+?):\s*(?P<threshold>\d+(?:\.\d+)?)\+\s*"
        r"(?P<stat>hits|hits \+ runs \+ RBIs|total bases|strikeouts|home runs)\?",
        re.I,
    )
    discovery.SERIES_TO_FAMILY["KXMLBHR"] = HOME_RUNS
    discovery.FAMILY_TO_GEMINI_STAT[HOME_RUNS] = "home_runs"


async def _fetch_one(http: httpx.AsyncClient, series: str) -> list[dict[str, Any]]:
    last_response: httpx.Response | None = None
    for attempt in range(5):
        response = await http.get(
            f"{KALSHI}/events",
            params={
                "limit": 200,
                "status": "open",
                "series_ticker": series,
                "with_nested_markets": "true",
            },
        )
        last_response = response
        if response.status_code != 429:
            response.raise_for_status()
            body = response.json()
            return list(body.get("events") or [])
        retry_after = response.headers.get("retry-after")
        try:
            header_delay = float(retry_after) if retry_after is not None else 0.0
        except ValueError:
            header_delay = 0.0
        await asyncio.sleep(max(header_delay, 0.75 * (2 ** attempt)))
    assert last_response is not None
    last_response.raise_for_status()
    return []


async def fetch_selected_kalshi_events(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, series in enumerate(SELECTED_SERIES):
        rows.extend(await _fetch_one(http, series))
        if index + 1 < len(SELECTED_SERIES):
            await asyncio.sleep(0.30)
    return rows


def install() -> None:
    _install_home_runs()
    hot._fetch_kalshi_events = fetch_selected_kalshi_events
