"""Read-only aggressive maker quote feasibility for same-event nested strikes.

Builds on ``kalshi_nested_maker_shadow`` but uses each market's own
``price_ranges[].step`` schedule to calculate the MOST aggressive valid resting bid
that:

1. strictly improves the current best bid (zero queue ahead at that price at post time),
2. remains strictly below the current ask (maker, not crossing), and
3. still preserves a requested guaranteed net profit after immediately taking the
   complementary nested hedge at its current ask.

No orders are placed. This only identifies shadow quotes worth monitoring for
trade-through / adverse-selection behavior.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from decimal import Decimal
import json
import logging
from typing import Any

import httpx

from src.kalshi_nested_maker_shadow import (
    D,
    Q,
    _ask,
    _bid,
    _orientation,
    _parse,
    maker_then_taker_profit,
    max_maker_price_for_target,
)
from src.kalshi_nested_strike_shadow import _dec, _event_fee, _get_json
from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata

logger = logging.getLogger("kalshi_arb.kalshi_nested_maker_aggressive_shadow")


def valid_price_levels(price_ranges: list[dict[str, Any]]) -> list[Decimal]:
    """Enumerate exact valid dollar price levels from Kalshi price ranges."""
    levels: set[Decimal] = set()
    for row in price_ranges or []:
        start = _dec(row.get("start"))
        end = _dec(row.get("end"))
        step = _dec(row.get("step"))
        if start is None or end is None or step is None or step <= 0 or end < start:
            continue
        price = start
        # Defensive bound: even 0.0001 ticks over [0,1] are only 10,001 levels.
        for _ in range(20_001):
            if price > end:
                break
            if D("0") <= price <= D("1"):
                levels.add(price)
            price += step
    return sorted(levels)


def most_aggressive_resting_price(
    *,
    best_bid: Decimal,
    best_ask: Decimal,
    max_price: Decimal,
    price_ranges: list[dict[str, Any]],
) -> Decimal | None:
    """Highest valid level that improves bid, stays maker, and preserves target."""
    if not D("0") <= best_bid < best_ask <= D("1"):
        return None
    ceiling = min(max_price, D("1"))
    candidates = [
        p for p in valid_price_levels(price_ranges)
        if p > best_bid and p < best_ask and p <= ceiling
    ]
    return max(candidates) if candidates else None


async def scan_aggressive_series(
    series_ticker: str,
    *,
    target_profit: Decimal,
    top_n: int = 30,
) -> dict[str, Any]:
    series_ticker = series_ticker.upper()
    async with httpx.AsyncClient(timeout=20.0) as http:
        fee_meta = await fetch_series_fee_metadata(http, series_ticker)
        payload = await _get_json(
            http,
            "/markets",
            params={
                "series_ticker": series_ticker,
                "status": "open",
                "limit": 1000,
                "mve_filter": "exclude",
            },
        )
        raw = list(payload.get("markets") or [])
        raw_by_ticker = {str(row.get("ticker") or ""): row for row in raw}
        quotes = [q for row in raw if (q := _parse(row)) is not None]
        groups: dict[tuple[str, str], list[Q]] = defaultdict(list)
        for quote in quotes:
            groups[(quote.event_ticker, quote.strike_type)].append(quote)

        rows: list[dict[str, Any]] = []
        pairs = 0
        for (event_ticker, strike_type), event_quotes in sorted(groups.items()):
            event_quotes.sort(key=lambda q: q.strike)
            fee_type, fee_multiplier, event = await _event_fee(http, event_ticker, fee_meta)
            if fee_type != "quadratic":
                continue
            for i, lower in enumerate(event_quotes):
                for higher in event_quotes[i + 1:]:
                    pairs += 1
                    leg1, leg2 = _orientation(lower, higher)
                    for maker_leg, hedge_leg in ((leg1, leg2), (leg2, leg1)):
                        maker_q, maker_side = maker_leg
                        hedge_q, hedge_side = hedge_leg
                        best_bid = _bid(maker_q, maker_side)
                        best_ask = _ask(maker_q, maker_side)
                        hedge_ask = _ask(hedge_q, hedge_side)
                        if best_bid is None or best_ask is None or hedge_ask is None:
                            continue
                        max_price = max_maker_price_for_target(
                            hedge_price=hedge_ask,
                            fee_multiplier=fee_multiplier,
                            target_profit=target_profit,
                        )
                        market_row = raw_by_ticker.get(maker_q.ticker) or {}
                        aggressive = most_aggressive_resting_price(
                            best_bid=best_bid,
                            best_ask=best_ask,
                            max_price=max_price,
                            price_ranges=list(market_row.get("price_ranges") or []),
                        )
                        if aggressive is None:
                            continue
                        profit, taker_fee = maker_then_taker_profit(
                            maker_price=aggressive,
                            hedge_price=hedge_ask,
                            fee_multiplier=fee_multiplier,
                        )
                        if profit < target_profit:
                            continue
                        rows.append({
                            "series": series_ticker,
                            "event_ticker": event_ticker,
                            "event_title": str(event.get("title") or ""),
                            "strike_type": strike_type,
                            "lower_strike": str(lower.strike),
                            "higher_strike": str(higher.strike),
                            "maker_ticker": maker_q.ticker,
                            "maker_side": maker_side,
                            "current_best_bid": str(best_bid),
                            "current_best_ask": str(best_ask),
                            "shadow_quote": str(aggressive),
                            "quote_improvement": str(aggressive - best_bid),
                            "distance_below_ask": str(best_ask - aggressive),
                            "max_price_for_target": str(max_price),
                            "price_level_structure": market_row.get("price_level_structure"),
                            "price_ranges": market_row.get("price_ranges") or [],
                            "hedge_ticker": hedge_q.ticker,
                            "hedge_side": hedge_side,
                            "hedge_top_ask": str(hedge_ask),
                            "hedge_taker_fee_q1": str(taker_fee),
                            "guaranteed_profit_if_shadow_quote_fills_and_hedge_still_available": str(profit),
                            "target_profit": str(target_profit),
                            "zero_queue_ahead_at_post_price": True,
                            "maker_fill_not_guaranteed": True,
                            "hedge_can_move_before_fill": True,
                            "read_only": True,
                            "order_entry": False,
                        })

        # Prefer quotes closest to the opposing ask (more aggressive / plausibly
        # fillable), then larger guaranteed buffer.
        rows.sort(
            key=lambda row: (
                D(row["distance_below_ask"]),
                -D(row["guaranteed_profit_if_shadow_quote_fills_and_hedge_still_available"]),
            )
        )
        summary = {
            "series": series_ticker,
            "target_profit": str(target_profit),
            "open_markets": len(raw),
            "nested_pairs_checked": pairs,
            "aggressive_shadow_quotes": len(rows),
            "top_quotes": rows[:top_n],
            "fee_metadata": fee_meta.as_dict(),
            "classification": "RESTING_MAKER_QUOTE_HYPOTHESIS_ONLY",
            "read_only": True,
            "order_entry": False,
        }
        for row in rows[:top_n]:
            logger.info("NESTED_AGGRESSIVE_QUOTE %s", json.dumps(row, separators=(",", ":"), default=str))
        logger.info("NESTED_AGGRESSIVE_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
        return summary


async def _run(series: list[str], targets: list[Decimal], top_n: int) -> None:
    for target in targets:
        for idx, ticker in enumerate(series):
            try:
                await scan_aggressive_series(ticker, target_profit=target, top_n=top_n)
            except Exception as exc:
                logger.exception(
                    "NESTED_AGGRESSIVE_SERIES_ERROR series=%s target=%s error=%s",
                    ticker, target, str(exc)[:240],
                )
            if idx + 1 < len(series):
                await asyncio.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", nargs="+", default=["KXBTCD", "KXETHD", "KXSOLD"])
    parser.add_argument("--target-profit", nargs="+", type=Decimal, default=[D("0.01"), D("0.03")])
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(_run([s.upper() for s in args.series], list(args.target_profit), args.top_n))


if __name__ == "__main__":
    main()
