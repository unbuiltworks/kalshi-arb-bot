"""Read-only same-event nested-strike arbitrage scanner for Kalshi.

For two markets in the SAME event with the SAME ``strike_type='greater'`` and
numeric strikes A < B:

    buy YES(A) + buy NO(B)

has a guaranteed minimum settlement payout of $1 per paired contract:

* X <= A      -> NO(B) pays $1
* A < X <= B  -> YES(A) + NO(B) both pay, total $2
* X > B       -> YES(A) pays $1

Unlike cross-venue complementary trades, both legs use the same event/settlement
source, so there is no cross-index basis mismatch. The remaining risks are execution
(partial fills / stale books), fees, and market-rule correctness.

Safety / load properties:
- public read-only REST only; no order-entry imports
- one explicitly requested series at a time
- groups only within identical event_ticker
- supports only exact strike_type == 'greater'; everything else fails closed
- full orderbooks are fetched only when top-of-book gross cost is already < $1
- q=1..N is swept through executable depth
- quadratic taker fees use live series/event fee metadata and are conservatively
  rounded per consumed level; unknown fee types fail closed
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from typing import Any, Iterable

import httpx

from src.gemini_kalshi_fast_overlap import KALSHI_REST
from src.gemini_kalshi_kalshi_fees import (
    KalshiSeriesFeeMetadata,
    fetch_series_fee_metadata,
    kalshi_quadratic_taker_fee,
)

D = Decimal
logger = logging.getLogger("kalshi_arb.kalshi_nested_strike_shadow")


@dataclass(frozen=True)
class MarketLeg:
    ticker: str
    event_ticker: str
    strike: Decimal
    strike_type: str
    title: str
    yes_ask: Decimal | None
    no_ask: Decimal | None
    close_time: str
    rules_primary: str
    rules_secondary: str


@dataclass(frozen=True)
class SweepResult:
    quantity: Decimal
    cost: Decimal
    fee: Decimal
    worst_payout: Decimal
    profit: Decimal


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


def _parse_market(row: dict[str, Any]) -> MarketLeg | None:
    strike_type = str(row.get("strike_type") or "").lower().strip()
    strike = _dec(row.get("floor_strike"))
    ticker = str(row.get("ticker") or "")
    event_ticker = str(row.get("event_ticker") or "")
    if strike_type != "greater" or strike is None or not ticker or not event_ticker:
        return None
    return MarketLeg(
        ticker=ticker,
        event_ticker=event_ticker,
        strike=strike,
        strike_type=strike_type,
        title=str(row.get("title") or ""),
        yes_ask=_dec(row.get("yes_ask_dollars")),
        no_ask=_dec(row.get("no_ask_dollars")),
        close_time=str(row.get("close_time") or ""),
        rules_primary=str(row.get("rules_primary") or ""),
        rules_secondary=str(row.get("rules_secondary") or ""),
    )


def guaranteed_payout_per_pair(lower_strike: Decimal, higher_strike: Decimal, x: Decimal) -> Decimal:
    """Return settlement payout of YES(lower)+NO(higher), ignoring fees/cost."""
    if not lower_strike < higher_strike:
        raise ValueError("nested strikes require lower < higher")
    yes_lower = D("1") if x > lower_strike else D("0")
    no_higher = D("1") if x <= higher_strike else D("0")
    return yes_lower + no_higher


def _level_rows(book: dict[str, Any], side: str) -> list[tuple[Decimal, Decimal]]:
    fp = book.get("orderbook_fp") or {}
    raw = fp.get(f"{side}_dollars") or []
    out: list[tuple[Decimal, Decimal]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price, size = _dec(row[0]), _dec(row[1])
        if price is None or size is None or price < 0 or price > 1 or size <= 0:
            continue
        out.append((price, size))
    return out


def opposite_ask_ladder(book: dict[str, Any], *, buy_side: str) -> list[tuple[Decimal, Decimal]]:
    """Convert Kalshi's bid-only book into executable asks for the side we buy.

    Buying YES consumes resting NO bids: YES ask = 1 - NO bid.
    Buying NO consumes resting YES bids: NO ask = 1 - YES bid.
    """
    if buy_side not in {"yes", "no"}:
        raise ValueError("buy_side must be yes or no")
    bid_side = "no" if buy_side == "yes" else "yes"
    asks = [(D("1") - bid, size) for bid, size in _level_rows(book, bid_side)]
    asks.sort(key=lambda row: row[0])
    return asks


def sweep_ladder(
    ladder: Iterable[tuple[Decimal, Decimal]],
    quantity: Decimal,
    *,
    fee_multiplier: Decimal,
) -> tuple[Decimal, Decimal] | None:
    """Return (notional cost, conservative taker fee), or None for insufficient depth.

    Fees are computed separately per consumed book level and rounded with the existing
    mapped quadratic fee function. Current Kalshi fill-level fee accumulation can be
    less punitive than this; over-rounding here is intentional for screening.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    remaining = quantity
    cost = D("0")
    fee = D("0")
    for price, size in ladder:
        if remaining <= 0:
            break
        take = min(remaining, size)
        if take <= 0:
            continue
        cost += price * take
        fee += kalshi_quadratic_taker_fee(
            price,
            take,
            fee_multiplier=fee_multiplier,
        )
        remaining -= take
    if remaining > 0:
        return None
    return cost, fee


def evaluate_depth_pair(
    *,
    lower: MarketLeg,
    higher: MarketLeg,
    lower_book: dict[str, Any],
    higher_book: dict[str, Any],
    q_max: int,
    fee_multiplier: Decimal,
) -> list[SweepResult]:
    if lower.event_ticker != higher.event_ticker:
        raise ValueError("nested pair must share event_ticker")
    if lower.strike_type != "greater" or higher.strike_type != "greater":
        raise ValueError("only greater strikes are supported")
    if not lower.strike < higher.strike:
        raise ValueError("lower strike must be below higher strike")

    yes_lower = opposite_ask_ladder(lower_book, buy_side="yes")
    no_higher = opposite_ask_ladder(higher_book, buy_side="no")
    results: list[SweepResult] = []
    for q_int in range(1, q_max + 1):
        q = D(q_int)
        yes_sweep = sweep_ladder(yes_lower, q, fee_multiplier=fee_multiplier)
        no_sweep = sweep_ladder(no_higher, q, fee_multiplier=fee_multiplier)
        if yes_sweep is None or no_sweep is None:
            continue
        yes_cost, yes_fee = yes_sweep
        no_cost, no_fee = no_sweep
        cost = yes_cost + no_cost
        fee = yes_fee + no_fee
        worst_payout = q
        results.append(SweepResult(
            quantity=q,
            cost=cost,
            fee=fee,
            worst_payout=worst_payout,
            profit=worst_payout - cost - fee,
        ))
    return results


def _scheduled_fee_rows(meta: KalshiSeriesFeeMetadata) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for row in meta.scheduled_changes:
        raw = row.get("scheduled_ts")
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        if when > now:
            out.append(row)
    return out


async def _get_json(http: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response: httpx.Response | None = None
    for attempt in range(6):
        response = await http.get(f"{KALSHI_REST}{path}", params=params)
        if response.status_code != 429:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"unexpected non-object response for {path}")
            return payload
        await asyncio.sleep(min(4.0, 0.25 * (2 ** attempt)))
    assert response is not None
    response.raise_for_status()
    return {}


async def _event_fee(
    http: httpx.AsyncClient,
    event_ticker: str,
    series_meta: KalshiSeriesFeeMetadata,
) -> tuple[str, Decimal, dict[str, Any]]:
    payload = await _get_json(http, f"/events/{event_ticker}")
    event = payload.get("event") or {}
    fee_type = str(event.get("fee_type_override") or series_meta.fee_type or "").strip()
    override = event.get("fee_multiplier_override")
    multiplier = _dec(override if override is not None else series_meta.fee_multiplier)
    if multiplier is None or multiplier < 0:
        raise ValueError(f"missing/invalid fee multiplier for {event_ticker}")
    return fee_type, multiplier, event


async def scan_series(series_ticker: str, *, q_max: int = 5) -> dict[str, Any]:
    series_ticker = series_ticker.upper()
    async with httpx.AsyncClient(timeout=20.0) as http:
        series_meta = await fetch_series_fee_metadata(http, series_ticker)
        markets_payload = await _get_json(
            http,
            "/markets",
            params={
                "series_ticker": series_ticker,
                "status": "open",
                "limit": 1000,
                "mve_filter": "exclude",
            },
        )
        raw_markets = list(markets_payload.get("markets") or [])
        parsed = [m for row in raw_markets if (m := _parse_market(row)) is not None]
        groups: dict[str, list[MarketLeg]] = defaultdict(list)
        for market in parsed:
            groups[market.event_ticker].append(market)

        summary: dict[str, Any] = {
            "series_ticker": series_ticker,
            "fee_metadata": series_meta.as_dict(),
            "future_fee_changes": _scheduled_fee_rows(series_meta),
            "open_markets": len(raw_markets),
            "supported_greater_markets": len(parsed),
            "events": len(groups),
            "nested_pairs_checked": 0,
            "gross_top_positive_pairs": 0,
            "depth_pairs_fetched": 0,
            "net_positive_q_rows": 0,
            "closest_pairs": [],
            "read_only": True,
            "order_entry": False,
        }
        closest: list[dict[str, Any]] = []

        for event_ticker, event_markets in sorted(groups.items()):
            event_markets.sort(key=lambda m: m.strike)
            try:
                fee_type, fee_multiplier, event = await _event_fee(http, event_ticker, series_meta)
            except Exception as exc:
                logger.warning(
                    "NESTED_STRIKE_EVENT_SKIP event=%s reason=fee_metadata_error error=%s",
                    event_ticker,
                    str(exc)[:240],
                )
                continue
            if fee_type != "quadratic":
                logger.info(
                    "NESTED_STRIKE_EVENT_SKIP %s",
                    json.dumps({
                        "series": series_ticker,
                        "event_ticker": event_ticker,
                        "reason": "UNMAPPED_FEE_TYPE",
                        "fee_type": fee_type,
                        "fee_multiplier": str(fee_multiplier),
                        "read_only": True,
                    }, separators=(",", ":")),
                )
                continue

            for i, lower in enumerate(event_markets):
                for higher in event_markets[i + 1:]:
                    summary["nested_pairs_checked"] += 1
                    if lower.yes_ask is None or higher.no_ask is None:
                        continue
                    gross_cost = lower.yes_ask + higher.no_ask
                    gross_edge = D("1") - gross_cost
                    candidate = {
                        "series": series_ticker,
                        "event_ticker": event_ticker,
                        "event_title": str(event.get("title") or ""),
                        "lower_ticker": lower.ticker,
                        "lower_strike": str(lower.strike),
                        "lower_yes_ask": str(lower.yes_ask),
                        "higher_ticker": higher.ticker,
                        "higher_strike": str(higher.strike),
                        "higher_no_ask": str(higher.no_ask),
                        "top_gross_cost": str(gross_cost),
                        "top_gross_edge": str(gross_edge),
                        "fee_type": fee_type,
                        "fee_multiplier": str(fee_multiplier),
                        "same_event": True,
                        "strike_type": "greater",
                        "guaranteed_min_payout_per_pair": "1",
                        "read_only": True,
                        "order_entry": False,
                    }
                    closest.append(candidate)
                    if gross_edge <= 0:
                        continue

                    summary["gross_top_positive_pairs"] += 1
                    logger.info(
                        "NESTED_STRIKE_GROSS_PAIR %s",
                        json.dumps(candidate, separators=(",", ":")),
                    )
                    # A deeper book cannot improve on top asks; only gross-positive
                    # pairs have any possibility of remaining positive after fees.
                    lower_book, higher_book = await asyncio.gather(
                        _get_json(http, f"/markets/{lower.ticker}/orderbook"),
                        _get_json(http, f"/markets/{higher.ticker}/orderbook"),
                    )
                    summary["depth_pairs_fetched"] += 1
                    rows = evaluate_depth_pair(
                        lower=lower,
                        higher=higher,
                        lower_book=lower_book,
                        higher_book=higher_book,
                        q_max=q_max,
                        fee_multiplier=fee_multiplier,
                    )
                    for row in rows:
                        payload = {
                            **candidate,
                            "quantity": str(row.quantity),
                            "depth_cost": str(row.cost),
                            "conservative_taker_fees": str(row.fee),
                            "guaranteed_min_payout": str(row.worst_payout),
                            "guaranteed_net_profit": str(row.profit),
                            "fee_rounding": "PER_CONSUMED_LEVEL_CENT_CEILING_CONSERVATIVE",
                        }
                        if row.profit > 0:
                            summary["net_positive_q_rows"] += 1
                            logger.info(
                                "NESTED_STRIKE_ARB %s",
                                json.dumps(payload, separators=(",", ":")),
                            )
                        elif row.profit == 0:
                            logger.info(
                                "NESTED_STRIKE_BREAK_EVEN %s",
                                json.dumps(payload, separators=(",", ":")),
                            )

        closest.sort(key=lambda row: D(row["top_gross_edge"]), reverse=True)
        summary["closest_pairs"] = closest[:10]
        logger.info("NESTED_STRIKE_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
        return summary


async def _run(series: list[str], q_max: int) -> None:
    for index, ticker in enumerate(series):
        try:
            await scan_series(ticker, q_max=q_max)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "NESTED_STRIKE_SERIES_ERROR series=%s status=%s error=%s",
                ticker,
                exc.response.status_code,
                str(exc)[:240],
            )
        except Exception as exc:
            logger.exception("NESTED_STRIKE_SERIES_ERROR series=%s error=%s", ticker, str(exc)[:240])
        if index + 1 < len(series):
            await asyncio.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", nargs="+", default=["KXBTCD"])
    parser.add_argument("--q-max", type=int, default=5)
    args = parser.parse_args()
    if args.q_max < 1 or args.q_max > 50:
        raise ValueError("q-max must be between 1 and 50")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(_run([s.upper() for s in args.series], args.q_max))


if __name__ == "__main__":
    main()
