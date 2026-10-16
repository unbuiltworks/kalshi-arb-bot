"""Read-only buy-vs-sell calibration study for 15-minute crypto UP/DOWN markets.

Settles one question across the nine Kalshi 15m series: in each
(series, minutes-remaining, price-band) cell, does the cheap side win MORE or
LESS often than its price implies?

  realized > implied + fee  ->  BUY the longshot
  realized < implied - fee  ->  SELL the longshot (buy the favorite)
  otherwise                 ->  NO EDGE, and the fee band is wide

The fee enters as a no-trade band of width 2*fee around the price, so "no edge"
is the default verdict and a cell must clear fees in one direction to earn one.
A verdict also requires the Wilson 95% interval -- not the point estimate -- to
clear that bound, so a lucky cell cannot produce a recommendation.

Why this exists: kalshi-longshot-fader SILENT_FAILURES.md SF-002 found the live
15m gate pays 90-94c for what it derives as a ~78% event, and that the market
was better calibrated than the gate at every level >= 1.5 (z 0.5-1: market
82.6%, actual 83.0%). That 78% is Phi() of a bug-corrected feature, not a
counted frequency. This module counts the frequency.

Never submits, modifies, or cancels orders. Market data reads only.

Polymarket US is deliberately out of scope: its settlement index is not
established as equivalent to Kalshi BRTI, and assuming so already cost a
realized -$0.885300 (REALIZED_INDEX_MISMATCH, kalshi-arb-bot
ARB_STATUS_HISTORY_2026-08-23.md).
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

# The nine 15m series tracked by kalshi-longshot-fader
# (causal_300_shadow.NINE_SERIES_PRODUCT).
NINE_SERIES = {
    "btc": "KXBTC15M",
    "eth": "KXETH15M",
    "sol": "KXSOL15M",
    "xrp": "KXXRP15M",
    "doge": "KXDOGE15M",
    "bnb": "KXBNB15M",
    "hype": "KXHYPE15M",
    "near": "KXNEAR15M",
    "zec": "KXZEC15M",
}

# Candlestick route has moved; try each until one answers 200.
CANDLE_PATHS = (
    "/series/{series}/markets/{ticker}/candlesticks",
    "/historical/markets/{ticker}/candlesticks",
)

# Longshot-side price bands, in cents. 6-10c is the fader's own cell: the
# complement of its 90-94c favorite entry band.
PRICE_BANDS = ((1, 5), (6, 10), (11, 20), (21, 35), (36, 49))

# Kalshi taker fee: ceil(0.07 * C * P * (1-P)) dollars, P in dollars.
TAKER_RATE = 0.07


def taker_fee_cents_per_contract(price_cents: float, count: int) -> float:
    """Per-contract taker fee in cents, carrying Kalshi's ceil-to-the-cent.

    The ceil is applied to the ORDER, not the contract, so the per-contract cost
    falls with size: a single contract at 8c pays a full 1c (12.5% of stake),
    while 100 pay ~0.52c each. Size is part of the economics, not a detail.
    """
    if count <= 0:
        raise ValueError("count must be positive")
    p = price_cents / 100.0
    fee_dollars = math.ceil(TAKER_RATE * count * p * (1.0 - p) * 100.0) / 100.0
    return fee_dollars * 100.0 / count


def wilson_95(wins: int, n: int) -> tuple[float, float] | None:
    """95% Wilson interval on a win rate; None when the sample is empty."""
    if n <= 0:
        return None
    z = 1.959964
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def verdict(ci: tuple[float, float] | None, implied: float, fee_frac: float,
            min_n: int, n: int) -> str:
    """Direction a cell earns, or why it earns none.

    Buying the longshot needs realized > implied + fee; selling it needs
    realized < implied - fee. The interval, not the point estimate, must clear
    the bound, so UNDERPOWERED is a distinct answer from NO_EDGE.
    """
    if n < min_n:
        return "UNDERPOWERED"
    if ci is None:
        return "UNDERPOWERED"
    low, high = ci
    if low > implied + fee_frac:
        return "BUY_LONGSHOT"
    if high < implied - fee_frac:
        return "SELL_LONGSHOT"
    return "NO_EDGE"


class Kalshi:
    """Minimal synchronous signed-GET client."""

    def __init__(self, pace_s: float = 0.0) -> None:
        key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
        if not key_id:
            raise SystemExit("KALSHI_API_KEY_ID is required")
        self.key_id = key_id
        self.private_key = load_private_key()
        self.http = httpx.Client(timeout=20.0)
        self.pace_s = pace_s

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        if self.pace_s:
            time.sleep(self.pace_s)
        headers = auth_headers(self.private_key, "GET", f"/trade-api/v2{path}", self.key_id)
        return self.http.get(f"{KALSHI_BASE}{path}", headers=headers, params=params)

    def get_json(self, path: str, params: dict | None = None) -> dict:
        resp = self.get(path, params)
        resp.raise_for_status()
        return resp.json() or {}

    def close(self) -> None:
        self.http.close()


def settled_markets(api: Kalshi, series: str, lookback_days: int, cap: int) -> list[dict]:
    """Page settled markets for one series."""
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

    Mid, not last trade: a tradable price is what an entry actually faces.
    """
    bid = (candle.get("yes_bid") or {}).get("close")
    ask = (candle.get("yes_ask") or {}).get("close")
    try:
        bid_c, ask_c = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not (0 < bid_c <= ask_c < 100):
        return None
    return (bid_c + ask_c) / 2.0


def band_of(price_cents: float) -> str | None:
    for low, high in PRICE_BANDS:
        if low <= price_cents <= high:
            return f"{low}-{high}c"
    return None


class Cells:
    """Accumulates longshot-side outcomes per (minutes_remaining, price band)."""

    def __init__(self) -> None:
        self.cells: dict[tuple[int, str], dict] = defaultdict(
            lambda: {"n": 0, "wins": 0, "price_sum": 0.0})
        self.favorite: dict[int, dict] = defaultdict(lambda: {"n": 0, "wins": 0})

    def add(self, minutes: int, longshot_price: float, longshot_won: bool) -> bool:
        band = band_of(longshot_price)
        if band is None:
            return False
        cell = self.cells[(minutes, band)]
        cell["n"] += 1
        cell["wins"] += int(longshot_won)
        cell["price_sum"] += longshot_price
        fav = self.favorite[minutes]
        fav["n"] += 1
        fav["wins"] += int(not longshot_won)
        return True

    def rows(self, count: int, min_n: int) -> list[dict]:
        out = []
        for (minutes, band) in sorted(self.cells):
            cell = self.cells[(minutes, band)]
            n, wins = cell["n"], cell["wins"]
            avg_price = cell["price_sum"] / n
            implied = avg_price / 100.0
            realized = wins / n
            fee_c = taker_fee_cents_per_contract(avg_price, count)
            ci = wilson_95(wins, n)
            out.append({
                "minutes_remaining": minutes,
                "longshot_price_band": band,
                "n": n,
                "avg_longshot_price_cents": round(avg_price, 2),
                "implied_win_rate": round(implied, 4),
                "realized_win_rate": round(realized, 4),
                "ci95_realized": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
                "fee_cents_per_contract": round(fee_c, 3),
                # EV per contract, in cents, net of one taker fee on entry.
                "ev_buy_longshot_cents": round(realized * 100.0 - avg_price - fee_c, 3),
                "ev_sell_longshot_cents": round(
                    (1.0 - realized) * 100.0 - (100.0 - avg_price) - fee_c, 3),
                "verdict": verdict(ci, implied, fee_c / 100.0, min_n, n),
            })
        return out

    def favorite_rows(self) -> list[dict]:
        out = []
        for minutes in sorted(self.favorite):
            cell = self.favorite[minutes]
            ci = wilson_95(cell["wins"], cell["n"])
            out.append({
                "minutes_remaining": minutes,
                "n": cell["n"],
                "favorite_win_rate": round(cell["wins"] / cell["n"], 4) if cell["n"] else None,
                "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
            })
        return out


def collect(api: Kalshi, series: str, markets: list[dict], cells: Cells,
            pooled: Cells) -> dict:
    """Score every usable window of one series into both cell tables."""
    usable = 0
    skipped: dict[str, int] = defaultdict(int)

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
            longshot_price = (100.0 - mid) if yes_is_favorite else mid
            longshot_won = yes_won != yes_is_favorite

            if cells.add(minutes, longshot_price, longshot_won):
                pooled.add(minutes, longshot_price, longshot_won)
                window_used = True

        usable += int(window_used)

    return {"windows_usable": usable, "skipped": dict(skipped)}


def headline(rows: list[dict]) -> dict:
    """The decision summary: which cells earned a direction, and the fader's own."""
    buys = [r for r in rows if r["verdict"] == "BUY_LONGSHOT"]
    sells = [r for r in rows if r["verdict"] == "SELL_LONGSHOT"]
    # The live 15m gate: 90-94c favorite, 250-350s to close = 4-6 minutes left.
    fader = [r for r in rows
             if r["longshot_price_band"] == "6-10c" and 4 <= r["minutes_remaining"] <= 6]
    return {
        "cells_total": len(rows),
        "cells_buy_longshot": len(buys),
        "cells_sell_longshot": len(sells),
        "cells_no_edge": sum(1 for r in rows if r["verdict"] == "NO_EDGE"),
        "cells_underpowered": sum(1 for r in rows if r["verdict"] == "UNDERPOWERED"),
        "best_buy_cell": max(buys, key=lambda r: r["ev_buy_longshot_cents"], default=None),
        "best_sell_cell": max(sells, key=lambda r: r["ev_sell_longshot_cents"], default=None),
        "fader_entry_cells_6_10c_at_4_6min": fader,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coin", action="append", default=[], choices=sorted(NINE_SERIES),
                        help="repeatable; defaults to all nine series")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--max-markets", type=int, default=2000,
                        help="per-series cap on settled markets pulled")
    parser.add_argument("--count", type=int, default=100,
                        help="order size the fee is computed for; the ceil makes this matter")
    parser.add_argument("--min-n", type=int, default=200,
                        help="minimum cell sample before a cell may earn a verdict")
    parser.add_argument("--pace-s", type=float, default=0.05,
                        help="sleep between API calls; this walks thousands of markets")
    parser.add_argument("--source", default="kalshi", choices=("kalshi", "pmus"))
    args = parser.parse_args()

    if args.source == "pmus":
        raise SystemExit(
            "PMUS adapter not implemented: gateway.polymarket.us /v1/price-history is "
            "unverified, and PMUS settlement-index equivalence with Kalshi BRTI is NOT "
            "established. Do not infer PMUS rates from Kalshi output."
        )

    api = Kalshi(pace_s=args.pace_s)
    pooled = Cells()
    try:
        for coin in (args.coin or sorted(NINE_SERIES)):
            series = NINE_SERIES[coin]
            cells = Cells()
            markets = settled_markets(api, series, args.lookback_days, args.max_markets)
            stats = collect(api, series, markets, cells, pooled)
            rows = cells.rows(args.count, args.min_n)
            print(json.dumps({
                "scope": "per_series",
                "coin": coin,
                "series": series,
                "lookback_days": args.lookback_days,
                "order_size_for_fees": args.count,
                "settled_markets_found": len(markets),
                **stats,
                "favorite_by_minute": cells.favorite_rows(),
                "cells": rows,
                "headline": headline(rows),
                "fees_applied": True,
                "classification": "HISTORICAL_REPLAY_NOT_REALIZED",
            }, sort_keys=True))

        pooled_rows = pooled.rows(args.count, args.min_n)
        print(json.dumps({
            "scope": "pooled_all_series",
            "order_size_for_fees": args.count,
            "favorite_by_minute": pooled.favorite_rows(),
            "cells": pooled_rows,
            "headline": headline(pooled_rows),
            "fees_applied": True,
            "classification": "HISTORICAL_REPLAY_NOT_REALIZED",
            "caveat": "Pooling mixes coins with different vol regimes; a pooled verdict "
                      "is a screen, not an attribution. See REPLICATION_PROTOCOL_15M.md "
                      "SF-019 (INSUFFICIENT OVERLAP TO IDENTIFY).",
        }, sort_keys=True))
    finally:
        api.close()


if __name__ == "__main__":
    main()
