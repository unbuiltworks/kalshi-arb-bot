"""Read-only favorite/longshot study for 15-minute crypto UP/DOWN markets.

Answers two questions over settled 15m windows, bucketed by minutes remaining:

  1. How often does the favored side (mid > 50c) actually win?
  2. Is the longshot side overpriced -- i.e. does a side quoted at X cents win
     LESS than X% of the time? That, not the favorite's raw win rate, is the
     only thing that makes selling longshots profitable.

Never submits, modifies, or cancels orders. Market data reads only.

Source: Kalshi settled KX*15M series, which have the longest history of
index-settled 15m crypto windows. Polymarket US settlement source is NOT
assumed equivalent -- see ARB_STATUS_HISTORY_2026-08-23.md
(REALIZED_INDEX_MISMATCH, -$0.885300, Kalshi BRTI vs a different venue index).
Use --source pmus only once a PMUS price-history adapter is verified.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import defaultdict

import httpx

from src.auth import auth_headers, load_private_key

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# 15-minute up/down series, one per coin.
SERIES = {
    "btc": "KXBTC15M",
    "eth": "KXETH15M",
    "sol": "KXSOL15M",
    "xrp": "KXXRP15M",
}

# Candlestick route has moved; try each until one answers 200.
CANDLE_PATHS = (
    "/series/{series}/markets/{ticker}/candlesticks",
    "/historical/markets/{ticker}/candlesticks",
)

PRICE_BUCKETS = ((1, 5), (6, 10), (11, 20), (21, 35), (36, 50))


class Kalshi:
    """Minimal synchronous signed-GET client."""

    def __init__(self) -> None:
        key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
        if not key_id:
            raise SystemExit("KALSHI_API_KEY_ID is required")
        self.key_id = key_id
        self.private_key = load_private_key()
        self.http = httpx.Client(timeout=20.0)

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        headers = auth_headers(self.private_key, "GET", f"/trade-api/v2{path}", self.key_id)
        return self.http.get(f"{KALSHI_BASE}{path}", headers=headers, params=params)

    def get_json(self, path: str, params: dict | None = None) -> dict:
        resp = self.get(path, params)
        resp.raise_for_status()
        return resp.json() or {}

    def close(self) -> None:
        self.http.close()


def settled_markets(api: Kalshi, series: str, lookback_days: int, cap: int) -> list[dict]:
    """Page settled markets for one series, newest first."""
    min_close = int(time.time()) - lookback_days * 86400
    rows: list[dict] = []
    cursor = ""
    while len(rows) < cap:
        params = {
            "series_ticker": series,
            "status": "settled",
            "min_close_ts": min_close,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        payload = api.get_json("/markets", params)
        batch = [m for m in (payload.get("markets") or []) if isinstance(m, dict)]
        rows.extend(batch)
        cursor = payload.get("cursor") or ""
        if not cursor or not batch:
            break
    return rows[:cap]


def candles(api: Kalshi, series: str, ticker: str, start_ts: int, end_ts: int) -> list[dict]:
    """1-minute candlesticks covering one window; empty list if no route answers."""
    params = {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 1}
    for template in CANDLE_PATHS:
        resp = api.get(template.format(series=series, ticker=ticker), params)
        if resp.status_code == 200:
            body = resp.json() or {}
            return [c for c in (body.get("candlesticks") or []) if isinstance(c, dict)]
    return []


def yes_mid_cents(candle: dict) -> float | None:
    """Mid of the YES touch at the candle's close, in cents.

    Mid -- not last trade -- because a tradable price is what a seller faces.
    """
    bid = ((candle.get("yes_bid") or {}).get("close"))
    ask = ((candle.get("yes_ask") or {}).get("close"))
    try:
        bid_c, ask_c = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not (0 < bid_c <= ask_c < 100):
        return None
    return (bid_c + ask_c) / 2.0


def wilson_95(wins: int, n: int) -> tuple[float, float] | None:
    """95% Wilson interval on a win rate; None when the sample is empty."""
    if n <= 0:
        return None
    z = 1.959964
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def study(api: Kalshi, coin: str, series: str, lookback_days: int, cap: int) -> dict:
    markets = settled_markets(api, series, lookback_days, cap)

    # minutes_remaining -> counters
    fav = defaultdict(lambda: {"n": 0, "fav_wins": 0})
    # (minutes_remaining, bucket) -> longshot-side counters
    longshot = defaultdict(lambda: {"n": 0, "longshot_wins": 0, "implied_sum": 0.0})

    usable = 0
    skipped = defaultdict(int)

    for market in markets:
        result = str(market.get("result") or "").lower()
        if result not in ("yes", "no"):
            skipped["unresolved_result"] += 1
            continue
        try:
            close_ts = int(market.get("close_ts"))
        except (TypeError, ValueError):
            skipped["missing_close_ts"] += 1
            continue

        rows = candles(api, series, str(market.get("ticker")), close_ts - 16 * 60, close_ts)
        if not rows:
            skipped["no_candles"] += 1
            continue

        by_end = {}
        for row in rows:
            try:
                by_end[int(row.get("end_period_ts"))] = row
            except (TypeError, ValueError):
                continue

        window_used = False
        for minutes in range(1, 15):
            candle = by_end.get(close_ts - minutes * 60)
            if candle is None:
                continue
            mid = yes_mid_cents(candle)
            if mid is None or mid == 50.0:
                continue

            yes_won = result == "yes"
            yes_is_favorite = mid > 50.0

            bucket = fav[minutes]
            bucket["n"] += 1
            bucket["fav_wins"] += int(yes_won == yes_is_favorite)

            # The longshot is the cheap side; its price is its implied probability.
            longshot_price = (100.0 - mid) if yes_is_favorite else mid
            longshot_won = yes_won != yes_is_favorite
            for low, high in PRICE_BUCKETS:
                if low <= longshot_price <= high:
                    cell = longshot[(minutes, f"{low}-{high}c")]
                    cell["n"] += 1
                    cell["longshot_wins"] += int(longshot_won)
                    cell["implied_sum"] += longshot_price / 100.0
                    break
            window_used = True

        usable += int(window_used)

    favorite_by_minute = []
    for minutes in sorted(fav):
        cell = fav[minutes]
        favorite_by_minute.append({
            "minutes_remaining": minutes,
            "n": cell["n"],
            "favorite_win_rate": round(cell["fav_wins"] / cell["n"], 4) if cell["n"] else None,
            "ci95": wilson_95(cell["fav_wins"], cell["n"]),
        })

    longshot_calibration = []
    for (minutes, label) in sorted(longshot, key=lambda k: (k[0], k[1])):
        cell = longshot[(minutes, label)]
        n = cell["n"]
        realized = cell["longshot_wins"] / n if n else None
        implied = cell["implied_sum"] / n if n else None
        longshot_calibration.append({
            "minutes_remaining": minutes,
            "longshot_price_bucket": label,
            "n": n,
            "implied_win_rate": round(implied, 4) if implied is not None else None,
            "realized_win_rate": round(realized, 4) if realized is not None else None,
            # Positive edge_pct_points = longshot overpriced = selling it has gross edge.
            "edge_pct_points": round((implied - realized) * 100, 3) if n else None,
            "ci95_realized": wilson_95(cell["longshot_wins"], n),
        })

    return {
        "coin": coin,
        "series": series,
        "lookback_days": lookback_days,
        "settled_markets_found": len(markets),
        "windows_usable": usable,
        "skipped": dict(skipped),
        "favorite_by_minute": favorite_by_minute,
        "longshot_calibration": longshot_calibration,
        "fees_applied": False,
        "classification": "HISTORICAL_REPLAY_NOT_REALIZED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coin", action="append", default=[], choices=sorted(SERIES),
                        help="repeatable; defaults to all four")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--max-markets", type=int, default=2000,
                        help="per-coin cap on settled markets pulled")
    parser.add_argument("--source", default="kalshi", choices=("kalshi", "pmus"))
    args = parser.parse_args()

    if args.source == "pmus":
        raise SystemExit(
            "PMUS adapter not implemented: gateway.polymarket.us /v1/price-history is "
            "unverified from this environment, and PMUS settlement-index equivalence with "
            "Kalshi BRTI is NOT established. Do not infer PMUS rates from Kalshi output."
        )

    api = Kalshi()
    try:
        for coin in (args.coin or sorted(SERIES)):
            print(json.dumps(study(api, coin, SERIES[coin], args.lookback_days, args.max_markets),
                             sort_keys=True))
    finally:
        api.close()


if __name__ == "__main__":
    main()
