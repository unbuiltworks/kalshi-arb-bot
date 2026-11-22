"""Read-only Kalshi 15m crypto incentive + top-book shadow.

Uses public series/market/incentive/orderbook GET endpoints only. No credentials,
portfolio reads, signing, order entry, amendment, or cancellation.

`period_reward` is reported exactly as Kalshi returns it. This module never treats
it as our expected earnings. `target_size_met_at_top_bids` is only a conservative
visible-book diagnostic; Kalshi's official reward score uses randomized snapshots,
relative scores, spread/distance rules, and exclusion rules that this shadow does
not reproduce yet.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import time
from typing import Any

import httpx

import src.kalshi_temporal_live_shadow as base
from src.kalshi_temporal_live_shadow_v2 import fetch_series_certified
from src.temporal_binary_shadow import FIRST_TIER_ASSETS

API = base.KALSHI_API


@dataclass(frozen=True)
class Incentive:
    id: str
    market_id: str
    market_ticker: str
    incentive_type: str
    description: str
    start_date: str | None
    end_date: str | None
    period_reward: float | None
    paid_out: bool
    discount_factor_bps: float | None
    target_size: float | None


def _f(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def fetch_incentives(http: httpx.Client) -> list[Incentive]:
    out: list[Incentive] = []
    cursor: str | None = None
    for _ in range(20):
        params: dict[str, Any] = {"status": "active", "type": "all", "limit": 10000}
        if cursor:
            params["cursor"] = cursor
        response = http.get(f"{API}/incentive_programs", params=params)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("incentive_programs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("unexpected incentive_programs payload")
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("market_ticker") or "").strip().upper()
            if not ticker:
                continue
            out.append(Incentive(
                id=str(row.get("id") or ""),
                market_id=str(row.get("market_id") or ""),
                market_ticker=ticker,
                incentive_type=str(row.get("incentive_type") or ""),
                description=str(row.get("incentive_description") or ""),
                start_date=None if row.get("start_date") is None else str(row.get("start_date")),
                end_date=None if row.get("end_date") is None else str(row.get("end_date")),
                period_reward=_f(row.get("period_reward")),
                paid_out=bool(row.get("paid_out")),
                discount_factor_bps=_f(row.get("discount_factor_bps")),
                target_size=_f(row.get("target_size_fp")),
            ))
        cursor = str(payload.get("next_cursor") or "") if isinstance(payload, dict) else ""
        if not cursor:
            break
    return out


def _best(levels: Any) -> tuple[float | None, float | None]:
    parsed: list[tuple[float, float]] = []
    if isinstance(levels, list):
        for row in levels:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            p, q = _f(row[0]), _f(row[1])
            if p is not None and q is not None and 0 <= p <= 1 and q > 0:
                parsed.append((p, q))
    if not parsed:
        return None, None
    return max(parsed, key=lambda x: x[0])


def fetch_orderbook(http: httpx.Client, ticker: str) -> dict[str, Any]:
    response = http.get(f"{API}/markets/{ticker}/orderbook", params={"depth": 10})
    response.raise_for_status()
    payload = response.json()
    book = payload.get("orderbook_fp") if isinstance(payload, dict) else None
    if not isinstance(book, dict):
        raise ValueError(f"unexpected orderbook payload for {ticker}")
    yes_bid, yes_size = _best(book.get("yes_dollars"))
    no_bid, no_size = _best(book.get("no_dollars"))
    yes_ask = None if no_bid is None else 1.0 - no_bid
    no_ask = None if yes_bid is None else 1.0 - yes_bid
    return {
        "yes_bid": yes_bid,
        "yes_bid_size": yes_size,
        "no_bid": no_bid,
        "no_bid_size": no_size,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "yes_spread": None if yes_bid is None or yes_ask is None else max(0.0, yes_ask - yes_bid),
    }


def current_markets(http: httpx.Client) -> list[base.KalshiBinaryMarket]:
    markets: list[base.KalshiBinaryMarket] = []
    now_ms = int(time.time() * 1000)
    for asset in FIRST_TIER_ASSETS:
        series = fetch_series_certified(http, asset)
        rows = base.fetch_open_markets(http, series, future_minutes=20)
        live = [m for m in rows if m.close_ts_ms is None or m.close_ts_ms > now_ms]
        if live:
            markets.append(min(live, key=lambda m: m.close_ts_ms or 2**63))
    return markets


def run(*, poll_seconds: float, discovery_seconds: float, incentive_seconds: float, summary_seconds: float) -> None:
    markets: list[base.KalshiBinaryMarket] = []
    incentives: list[Incentive] = []
    next_discovery = 0.0
    next_incentives = 0.0
    next_summary = 0.0
    samples = 0
    book_errors = 0

    with httpx.Client(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-kalshi-incentive-shadow/1.0"}) as http:
        while True:
            cycle = time.time()
            if cycle >= next_discovery:
                try:
                    markets = current_markets(http)
                    _emit({
                        "type": "KALSHI_INCENTIVE_MARKET_DISCOVERY",
                        "markets": len(markets),
                        "streams": sorted({m.stream.name for m in markets}),
                        "tickers": {m.stream.name: m.ticker for m in markets},
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                except Exception as exc:
                    _emit({
                        "type": "KALSHI_INCENTIVE_DISCOVERY_ERROR",
                        "error": type(exc).__name__,
                        "message": str(exc)[:400],
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                next_discovery = cycle + discovery_seconds

            if cycle >= next_incentives:
                try:
                    incentives = fetch_incentives(http)
                    short_prefixes = tuple(base.SERIES_BY_ASSET.values())
                    short_rows = [i for i in incentives if i.market_ticker.startswith(short_prefixes)]
                    _emit({
                        "type": "KALSHI_INCENTIVE_REFRESH",
                        "active_programs_all": len(incentives),
                        "active_short_crypto_programs": len(short_rows),
                        "headline_period_reward_sum_short_crypto": sum(i.period_reward or 0.0 for i in short_rows),
                        "interpretation": "Headline period_reward is venue program configuration, not our expected reward.",
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                except Exception as exc:
                    _emit({
                        "type": "KALSHI_INCENTIVE_REFRESH_ERROR",
                        "error": type(exc).__name__,
                        "message": str(exc)[:400],
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                next_incentives = cycle + incentive_seconds

            rows: dict[str, dict[str, Any]] = {}
            for market in markets:
                matching = [i for i in incentives if i.market_ticker == market.ticker]
                try:
                    book = fetch_orderbook(http, market.ticker)
                    samples += 1
                except Exception as exc:
                    book_errors += 1
                    rows[market.stream.name] = {
                        "market_ticker": market.ticker,
                        "book_error": f"{type(exc).__name__}: {str(exc)[:180]}",
                        "active_incentives": len(matching),
                    }
                    continue

                incentive_rows: list[dict[str, Any]] = []
                for inc in matching:
                    target = inc.target_size
                    target_met = (
                        target is not None
                        and book["yes_bid_size"] is not None
                        and book["no_bid_size"] is not None
                        and float(book["yes_bid_size"]) >= target
                        and float(book["no_bid_size"]) >= target
                    )
                    incentive_rows.append({
                        "id": inc.id,
                        "type": inc.incentive_type,
                        "description": inc.description,
                        "period_reward": inc.period_reward,
                        "discount_factor_bps": inc.discount_factor_bps,
                        "target_size": target,
                        "target_size_met_at_top_bids": target_met,
                        "start_date": inc.start_date,
                        "end_date": inc.end_date,
                    })

                rows[market.stream.name] = {
                    "market_ticker": market.ticker,
                    "close_ts_ms": market.close_ts_ms,
                    "yes_bid": book["yes_bid"],
                    "yes_bid_size": book["yes_bid_size"],
                    "yes_ask": book["yes_ask"],
                    "no_bid": book["no_bid"],
                    "no_bid_size": book["no_bid_size"],
                    "no_ask": book["no_ask"],
                    "yes_spread": book["yes_spread"],
                    "active_incentives": incentive_rows,
                }

            if cycle >= next_summary:
                _emit({
                    "type": "KALSHI_INCENTIVE_SHADOW_SUMMARY",
                    "streams": sorted(rows),
                    "by_stream": rows,
                    "book_samples": samples,
                    "book_errors": book_errors,
                    "headline_period_reward_sum_visible": sum(
                        float(i["period_reward"] or 0.0)
                        for row in rows.values()
                        for i in row.get("active_incentives", [])
                        if isinstance(i, dict)
                    ),
                    "interpretation": "Visible book + active incentive metadata only. Reward share is unmodeled; top-bid target check is not official Kalshi eligibility.",
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                next_summary = cycle + summary_seconds

            time.sleep(max(0.25, poll_seconds - (time.time() - cycle)))


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Kalshi short-crypto incentive shadow")
    p.add_argument("--poll-seconds", type=float, default=5.0)
    p.add_argument("--discovery-seconds", type=float, default=15.0)
    p.add_argument("--incentive-seconds", type=float, default=30.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    args = p.parse_args()
    run(
        poll_seconds=max(1.0, args.poll_seconds),
        discovery_seconds=max(10.0, args.discovery_seconds),
        incentive_seconds=max(10.0, args.incentive_seconds),
        summary_seconds=max(5.0, args.summary_seconds),
    )


if __name__ == "__main__":
    main()
