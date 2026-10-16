"""Read-only test of whether distance-from-strike adds information to price.

THE QUESTION. The 15m calibration study found Kalshi's prices well calibrated:
a side priced at 8c settles ~6% of the time. That kills a pure price-based
edge. But it leaves one thing untested -- the hypothesis that the live fader's
losses came from its FILTER rather than from the market.

The test is conditional, and that is the whole point:

    Holding the Kalshi price FIXED, does distance-from-strike -- measured from
    an INDEPENDENT spot feed -- predict the outcome?

  * If realized win rate is flat across distance buckets inside one price band,
    the price already contains everything distance knows. No edge, and the
    fader's filter was selecting noise.
  * If it varies, the independent feed carries information Kalshi's quote has
    not absorbed yet, and the sign tells you which way the filter was wrong.
    A filter that paid 92c for windows whose distance implied ~78% is adverse
    selection in the filter, not a mispricing in the market.

WHY THE FEED MUST BE INDEPENDENT. Distance could be reconstructed from Kalshi
alone: inside one event the strike ladder brackets spot, so the strike where
price crosses 50c IS the market-implied spot. That is useless here. A distance
derived from Kalshi prices is a monotone transform OF the price, so it cannot
out-predict the price by construction -- it would answer the question "no" no
matter what is true. Only a feed Kalshi does not quote can test the lag.

HORIZON SCALING (kalshi-longshot-fader SILENT_FAILURES.md SF-002). The live
gate divides distance by one-minute realized vol and nothing else, so it is
horizon-blind: identical whether 60 seconds or 15 minutes remain. A z-score
needs vol * sqrt(minutes). At the 300s decision point that is a 2.24x
overstatement. Both forms are reported side by side -- `raw_1m_units` is what
the gate actually used, `horizon_z` is the corrected one -- so the bug's
effect is visible rather than argued about.

Never submits, modifies, or cancels orders. Market data reads only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from collections import defaultdict

import httpx

from src.crypto_15m_longshot_study import (
    NINE_SERIES,
    Kalshi,
    band_of,
    close_ts_of,
    settled_markets,
    taker_fee_cents_per_contract,
    touch_close_cents,
    wilson_95,
    yes_mid_cents,
    candles as kalshi_candles,
)

# Independent spot feed. Must NOT be Kalshi -- see the module docstring.
COINBASE_BASE = "https://api.exchange.coinbase.com"

SPOT_PRODUCT = {
    "KXBTC15M": "BTC-USD",
    "KXETH15M": "ETH-USD",
    "KXSOL15M": "SOL-USD",
    "KXXRP15M": "XRP-USD",
    "KXDOGE15M": "DOGE-USD",
    "KXBNB15M": "BNB-USD",
    "KXHYPE15M": "HYPE-USD",
    "KXNEAR15M": "NEAR-USD",
    "KXZEC15M": "ZEC-USD",
}

# Trailing 1-minute bars used for the vol estimate.
VOL_BARS = 30

# Buckets on |distance|, in the unit being bucketed.
DISTANCE_BUCKETS = ((0.0, 0.5), (0.5, 1.0), (1.0, 1.75), (1.75, 2.5), (2.5, 99.0))


def spot_series(http: httpx.Client, product: str, start_ts: int,
                end_ts: int) -> dict[int, float] | None:
    """Close price per 1-minute bar, keyed by bar-end epoch seconds.

    Coinbase returns [time, low, high, open, close, volume] with `time` the bar
    START, so each is shifted by 60s to match Kalshi's end_period_ts convention.
    """
    try:
        resp = http.get(
            f"{COINBASE_BASE}/products/{product}/candles",
            params={"granularity": 60, "start": start_ts, "end": end_ts},
        )
    except httpx.HTTPError:
        # A blocked host raises rather than answering; treat it as absent data
        # so the caller's explicit feed probe reports it, not a traceback.
        return None
    if resp.status_code != 200:
        return None
    try:
        rows = resp.json()
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    out: dict[int, float] = {}
    for row in rows:
        try:
            out[int(row[0]) + 60] = float(row[4])
        except (TypeError, ValueError, IndexError):
            continue
    return out or None


def one_minute_vol(spot: dict[int, float], at_ts: int, bars: int = VOL_BARS) -> float | None:
    """Stdev of 1-minute log returns over the `bars` bars ending at `at_ts`."""
    times = sorted(t for t in spot if t <= at_ts)[-(bars + 1):]
    if len(times) < 5:
        return None
    rets = []
    for prev, cur in zip(times, times[1:]):
        p0, p1 = spot[prev], spot[cur]
        if p0 > 0 and p1 > 0:
            rets.append(math.log(p1 / p0))
    if len(rets) < 4:
        return None
    try:
        vol = statistics.stdev(rets)
    except statistics.StatisticsError:
        return None
    return vol if vol > 0 else None


def distances(spot_px: float, strike: float, vol: float,
              minutes_remaining: int) -> tuple[float, float] | None:
    """(raw_1m_units, horizon_z) -- the gate's number, and the corrected one.

    raw_1m_units is what `distance_in_1m_vol_units` computes: no sqrt(time), so
    horizon-blind. horizon_z divides by sqrt(minutes) as a z-score requires.
    """
    if not strike or vol is None or vol <= 0 or minutes_remaining <= 0:
        return None
    raw = ((spot_px - strike) / strike) / vol
    return raw, raw / math.sqrt(minutes_remaining)


def bucket_of(value: float) -> str | None:
    mag = abs(value)
    for low, high in DISTANCE_BUCKETS:
        if low <= mag < high:
            return f"{low}-{high}"
    return None


def study(api: Kalshi, http: httpx.Client, series: str, markets: list[dict],
          minutes_window: tuple[int, int], unit: str, count: int,
          min_n: int) -> dict:
    """Score windows into (price band, distance bucket) cells."""
    product = SPOT_PRODUCT[series]
    lo_m, hi_m = minutes_window

    cells: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"n": 0, "fav_wins": 0, "fav_price_sum": 0.0})
    skipped: dict[str, int] = defaultdict(int)
    usable = 0

    for market in markets:
        result = str(market.get("result") or "").lower()
        if result not in ("yes", "no"):
            skipped["unresolved_result"] += 1
            continue
        close_ts = close_ts_of(market)
        if close_ts is None:
            skipped["missing_close_ts"] += 1
            continue
        try:
            strike = float(market.get("floor_strike"))
        except (TypeError, ValueError):
            skipped["missing_strike"] += 1
            continue

        k_rows = kalshi_candles(api, series, str(market.get("ticker")),
                                close_ts - 16 * 60, close_ts)
        if not k_rows:
            skipped["no_kalshi_candles"] += 1
            continue
        by_end = {}
        for row in k_rows:
            try:
                by_end[int(row.get("end_period_ts"))] = row
            except (TypeError, ValueError):
                continue

        # Reach back far enough for the trailing vol estimate.
        spot = spot_series(http, product,
                           close_ts - (VOL_BARS + 20) * 60, close_ts)
        if spot is None:
            skipped["no_spot"] += 1
            continue

        used = False
        for minutes in range(lo_m, hi_m + 1):
            at_ts = close_ts - minutes * 60
            candle = by_end.get(at_ts)
            spot_px = spot.get(at_ts)
            if candle is None or spot_px is None:
                continue
            mid = yes_mid_cents(candle)
            if mid is None or mid == 50.0:
                continue
            vol = one_minute_vol(spot, at_ts)
            pair = distances(spot_px, strike, vol, minutes)
            if pair is None:
                continue
            raw, horizon = pair
            value = raw if unit == "raw_1m_units" else horizon
            bucket = bucket_of(value)
            if bucket is None:
                continue

            yes_won = result == "yes"
            yes_is_favorite = mid > 50.0
            longshot_price = (100.0 - mid) if yes_is_favorite else mid
            band = band_of(longshot_price)
            if band is None:
                continue

            cell = cells[(band, bucket)]
            cell["n"] += 1
            cell["fav_wins"] += int(yes_won == yes_is_favorite)
            cell["fav_price_sum"] += 100.0 - longshot_price
            used = True

        usable += int(used)

    rows = []
    for (band, bucket) in sorted(cells):
        cell = cells[(band, bucket)]
        n, wins = cell["n"], cell["fav_wins"]
        fav_px = cell["fav_price_sum"] / n
        fee = taker_fee_cents_per_contract(fav_px, count)
        realized = wins / n
        ci = wilson_95(wins, n)
        rows.append({
            "longshot_price_band": band,
            "distance_bucket": bucket,
            "n": n,
            "avg_favorite_price_cents": round(fav_px, 2),
            "implied_favorite_win_rate": round(fav_px / 100.0, 4),
            "realized_favorite_win_rate": round(realized, 4),
            "ci95_realized": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
            "fee_cents_per_contract": round(fee, 3),
            "ev_buy_favorite_cents": round(realized * 100.0 - fav_px - fee, 3),
            "powered": n >= min_n,
        })

    return {
        "series": series,
        "spot_product": product,
        "distance_unit": unit,
        "minutes_window": list(minutes_window),
        "settled_markets": len(markets),
        "windows_usable": usable,
        "skipped": dict(skipped),
        "cells": rows,
        "spread_within_price_band": spread_report(rows, min_n),
        "fees_applied": True,
        "classification": "HISTORICAL_REPLAY_NOT_REALIZED",
    }


def spread_report(rows: list[dict], min_n: int) -> list[dict]:
    """The actual answer: inside one price band, how much does distance move it?

    A flat spread means the price already knows what distance knows. Only the
    powered cells are compared, so a two-observation bucket cannot manufacture
    a spread.
    """
    by_band: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["n"] >= min_n:
            by_band[row["longshot_price_band"]].append(row)

    out = []
    for band, group in sorted(by_band.items()):
        if len(group) < 2:
            continue
        rates = [r["realized_favorite_win_rate"] for r in group]
        lo = min(group, key=lambda r: r["realized_favorite_win_rate"])
        hi = max(group, key=lambda r: r["realized_favorite_win_rate"])
        # Non-overlapping intervals are the only version of this worth acting on.
        separated = (lo["ci95_realized"] and hi["ci95_realized"]
                     and lo["ci95_realized"][1] < hi["ci95_realized"][0])
        out.append({
            "longshot_price_band": band,
            "powered_buckets": len(group),
            "realized_spread_pct_points": round((max(rates) - min(rates)) * 100, 2),
            "lowest_bucket": lo["distance_bucket"],
            "highest_bucket": hi["distance_bucket"],
            "intervals_disjoint": bool(separated),
            "reading": ("DISTANCE_ADDS_INFORMATION" if separated
                        else "PRICE_ALREADY_CONTAINS_DISTANCE"),
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coin", action="append", default=[], choices=sorted(NINE_SERIES))
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--max-markets", type=int, default=2000)
    parser.add_argument("--minutes", default="4-6",
                        help="minutes-remaining window, e.g. 4-6 (the live gate's 250-350s)")
    parser.add_argument("--unit", default="horizon_z",
                        choices=("horizon_z", "raw_1m_units"),
                        help="horizon_z is the sqrt(time)-corrected form; "
                             "raw_1m_units is what the live gate actually used")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--min-n", type=int, default=100)
    parser.add_argument("--pace-s", type=float, default=1.2)
    args = parser.parse_args()

    lo_s, _, hi_s = args.minutes.partition("-")
    minutes_window = (int(lo_s), int(hi_s or lo_s))

    api = Kalshi(pace_s=args.pace_s)
    http = httpx.Client(timeout=20.0)

    # Fail loudly and early: a blocked spot feed would otherwise look like a
    # clean "no information" result, which is the wrong conclusion entirely.
    probe = spot_series(http, "BTC-USD", int(time.time()) - 3600, int(time.time()))
    if probe is None:
        api.close()
        http.close()
        raise SystemExit(
            f"independent spot feed unreachable at {COINBASE_BASE}. This study "
            "CANNOT fall back to Kalshi-derived spot: a distance built from "
            "Kalshi prices is a transform of the price and would answer 'no "
            "information' regardless of the truth. Allowlist the feed host and "
            "re-run."
        )

    try:
        for coin in (args.coin or ["btc"]):
            series = NINE_SERIES[coin]
            markets = settled_markets(api, series, args.lookback_days, args.max_markets)
            print(json.dumps(study(api, http, series, markets, minutes_window,
                                   args.unit, args.count, args.min_n), sort_keys=True))
    finally:
        api.close()
        http.close()


if __name__ == "__main__":
    main()
