"""Read-only maker-first / taker-hedge feasibility for nested Kalshi ladders.

Direct two-taker nested arbitrage is usually priced at >= $1 before fees. A safer
execution variant is to rest ONE logically nested leg, then only after that maker
fill immediately take the complementary leg. Once both legs fill, the same-event
settlement payoff is guaranteed; before the first fill there is no position.

This scanner does NOT place orders. It asks a narrower question:

    Can a 1-contract order JOIN the current best bid as maker, and if filled now,
    could the complementary top ask be taken immediately while still locking at
    least ``target_profit`` after the mapped taker fee?

Only ``fee_type='quadratic'`` is supported here. Kalshi exposes a separate
``quadratic_with_maker_fees`` type; those events fail closed because this research
assumes zero maker fee for the resting leg.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
from typing import Any

import httpx

from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata, kalshi_quadratic_taker_fee
from src.kalshi_nested_strike_shadow import D, _dec, _event_fee, _get_json, opposite_ask_ladder

logger = logging.getLogger("kalshi_arb.kalshi_nested_maker_shadow")


@dataclass(frozen=True)
class Q:
    ticker: str
    event_ticker: str
    strike_type: str
    strike: Decimal
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    title: str


def _parse(row: dict[str, Any]) -> Q | None:
    st = str(row.get("strike_type") or "").strip().lower()
    if st == "greater":
        strike = _dec(row.get("floor_strike"))
    elif st == "less":
        strike = _dec(row.get("cap_strike"))
    else:
        return None
    ticker = str(row.get("ticker") or "")
    event_ticker = str(row.get("event_ticker") or "")
    if strike is None or not ticker or not event_ticker:
        return None
    return Q(
        ticker=ticker,
        event_ticker=event_ticker,
        strike_type=st,
        strike=strike,
        yes_bid=_dec(row.get("yes_bid_dollars")),
        yes_ask=_dec(row.get("yes_ask_dollars")),
        no_bid=_dec(row.get("no_bid_dollars")),
        no_ask=_dec(row.get("no_ask_dollars")),
        title=str(row.get("title") or ""),
    )


def maker_then_taker_profit(
    *,
    maker_price: Decimal,
    hedge_price: Decimal,
    fee_multiplier: Decimal,
    quantity: Decimal = D("1"),
    maker_fee: Decimal = D("0"),
) -> tuple[Decimal, Decimal]:
    if quantity <= 0 or maker_price < 0 or maker_price > 1 or hedge_price < 0 or hedge_price > 1:
        raise ValueError("invalid maker/taker inputs")
    taker_fee = kalshi_quadratic_taker_fee(
        hedge_price,
        quantity,
        fee_multiplier=fee_multiplier,
    )
    min_payout = quantity
    total_cost = (maker_price + hedge_price) * quantity + maker_fee + taker_fee
    return min_payout - total_cost, taker_fee


def max_maker_price_for_target(
    *,
    hedge_price: Decimal,
    fee_multiplier: Decimal,
    target_profit: Decimal,
    quantity: Decimal = D("1"),
) -> Decimal:
    if target_profit < 0:
        raise ValueError("target profit cannot be negative")
    taker_fee = kalshi_quadratic_taker_fee(
        hedge_price,
        quantity,
        fee_multiplier=fee_multiplier,
    )
    return (quantity - hedge_price * quantity - taker_fee - target_profit) / quantity


def _orientation(lower: Q, higher: Q) -> tuple[tuple[Q, str], tuple[Q, str]]:
    """Return the two nested bought legs in strike order.

    greater: YES(lower) + NO(higher)
    less:    NO(lower) + YES(higher)
    """
    if lower.strike_type != higher.strike_type or not lower.strike < higher.strike:
        raise ValueError("invalid nested orientation")
    if lower.strike_type == "greater":
        return (lower, "yes"), (higher, "no")
    if lower.strike_type == "less":
        return (lower, "no"), (higher, "yes")
    raise ValueError("unsupported strike type")


def _bid(q: Q, side: str) -> Decimal | None:
    return q.yes_bid if side == "yes" else q.no_bid


def _ask(q: Q, side: str) -> Decimal | None:
    return q.yes_ask if side == "yes" else q.no_ask


def _best_bid_from_book(book: dict[str, Any], side: str) -> Decimal | None:
    fp = book.get("orderbook_fp") or {}
    raw = fp.get(f"{side}_dollars") or []
    prices = [_dec(row[0]) for row in raw if isinstance(row, (list, tuple)) and len(row) >= 2]
    clean = [p for p in prices if p is not None]
    return max(clean) if clean else None


async def scan_maker_series(
    series_ticker: str,
    *,
    target_profit: Decimal = D("0.01"),
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
        strike_types = Counter(str(row.get("strike_type") or "<missing>") for row in raw)
        quotes = [q for row in raw if (q := _parse(row)) is not None]
        groups: dict[tuple[str, str], list[Q]] = defaultdict(list)
        for q in quotes:
            groups[(q.event_ticker, q.strike_type)].append(q)

        summary = {
            "series": series_ticker,
            "open_markets": len(raw),
            "strike_type_counts": dict(strike_types),
            "supported_markets": len(quotes),
            "nested_pairs_checked": 0,
            "maker_join_feasible_top": 0,
            "maker_join_verified_depth": 0,
            "target_profit": str(target_profit),
            "closest_maker_rows": [],
            "fee_metadata": fee_meta.as_dict(),
            "maker_fee_assumption": "ZERO_ONLY_WHEN_FEE_TYPE_QUADRATIC",
            "read_only": True,
            "order_entry": False,
        }
        closest: list[dict[str, Any]] = []

        for (event_ticker, strike_type), event_quotes in sorted(groups.items()):
            event_quotes.sort(key=lambda q: q.strike)
            fee_type, fee_multiplier, event = await _event_fee(http, event_ticker, fee_meta)
            if fee_type != "quadratic":
                continue

            for i, lower in enumerate(event_quotes):
                for higher in event_quotes[i + 1:]:
                    summary["nested_pairs_checked"] += 1
                    leg1, leg2 = _orientation(lower, higher)
                    # Either logical leg can be the maker; the other becomes the
                    # immediate taker hedge after the maker fill.
                    for maker_leg, hedge_leg in ((leg1, leg2), (leg2, leg1)):
                        maker_q, maker_side = maker_leg
                        hedge_q, hedge_side = hedge_leg
                        maker_bid = _bid(maker_q, maker_side)
                        maker_ask = _ask(maker_q, maker_side)
                        hedge_ask = _ask(hedge_q, hedge_side)
                        if maker_bid is None or maker_ask is None or hedge_ask is None:
                            continue
                        if maker_bid >= maker_ask:
                            continue
                        profit_at_join, taker_fee = maker_then_taker_profit(
                            maker_price=maker_bid,
                            hedge_price=hedge_ask,
                            fee_multiplier=fee_multiplier,
                        )
                        max_maker = max_maker_price_for_target(
                            hedge_price=hedge_ask,
                            fee_multiplier=fee_multiplier,
                            target_profit=target_profit,
                        )
                        row = {
                            "series": series_ticker,
                            "event_ticker": event_ticker,
                            "event_title": str(event.get("title") or ""),
                            "strike_type": strike_type,
                            "lower_strike": str(lower.strike),
                            "higher_strike": str(higher.strike),
                            "maker_ticker": maker_q.ticker,
                            "maker_side": maker_side,
                            "maker_best_bid": str(maker_bid),
                            "maker_best_ask": str(maker_ask),
                            "hedge_ticker": hedge_q.ticker,
                            "hedge_side": hedge_side,
                            "hedge_top_ask": str(hedge_ask),
                            "taker_fee_q1": str(taker_fee),
                            "profit_if_join_bid_fills_then_hedge": str(profit_at_join),
                            "max_maker_price_for_target": str(max_maker),
                            "headroom_vs_current_bid": str(max_maker - maker_bid),
                            "target_profit": str(target_profit),
                            "maker_fee_assumed": "0",
                            "fee_type": fee_type,
                            "fee_multiplier": str(fee_multiplier),
                            "read_only": True,
                            "order_entry": False,
                        }
                        closest.append(row)
                        if profit_at_join < target_profit:
                            continue
                        summary["maker_join_feasible_top"] += 1
                        logger.info("NESTED_MAKER_TOP %s", json.dumps(row, separators=(",", ":")))

                        # Verify the exact current best maker bid and executable hedge
                        # depth using public orderbooks before classifying as ready.
                        maker_book, hedge_book = await asyncio.gather(
                            _get_json(http, f"/markets/{maker_q.ticker}/orderbook"),
                            _get_json(http, f"/markets/{hedge_q.ticker}/orderbook"),
                        )
                        exact_bid = _best_bid_from_book(maker_book, maker_side)
                        hedge_ladder = opposite_ask_ladder(hedge_book, buy_side=hedge_side)
                        if exact_bid is None or not hedge_ladder:
                            continue
                        exact_hedge_price, exact_hedge_size = hedge_ladder[0]
                        if exact_hedge_size < 1:
                            continue
                        exact_profit, exact_fee = maker_then_taker_profit(
                            maker_price=exact_bid,
                            hedge_price=exact_hedge_price,
                            fee_multiplier=fee_multiplier,
                        )
                        verified = {
                            **row,
                            "maker_best_bid_verified": str(exact_bid),
                            "hedge_ask_verified": str(exact_hedge_price),
                            "hedge_top_size_verified": str(exact_hedge_size),
                            "taker_fee_verified": str(exact_fee),
                            "locked_profit_if_maker_fills_now": str(exact_profit),
                            "classification": "MAKER_FILL_REQUIRED_THEN_IMMEDIATE_TAKER_HEDGE",
                            "not_guaranteed_to_fill": True,
                        }
                        if exact_profit >= target_profit:
                            summary["maker_join_verified_depth"] += 1
                            logger.info("NESTED_MAKER_READY %s", json.dumps(verified, separators=(",", ":")))

        closest.sort(
            key=lambda row: D(row["profit_if_join_bid_fills_then_hedge"]),
            reverse=True,
        )
        summary["closest_maker_rows"] = closest[:15]
        logger.info("NESTED_MAKER_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
        return summary


async def _run(series: list[str], target_profit: Decimal) -> None:
    for i, ticker in enumerate(series):
        try:
            await scan_maker_series(ticker, target_profit=target_profit)
        except Exception as exc:
            logger.exception("NESTED_MAKER_SERIES_ERROR series=%s error=%s", ticker, str(exc)[:240])
        if i + 1 < len(series):
            await asyncio.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", nargs="+", default=["KXBTCD", "KXETHD", "KXSOLD"])
    parser.add_argument("--target-profit", type=Decimal, default=D("0.01"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(_run([s.upper() for s in args.series], args.target_profit))


if __name__ == "__main__":
    main()
