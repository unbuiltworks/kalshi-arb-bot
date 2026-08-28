"""Read-only l5-style Kalshi 15-minute BTC/ETH threshold shadow.

The runner discovers the nearest open KXBTC15M and KXETH15M markets, samples
public depth-1 order books, applies the simple 60% favored-side one-tick model,
and attaches an exact-market incentive row when one is publicly active.

Safety:
- public REST only;
- no Kalshi credentials;
- no order-entry imports;
- no fills are assumed;
- pair margin is PRE_FEE_ONLY and never realized P&L;
- incentive rows are eligibility context, never accrued/realized rewards.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import time
from typing import Any

import httpx

from src.gemini_kalshi_live_shadow import KALSHI_REST, kalshi_book_once
from src.gemini_kalshi_window_identity import kalshi_window_end
from src.kalshi_l5_threshold_model import DEFAULT_THRESHOLD, plan_kalshi_threshold_quotes
from src.short_crypto_incentive_snapshot import fetch_snapshot

SERIES = ("KXBTC15M", "KXETH15M")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _nearest_open_market(http: httpx.AsyncClient, series: str) -> dict[str, Any] | None:
    response = await http.get(
        f"{KALSHI_REST}/markets",
        params={"series_ticker": series, "status": "open", "limit": 100, "mve_filter": "exclude"},
    )
    response.raise_for_status()
    now = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for row in response.json().get("markets", []):
        close = kalshi_window_end(row)
        ticker = str(row.get("ticker") or "")
        if close is None or close <= now or not ticker:
            continue
        candidates.append((close, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _incentive_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("kalshi", {}).get("target_15m_incentives", [])
    return {
        str(row.get("market_ticker") or ""): row
        for row in rows
        if str(row.get("market_ticker") or "")
    }


async def capture(*, seconds: float, poll_s: float, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    stats: dict[str, dict[str, int]] = {
        series: {"samples": 0, "threshold_samples": 0, "exact_incentive_samples": 0, "errors": 0}
        for series in SERIES
    }
    last_identity: dict[str, str] = {}
    incentive_snapshot = await fetch_snapshot()
    incentives = _incentive_map(incentive_snapshot)

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={"User-Agent": "arb-kalshi-l5-threshold-shadow/1.0"}) as http:
        with output.open("w", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                loop_started = time.monotonic()
                for series in SERIES:
                    try:
                        market = await _nearest_open_market(http, series)
                        if market is None:
                            stats[series]["errors"] += 1
                            continue
                        ticker = str(market["ticker"])
                        close = kalshi_window_end(market)
                        if close is None:
                            stats[series]["errors"] += 1
                            continue
                        book = await kalshi_book_once(http, ticker)
                        plan = plan_kalshi_threshold_quotes(book.yes_bid, book.yes_ask)
                        incentive = incentives.get(ticker)
                        seconds_to_close = max(0.0, (close - datetime.now(timezone.utc)).total_seconds())
                        row = {
                            "type": "KALSHI_L5_THRESHOLD_SHADOW",
                            "observed_at": _iso_now(),
                            "series": series,
                            "ticker": ticker,
                            "close_time": close.isoformat(),
                            "seconds_to_close": round(seconds_to_close, 3),
                            "yes_bid": str(book.yes_bid),
                            "yes_ask": str(book.yes_ask),
                            "yes_mid": str(plan.yes_mid),
                            "no_mid": str(plan.no_mid),
                            "favored_side": plan.favored_side,
                            "favored_probability": str(plan.favored_probability),
                            "threshold": str(DEFAULT_THRESHOLD),
                            "shadow_yes_quote": str(plan.yes_quote),
                            "shadow_no_quote": str(plan.no_quote),
                            "quote_skew_ticks": plan.quote_skew_ticks,
                            "shadow_target_units": plan.shadow_target_units,
                            "pair_cost_if_both_fill": str(plan.pair_cost_if_both_fill),
                            "pre_fee_pair_margin": str(plan.pre_fee_pair_margin),
                            "fee_status": "UNMODELED_DO_NOT_COUNT",
                            "fill_status": "NO_FILL_ASSUMED",
                            "trading_pnl_status": "SHADOW_NOT_REALIZED",
                            "exact_market_incentive_active": incentive is not None,
                            "incentive": incentive,
                            "incentive_status": "ELIGIBILITY_CONTEXT_ONLY_NOT_ACCRUED",
                            "read_only": True,
                            "order_entry": False,
                        }
                        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
                        fh.flush()
                        stats[series]["samples"] += 1
                        if plan.favored_side is not None:
                            stats[series]["threshold_samples"] += 1
                        if incentive is not None:
                            stats[series]["exact_incentive_samples"] += 1
                        identity = f"{ticker}:{plan.favored_side}:{plan.yes_quote}:{plan.no_quote}:{bool(incentive)}"
                        if last_identity.get(series) != identity:
                            print(json.dumps(row, separators=(",", ":"), default=str), flush=True)
                            last_identity[series] = identity
                    except Exception as exc:
                        stats[series]["errors"] += 1
                        print(json.dumps({
                            "type": "KALSHI_L5_THRESHOLD_SHADOW_ERROR",
                            "observed_at": _iso_now(),
                            "series": series,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:240],
                            "read_only": True,
                            "order_entry": False,
                        }, separators=(",", ":")), flush=True)

                elapsed = time.monotonic() - loop_started
                await asyncio.sleep(max(0.05, poll_s - elapsed))

    summary = {
        "type": "KALSHI_L5_THRESHOLD_SHADOW_SUMMARY",
        "observed_at": _iso_now(),
        "seconds_requested": seconds,
        "poll_s": poll_s,
        "series": stats,
        "read_only": True,
        "order_entry": False,
        "realized_pnl": None,
    }
    print(json.dumps(summary, separators=(",", ":")), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/kalshi-l5-threshold-shadow.jsonl"))
    args = parser.parse_args()
    asyncio.run(capture(seconds=args.seconds, poll_s=args.poll_s, output=args.output))


if __name__ == "__main__":
    main()
