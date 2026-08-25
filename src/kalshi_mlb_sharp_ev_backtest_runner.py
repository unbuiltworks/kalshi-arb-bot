"""Diagnostic runner for the isolated Kalshi MLB sharp-EV backtest.

Research-only adapter:
- keeps the hardened Eastern-aware Kalshi MLB physical-game normalizer;
- routes historical sportsbook snapshots through ParlayAPI so the production
  The Odds API quota is untouched;
- authenticates with X-API-Key so the credential is never placed in a URL;
- leaves all precommitted thresholds, fee math, buckets, and go/no-go rules in
  the backtest module unchanged.

No order entry is imported or enabled.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Mapping

import src.kalshi_mlb_sharp_ev_backtest as backtest
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern

PARLAY_HISTORICAL_URL = "https://parlay-api.com/v1/historical/sports/baseball_mlb/odds"


async def _parlay_historical_odds_snapshot(http: Any, api_key: str, snapshot_ts: int):
    """Fetch one point-in-time MLB snapshot without putting the key in the URL."""
    dt = datetime.fromtimestamp(snapshot_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    response = await http.get(
        PARLAY_HISTORICAL_URL,
        params={
            "bookmakers": ",".join(backtest.SHARP_BOOKS),
            "markets": "h2h",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            "date": dt,
        },
        headers={"X-API-Key": api_key},
    )
    if response.status_code != 200:
        request_id = response.headers.get("x-request-id") or response.headers.get("X-Request-ID")
        raise RuntimeError(
            f"ParlayAPI historical request failed status={response.status_code} request_id={request_id or 'unknown'}"
        )

    payload = response.json()
    if isinstance(payload, list):
        data = payload
        snapshot = dt
    elif isinstance(payload, Mapping):
        raw_data = payload.get("data")
        if isinstance(raw_data, list):
            data = raw_data
        elif isinstance(payload.get("events"), list):
            data = payload.get("events") or []
        else:
            data = []
        snapshot = payload.get("timestamp") or payload.get("snapshot") or dt
    else:
        data = []
        snapshot = dt

    events = [
        event
        for raw in data
        if isinstance(raw, Mapping)
        if (event := backtest.sharp_event(raw)) is not None
    ]
    quota = {
        "provider": "parlayapi",
        "remaining": response.headers.get("x-requests-remaining"),
        "used": response.headers.get("x-requests-used"),
        "last": response.headers.get("x-requests-last"),
        "snapshot": snapshot,
        "request_id": response.headers.get("x-request-id") or response.headers.get("X-Request-ID"),
    }
    return events, quota


# Backtest globals are resolved dynamically. Override only data-source adapters;
# all strategy/accounting parameters remain fixed in the original module.
backtest.normalize_kalshi_mlb = normalize_kalshi_mlb_eastern
backtest._historical_odds_snapshot = _parlay_historical_odds_snapshot


if __name__ == "__main__":
    key = os.getenv("PARLAY_API_KEY", "").strip()
    if not key:
        raise SystemExit("PARLAY_API_KEY is required")
    # backtest.main() owns argument parsing and passes this opaque value into the
    # patched historical adapter. It is never sent to The Odds API.
    os.environ["THE_ODDS_API_KEY"] = key
    backtest.main()
