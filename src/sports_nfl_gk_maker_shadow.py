"""Read-only NFL Gemini <-> Kalshi maker architecture comparison.

Compares, on the same exact counterpart keys:
- taker/taker;
- maker -> taker hedge, with the maker quote pushed as aggressively as possible
  without crossing the book or violating the configured q=1 profit floor;
- dual-maker with immediate taker fallback after either first fill.

This module never authenticates and cannot place orders.  It intentionally uses the
embedded Kalshi nested-event quote fields instead of per-market orderbook requests,
so a broad NFL screen does not add orderbook API pressure.  All rows remain
research-only until rule fingerprints and final direct depth/freshness gates pass in
a separate live execution path.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_CEILING
import json
import logging
import time
from typing import Any, Mapping

import httpx

# Importing the verified wrapper installs the case-aligned exact NFL matcher patch.
import src.sports_nfl_gk_price_scan_verified  # noqa: F401
import src.sports_nfl_gk_price_scan as core

D = Decimal
ONE = D("1")
ZERO = D("0")
CENT = D("0.01")
MAKER_RATE = D("0.0175")
MIN_FLOOR = D("0.03")
logger = logging.getLogger("kalshi_arb.sports_nfl_gk_maker_shadow")


def _ceil_cent(value: D) -> D:
    return (value * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def _gemini_maker_fee(price: D) -> D:
    if not ZERO <= price <= ONE:
        raise ValueError("price outside [0,1]")
    return _ceil_cent(MAKER_RATE * price * (ONE - price))


def _kalshi_maker_fee(price: D, multiplier: D, fee_type: str) -> D:
    if not ZERO <= price <= ONE:
        raise ValueError("price outside [0,1]")
    # Kalshi exposes whether the series is one of the maker-fee series.  Standard
    # quadratic series do not charge a resting-order fee; the explicit
    # quadratic_with_maker_fees shape uses the 0.0175 maker coefficient.
    if fee_type != "quadratic_with_maker_fees":
        return ZERO
    return _ceil_cent(MAKER_RATE * multiplier * price * (ONE - price))


def _tick_from_kalshi_market(market: Mapping[str, Any]) -> D | None:
    ranges = market.get("price_ranges")
    if isinstance(ranges, list):
        for row in ranges:
            if not isinstance(row, Mapping):
                continue
            step = core._d(row.get("step"))
            if step is not None and ZERO < step <= D("0.10"):
                return step
    return CENT


def _gemini_tick(contract: Mapping[str, Any]) -> tuple[D, str]:
    tick = core._d(contract.get("priceIncrement"))
    if tick is not None and ZERO < tick <= D("0.10"):
        return tick, "contract.priceIncrement"
    return CENT, "conservative_default_0.01"


def _kalshi_nested_book(market: Mapping[str, Any]) -> dict[str, D] | None:
    yes_bid = core._d(market.get("yes_bid_dollars"))
    yes_ask = core._d(market.get("yes_ask_dollars"))
    no_bid = core._d(market.get("no_bid_dollars"))
    no_ask = core._d(market.get("no_ask_dollars"))
    yes_bid_size = core._d(market.get("yes_bid_size_fp"))
    yes_ask_size = core._d(market.get("yes_ask_size_fp"))
    if None in (yes_bid, yes_ask, no_bid, no_ask, yes_bid_size, yes_ask_size):
        return None
    assert yes_bid is not None and yes_ask is not None
    assert no_bid is not None and no_ask is not None
    assert yes_bid_size is not None and yes_ask_size is not None
    if not (ZERO <= yes_bid < yes_ask <= ONE and ZERO <= no_bid < no_ask <= ONE):
        return None
    if yes_bid_size <= ZERO or yes_ask_size <= ZERO:
        return None
    # Fail closed if the direct YES/NO fields do not describe the same binary book.
    if abs(no_ask - (ONE - yes_bid)) > D("0.0001"):
        return None
    if abs(no_bid - (ONE - yes_ask)) > D("0.0001"):
        return None
    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "yes_bid_size": yes_bid_size,
        "yes_ask_size": yes_ask_size,
        "no_bid": no_bid,
        "no_ask": no_ask,
        # In a binary contract, the YES bid queue is the NO ask liquidity and the
        # YES ask queue is the NO bid liquidity.
        "no_bid_size": yes_ask_size,
        "no_ask_size": yes_bid_size,
    }


def _gemini_embedded_book(contract: Mapping[str, Any]) -> dict[str, D] | None:
    prices = contract.get("prices") if isinstance(contract.get("prices"), Mapping) else {}
    yes_bid = core._d(prices.get("bestBid"))
    yes_ask = core._d(prices.get("bestAsk"))
    if yes_bid is None or yes_ask is None or not (ZERO <= yes_bid < yes_ask <= ONE):
        return None
    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": ONE - yes_ask,
        "no_ask": ONE - yes_bid,
    }


def _taker_fee(venue: str, price: D, *, kalshi_multiplier: D | None = None) -> D | None:
    if venue == "gemini":
        return core.gemini_one_contract_fee(price)
    if venue == "kalshi" and kalshi_multiplier is not None:
        return core.conservative_kalshi_fee(1, price, kalshi_multiplier)
    return None


def _maker_fee(
    venue: str,
    price: D,
    *,
    kalshi_multiplier: D | None = None,
    kalshi_fee_type: str = "",
) -> D | None:
    if venue == "gemini":
        return _gemini_maker_fee(price)
    if venue == "kalshi" and kalshi_multiplier is not None:
        return _kalshi_maker_fee(price, kalshi_multiplier, kalshi_fee_type)
    return None


def _all_in_taker(
    venue: str,
    price: D,
    *,
    kalshi_multiplier: D | None = None,
) -> D | None:
    fee = _taker_fee(venue, price, kalshi_multiplier=kalshi_multiplier)
    return None if fee is None else price + fee


def _all_in_maker(
    venue: str,
    price: D,
    *,
    kalshi_multiplier: D | None = None,
    kalshi_fee_type: str = "",
) -> D | None:
    fee = _maker_fee(
        venue,
        price,
        kalshi_multiplier=kalshi_multiplier,
        kalshi_fee_type=kalshi_fee_type,
    )
    return None if fee is None else price + fee


def _floor_profit(first_cost: D, second_cost: D) -> D:
    return ONE - first_cost - second_cost


def _aggressive_safe_maker_quote(
    *,
    maker_venue: str,
    maker_bid: D,
    maker_ask: D,
    maker_tick: D,
    hedge_venue: str,
    hedge_ask: D,
    kalshi_multiplier: D | None,
    kalshi_fee_type: str,
    min_profit: D = MIN_FLOOR,
) -> dict[str, Any] | None:
    if maker_tick <= ZERO or maker_bid < ZERO or maker_ask > ONE or maker_bid >= maker_ask:
        return None
    hedge_cost = _all_in_taker(
        hedge_venue,
        hedge_ask,
        kalshi_multiplier=kalshi_multiplier if hedge_venue == "kalshi" else None,
    )
    if hedge_cost is None:
        return None

    # Search the actual price grid from the current bid upward.  The highest safe
    # post-only price is the most aggressive quote that preserves the fallback
    # floor if this maker order fills first and the opposite leg must cross now.
    upper = maker_ask - maker_tick
    if upper < maker_bid:
        return None
    price = maker_bid
    best: dict[str, Any] | None = None
    guard = 0
    while price <= upper + D("0.0000001") and guard < 200:
        maker_cost = _all_in_maker(
            maker_venue,
            price,
            kalshi_multiplier=kalshi_multiplier if maker_venue == "kalshi" else None,
            kalshi_fee_type=kalshi_fee_type if maker_venue == "kalshi" else "",
        )
        if maker_cost is None:
            return None
        profit = _floor_profit(maker_cost, hedge_cost)
        if profit >= min_profit:
            best = {
                "maker_price": price,
                "maker_fee": maker_cost - price,
                "maker_all_in": maker_cost,
                "hedge_ask": hedge_ask,
                "hedge_all_in": hedge_cost,
                "fallback_profit": profit,
                "join_bid_profit": None,
            }
        price += maker_tick
        guard += 1

    if best is None:
        return None
    join_cost = _all_in_maker(
        maker_venue,
        maker_bid,
        kalshi_multiplier=kalshi_multiplier if maker_venue == "kalshi" else None,
        kalshi_fee_type=kalshi_fee_type if maker_venue == "kalshi" else "",
    )
    if join_cost is not None:
        best["join_bid_profit"] = _floor_profit(join_cost, hedge_cost)
    best["maker_bid"] = maker_bid
    best["maker_ask"] = maker_ask
    best["maker_tick"] = maker_tick
    best["ticks_improved"] = int(((best["maker_price"] - maker_bid) / maker_tick).to_integral_value())
    best["near_touch"] = bool(best["maker_price"] >= upper)
    return best


def _stringify(value: Any) -> Any:
    if isinstance(value, D):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify(v) for v in value]
    return value


def _side_plan(
    *,
    key: str,
    identity: Any,
    maker_venue: str,
    maker_side: str,
    maker_book: Mapping[str, D],
    maker_tick: D,
    hedge_venue: str,
    hedge_side: str,
    hedge_book: Mapping[str, D],
    kalshi_multiplier: D,
    kalshi_fee_type: str,
    gemini_symbol: str,
    kalshi_ticker: str,
) -> dict[str, Any] | None:
    plan = _aggressive_safe_maker_quote(
        maker_venue=maker_venue,
        maker_bid=maker_book[f"{maker_side}_bid"],
        maker_ask=maker_book[f"{maker_side}_ask"],
        maker_tick=maker_tick,
        hedge_venue=hedge_venue,
        hedge_ask=hedge_book[f"{hedge_side}_ask"],
        kalshi_multiplier=kalshi_multiplier,
        kalshi_fee_type=kalshi_fee_type,
    )
    if plan is None:
        return None
    return {
        "contract_key": key,
        "family": identity.market_family,
        "participants": list(identity.event.participants),
        "subject": identity.subject,
        "line": str(identity.line) if identity.line is not None else None,
        "gemini_symbol": gemini_symbol,
        "kalshi_ticker": kalshi_ticker,
        "maker_venue": maker_venue,
        "maker_side": maker_side.upper(),
        "hedge_venue": hedge_venue,
        "hedge_side": hedge_side.upper(),
        **plan,
    }


async def run_once() -> dict[str, Any]:
    started = time.monotonic()
    fetch_health: Counter[str] = Counter()
    async with httpx.AsyncClient(timeout=12.0) as http:
        gbody, gemini_received_ms = await core._get(
            http,
            f"{core.GEMINI}/v1/prediction-markets/events",
            fetch_health,
            params={"status": "active", "sport": "american_football", "limit": 500, "offset": 0},
        )
        gemini_events = [
            dict(x)
            for x in ((gbody or {}).get("data") or (gbody or {}).get("events") or [])
            if isinstance(x, Mapping)
        ]
        gemini_rows, gcontracts = core._gemini_rows(gemini_events)
        gindex = core._gemini_match_index(gemini_rows)

        events_by_series: dict[str, list[dict[str, Any]]] = {}
        kalshi_received_ms: dict[str, int] = {}
        for series in core.SERIES_FAMILY:
            body, received_ms = await core._get(
                http,
                f"{core.KALSHI}/events",
                fetch_health,
                params={
                    "limit": 200,
                    "status": "open",
                    "series_ticker": series,
                    "with_nested_markets": "true",
                },
            )
            events_by_series[series] = [
                dict(x) for x in ((body or {}).get("events") or []) if isinstance(x, Mapping)
            ]
            kalshi_received_ms[series] = received_ms
            await asyncio.sleep(0.05)

        kalshi_rows, match_stats = core._kalshi_rows(events_by_series, gindex)

        fee_meta: dict[str, tuple[D, str]] = {}
        for series in core.SERIES_FAMILY:
            body, _ = await core._get(http, f"{core.KALSHI}/series/{series}", fetch_health)
            meta = (body or {}).get("series") if isinstance((body or {}).get("series"), Mapping) else {}
            multiplier = core._d(meta.get("fee_multiplier"))
            fee_type = str(meta.get("fee_type") or "")
            if multiplier is not None and ZERO <= multiplier <= D("100") and fee_type in {
                "quadratic", "quadratic_with_maker_fees"
            }:
                fee_meta[series] = (multiplier, fee_type)

    raw_kalshi: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for series, events in events_by_series.items():
        for event in events:
            for market in event.get("markets") or []:
                if not isinstance(market, Mapping):
                    continue
                ticker = str(market.get("ticker") or "")
                if ticker:
                    raw_kalshi[ticker] = (series, market)

    gemini_by_key: dict[str, list[Any]] = defaultdict(list)
    kalshi_by_key: dict[str, list[Any]] = defaultdict(list)
    for row in gemini_rows:
        gemini_by_key[row.contract_key].append(row)
    for row in kalshi_rows:
        kalshi_by_key[row.side.contract_key].append(row)
    keys = sorted(set(gemini_by_key) & set(kalshi_by_key))

    taker_rows: list[dict[str, Any]] = []
    maker_rows: list[dict[str, Any]] = []
    dual_rows: list[dict[str, Any]] = []
    rejects: Counter[str] = Counter()
    family_keys: Counter[str] = Counter()

    for key in keys:
        for grow in gemini_by_key[key]:
            gcontract = gcontracts.get(grow.venue_market_id)
            if not isinstance(gcontract, Mapping):
                rejects["missing_gemini_contract"] += 1
                continue
            gbook = _gemini_embedded_book(gcontract)
            if gbook is None:
                rejects["missing_gemini_two_sided_book"] += 1
                continue
            gtick, gtick_source = _gemini_tick(gcontract)

            for kcandidate in kalshi_by_key[key]:
                raw = raw_kalshi.get(kcandidate.side.venue_market_id)
                if raw is None:
                    rejects["missing_kalshi_nested_market"] += 1
                    continue
                series, kmarket = raw
                kbook = _kalshi_nested_book(kmarket)
                if kbook is None:
                    rejects["invalid_kalshi_nested_book"] += 1
                    continue
                ktick = _tick_from_kalshi_market(kmarket)
                meta = fee_meta.get(series)
                if ktick is None or meta is None:
                    rejects["missing_tick_or_fee_meta"] += 1
                    continue
                multiplier, fee_type = meta
                identity = grow.identity
                family_keys[identity.market_family] += 1

                # Taker/taker baseline in both complementary directions.
                gy = _all_in_taker("gemini", gbook["yes_ask"])
                gn = _all_in_taker("gemini", gbook["no_ask"])
                ky = _all_in_taker("kalshi", kbook["yes_ask"], kalshi_multiplier=multiplier)
                kn = _all_in_taker("kalshi", kbook["no_ask"], kalshi_multiplier=multiplier)
                if None not in (gy, gn, ky, kn):
                    assert gy is not None and gn is not None and ky is not None and kn is not None
                    for direction, profit in (
                        ("GEMINI_YES_KALSHI_NO", _floor_profit(gy, kn)),
                        ("KALSHI_YES_GEMINI_NO", _floor_profit(ky, gn)),
                    ):
                        if profit > ZERO:
                            taker_rows.append({
                                "contract_key": key,
                                "family": identity.market_family,
                                "participants": list(identity.event.participants),
                                "subject": identity.subject,
                                "line": str(identity.line) if identity.line is not None else None,
                                "gemini_symbol": grow.venue_market_id,
                                "kalshi_ticker": kcandidate.side.venue_market_id,
                                "direction": direction,
                                "profit": profit,
                            })

                plans: dict[str, dict[str, Any]] = {}
                plan_specs = (
                    ("G_Y_K_N", "gemini", "yes", gbook, gtick, "kalshi", "no", kbook),
                    ("G_N_K_Y", "gemini", "no", gbook, gtick, "kalshi", "yes", kbook),
                    ("K_Y_G_N", "kalshi", "yes", kbook, ktick, "gemini", "no", gbook),
                    ("K_N_G_Y", "kalshi", "no", kbook, ktick, "gemini", "yes", gbook),
                )
                for name, maker_v, maker_s, maker_b, maker_tick, hedge_v, hedge_s, hedge_b in plan_specs:
                    row = _side_plan(
                        key=key,
                        identity=identity,
                        maker_venue=maker_v,
                        maker_side=maker_s,
                        maker_book=maker_b,
                        maker_tick=maker_tick,
                        hedge_venue=hedge_v,
                        hedge_side=hedge_s,
                        hedge_book=hedge_b,
                        kalshi_multiplier=multiplier,
                        kalshi_fee_type=fee_type,
                        gemini_symbol=grow.venue_market_id,
                        kalshi_ticker=kcandidate.side.venue_market_id,
                    )
                    if row is not None:
                        row["gemini_tick_source"] = gtick_source
                        row["kalshi_series"] = series
                        row["kalshi_fee_type"] = fee_type
                        row["kalshi_fee_multiplier"] = multiplier
                        plans[name] = row
                        maker_rows.append(row)

                # Two safe dual-maker orientations.  Safety is the minimum profit
                # after either possible first fill followed by an immediate taker
                # hedge.  Both-fill economics are shown separately and are never
                # substituted for the fallback floor.
                for orientation, left_name, right_name in (
                    ("GEMINI_YES__KALSHI_NO", "G_Y_K_N", "K_N_G_Y"),
                    ("KALSHI_YES__GEMINI_NO", "K_Y_G_N", "G_N_K_Y"),
                ):
                    left, right = plans.get(left_name), plans.get(right_name)
                    if left is None or right is None:
                        continue
                    left_cost = D(str(left["maker_all_in"]))
                    right_cost = D(str(right["maker_all_in"]))
                    both_fill_profit = _floor_profit(left_cost, right_cost)
                    fallback_floor = min(D(str(left["fallback_profit"])), D(str(right["fallback_profit"])))
                    dual_rows.append({
                        "contract_key": key,
                        "family": identity.market_family,
                        "participants": list(identity.event.participants),
                        "subject": identity.subject,
                        "line": str(identity.line) if identity.line is not None else None,
                        "gemini_symbol": grow.venue_market_id,
                        "kalshi_ticker": kcandidate.side.venue_market_id,
                        "orientation": orientation,
                        "fallback_floor_profit": fallback_floor,
                        "both_fill_profit": both_fill_profit,
                        "both_near_touch": bool(left["near_touch"] and right["near_touch"]),
                        "first_maker": left,
                        "second_maker": right,
                    })

    taker_floor = [r for r in taker_rows if D(str(r["profit"])) >= MIN_FLOOR]
    maker_floor = [r for r in maker_rows if D(str(r["fallback_profit"])) >= MIN_FLOOR]
    maker_near = [r for r in maker_floor if bool(r["near_touch"])]
    dual_floor = [r for r in dual_rows if D(str(r["fallback_floor_profit"])) >= MIN_FLOOR]
    dual_near = [r for r in dual_floor if bool(r["both_near_touch"])]

    maker_floor.sort(
        key=lambda r: (bool(r["near_touch"]), int(r["ticks_improved"]), D(str(r["join_bid_profit"] or ZERO))),
        reverse=True,
    )
    dual_floor.sort(
        key=lambda r: (bool(r["both_near_touch"]), D(str(r["both_fill_profit"]))),
        reverse=True,
    )
    taker_rows.sort(key=lambda r: D(str(r["profit"])), reverse=True)

    result = {
        "status": "SPORTS_NFL_GK_MAKER_ARCH_SHADOW",
        "gemini_football_events": len(gemini_events),
        "gemini_nfl_normalized_contracts": len(gemini_rows),
        "kalshi_events": {k: len(v) for k, v in events_by_series.items()},
        "kalshi_normalized_candidates": len(kalshi_rows),
        "match_stats": dict(match_stats),
        "exact_counterpart_keys": len(keys),
        "priced_pair_instances": sum(family_keys.values()),
        "priced_pair_instances_by_family": dict(family_keys),
        "rejects": dict(rejects),
        "taker_taker_positive": len(taker_rows),
        "taker_taker_floor_3c": len(taker_floor),
        "maker_hedge_floor_3c": len(maker_floor),
        "maker_hedge_near_touch_floor_3c": len(maker_near),
        "dual_maker_fallback_floor_3c": len(dual_floor),
        "dual_maker_both_near_touch_floor_3c": len(dual_near),
        "top_taker_taker": taker_rows[:20],
        "top_maker_hedge": maker_floor[:30],
        "top_dual_maker": dual_floor[:20],
        "min_after_fee_floor": str(MIN_FLOOR),
        "kalshi_quote_source": "nested_event_yes_bid_ask_and_sizes",
        "gemini_quote_source": "active_event_bestBid_bestAsk",
        "gemini_received_ms": gemini_received_ms,
        "kalshi_received_ms_by_series": kalshi_received_ms,
        "rule_gate": "NFL_GEMINI_KALSHI_FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
        "cycle_ms": round((time.monotonic() - started) * 1000, 3),
    }
    logger.warning("SPORTS_NFL_GK_MAKER_ARCH_SHADOW %s", json.dumps(_stringify(result), separators=(",", ":"), default=str))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
