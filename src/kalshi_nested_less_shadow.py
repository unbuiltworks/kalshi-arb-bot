"""Read-only nested-strike scanner for Kalshi ``strike_type='less'`` ladders.

For A < B on the SAME less-than event:

    buy NO(A) + buy YES(B)

has minimum payout $1 per pair:
- X < A       -> YES(B) pays $1
- A <= X < B  -> NO(A) + YES(B) both pay $2
- X >= B      -> NO(A) pays $1

This is the exact mirror of the greater-than scanner and keeps all the same
fee/depth/fail-closed constraints.
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

from src.kalshi_nested_strike_shadow import (
    D,
    _dec,
    _event_fee,
    _get_json,
    opposite_ask_ladder,
    sweep_ladder,
)
from src.gemini_kalshi_fast_overlap import KALSHI_REST
from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata

logger = logging.getLogger("kalshi_arb.kalshi_nested_less_shadow")


@dataclass(frozen=True)
class LessLeg:
    ticker: str
    event_ticker: str
    strike: Decimal
    no_ask: Decimal | None
    yes_ask: Decimal | None
    title: str


def guaranteed_less_payout(lower: Decimal, higher: Decimal, x: Decimal) -> Decimal:
    if not lower < higher:
        raise ValueError("lower must be below higher")
    no_lower = D("1") if x >= lower else D("0")
    yes_higher = D("1") if x < higher else D("0")
    return no_lower + yes_higher


def _parse_less(row: dict[str, Any]) -> LessLeg | None:
    if str(row.get("strike_type") or "").strip().lower() != "less":
        return None
    # Kalshi documents ``cap_strike`` as the numeric cap threshold; less-than markets
    # use that field. Do not fall back to another strike representation.
    strike = _dec(row.get("cap_strike"))
    ticker = str(row.get("ticker") or "")
    event_ticker = str(row.get("event_ticker") or "")
    if strike is None or not ticker or not event_ticker:
        return None
    return LessLeg(
        ticker=ticker,
        event_ticker=event_ticker,
        strike=strike,
        no_ask=_dec(row.get("no_ask_dollars")),
        yes_ask=_dec(row.get("yes_ask_dollars")),
        title=str(row.get("title") or ""),
    )


async def scan_less_series(series_ticker: str, *, q_max: int = 5) -> dict[str, Any]:
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
        legs = [leg for row in raw if (leg := _parse_less(row)) is not None]
        groups: dict[str, list[LessLeg]] = defaultdict(list)
        for leg in legs:
            groups[leg.event_ticker].append(leg)

        summary = {
            "series": series_ticker,
            "open_markets": len(raw),
            "strike_type_counts": dict(strike_types),
            "supported_less_markets": len(legs),
            "events": len(groups),
            "nested_pairs_checked": 0,
            "gross_top_positive_pairs": 0,
            "net_positive_q_rows": 0,
            "closest_pairs": [],
            "fee_metadata": fee_meta.as_dict(),
            "read_only": True,
            "order_entry": False,
        }
        closest: list[dict[str, Any]] = []

        for event_ticker, event_legs in sorted(groups.items()):
            event_legs.sort(key=lambda leg: leg.strike)
            fee_type, fee_multiplier, event = await _event_fee(http, event_ticker, fee_meta)
            if fee_type != "quadratic":
                logger.info(
                    "NESTED_LESS_EVENT_SKIP %s",
                    json.dumps({
                        "series": series_ticker,
                        "event_ticker": event_ticker,
                        "reason": "UNMAPPED_FEE_TYPE",
                        "fee_type": fee_type,
                        "read_only": True,
                    }, separators=(",", ":")),
                )
                continue

            for i, lower in enumerate(event_legs):
                for higher in event_legs[i + 1:]:
                    summary["nested_pairs_checked"] += 1
                    if lower.no_ask is None or higher.yes_ask is None:
                        continue
                    gross_cost = lower.no_ask + higher.yes_ask
                    gross_edge = D("1") - gross_cost
                    base = {
                        "series": series_ticker,
                        "event_ticker": event_ticker,
                        "event_title": str(event.get("title") or ""),
                        "lower_ticker": lower.ticker,
                        "lower_strike": str(lower.strike),
                        "lower_no_ask": str(lower.no_ask),
                        "higher_ticker": higher.ticker,
                        "higher_strike": str(higher.strike),
                        "higher_yes_ask": str(higher.yes_ask),
                        "top_gross_cost": str(gross_cost),
                        "top_gross_edge": str(gross_edge),
                        "fee_type": fee_type,
                        "fee_multiplier": str(fee_multiplier),
                        "same_event": True,
                        "strike_type": "less",
                        "guaranteed_min_payout_per_pair": "1",
                        "read_only": True,
                        "order_entry": False,
                    }
                    closest.append(base)
                    if gross_edge <= 0:
                        continue

                    summary["gross_top_positive_pairs"] += 1
                    logger.info("NESTED_LESS_GROSS_PAIR %s", json.dumps(base, separators=(",", ":")))
                    lower_book, higher_book = await asyncio.gather(
                        _get_json(http, f"/markets/{lower.ticker}/orderbook"),
                        _get_json(http, f"/markets/{higher.ticker}/orderbook"),
                    )
                    # NO(lower) consumes YES bids; YES(higher) consumes NO bids.
                    no_lower = opposite_ask_ladder(lower_book, buy_side="no")
                    yes_higher = opposite_ask_ladder(higher_book, buy_side="yes")
                    for q_int in range(1, q_max + 1):
                        q = D(q_int)
                        a = sweep_ladder(no_lower, q, fee_multiplier=fee_multiplier)
                        b = sweep_ladder(yes_higher, q, fee_multiplier=fee_multiplier)
                        if a is None or b is None:
                            continue
                        cost = a[0] + b[0]
                        fees = a[1] + b[1]
                        profit = q - cost - fees
                        row = {
                            **base,
                            "quantity": str(q),
                            "depth_cost": str(cost),
                            "conservative_taker_fees": str(fees),
                            "guaranteed_min_payout": str(q),
                            "guaranteed_net_profit": str(profit),
                            "fee_rounding": "PER_CONSUMED_LEVEL_CENT_CEILING_CONSERVATIVE",
                        }
                        if profit > 0:
                            summary["net_positive_q_rows"] += 1
                            logger.info("NESTED_LESS_ARB %s", json.dumps(row, separators=(",", ":")))

        closest.sort(key=lambda row: D(row["top_gross_edge"]), reverse=True)
        summary["closest_pairs"] = closest[:10]
        logger.info("NESTED_LESS_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
        return summary


async def _run(series: list[str], q_max: int) -> None:
    for i, ticker in enumerate(series):
        try:
            await scan_less_series(ticker, q_max=q_max)
        except Exception as exc:
            logger.exception("NESTED_LESS_SERIES_ERROR series=%s error=%s", ticker, str(exc)[:240])
        if i + 1 < len(series):
            await asyncio.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", nargs="+", default=["KXDOGED", "KXSHIBAD"])
    parser.add_argument("--q-max", type=int, default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(_run([s.upper() for s in args.series], args.q_max))


if __name__ == "__main__":
    main()
