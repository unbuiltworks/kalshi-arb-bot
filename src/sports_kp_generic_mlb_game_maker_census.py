"""Read-only K<->P MLB maker->taker economics census.

This module reuses the clean full-game K/P breadth matcher and current top books,
but measures whether resting one leg inside/joining the spread would create an
after-fee lock against the opposite venue's currently executable hedge.

Important accounting:
* a maker quote is NOT a fill;
* quoted maker edge is NOT executable arbitrage;
* no fill-rate or $/day capacity is credited here;
* settlement rules remain incomplete for these normal-event rows;
* no order-entry code is imported.

The purpose is to answer a narrower question before any maker engine work:
"Are the missing K/P economics sitting inside the spread?"
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import os
import statistics
from typing import Any, Mapping

import httpx

from src import sports_kp_generic_mlb_game_breadth as breadth
from src.sports_kp_generic_mlb_game_breadth_fullgame import is_clearly_partial_game_label
from src.polymarket_kalshi_shadow import kalshi_taker_fee, polymarket_taker_fee

logger = logging.getLogger("kalshi_arb.sports_kp_generic_mlb_game_maker_census")
D = Decimal
TICK = D("0.01")
TARGETS = (D("0.01"), D("0.02"), D("0.03"))


def _stats(values: list[Decimal]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "p50": None, "max": None}
    nums = [float(v) for v in values]
    return {
        "avg": round(statistics.fmean(nums), 6),
        "p50": round(statistics.median(nums), 6),
        "max": round(max(nums), 6),
    }


def _levels(quote: Any, side: str) -> dict[str, Decimal] | None:
    """Return oriented bid/ask levels reconstructed from complementary asks."""
    side = str(side or "").upper()
    if side == "YES":
        prop_ask = D(quote.yes_ask)
        prop_ask_depth = D(quote.yes_depth)
        prop_bid = D("1") - D(quote.no_ask)
        prop_bid_depth = D(quote.no_depth)
        opp_ask = D(quote.no_ask)
        opp_ask_depth = D(quote.no_depth)
        opp_bid = D("1") - D(quote.yes_ask)
        opp_bid_depth = D(quote.yes_depth)
    elif side == "NO":
        prop_ask = D(quote.no_ask)
        prop_ask_depth = D(quote.no_depth)
        prop_bid = D("1") - D(quote.yes_ask)
        prop_bid_depth = D(quote.yes_depth)
        opp_ask = D(quote.yes_ask)
        opp_ask_depth = D(quote.yes_depth)
        opp_bid = D("1") - D(quote.no_ask)
        opp_bid_depth = D(quote.no_depth)
    else:
        return None
    if not (D("0") <= prop_bid < prop_ask <= D("1")):
        return None
    if not (D("0") <= opp_bid < opp_ask <= D("1")):
        return None
    return {
        "prop_ask": prop_ask,
        "prop_ask_depth": prop_ask_depth,
        "prop_bid": prop_bid,
        "prop_bid_depth": prop_bid_depth,
        "opp_ask": opp_ask,
        "opp_ask_depth": opp_ask_depth,
        "opp_bid": opp_bid,
        "opp_bid_depth": opp_bid_depth,
    }


def _maker_price(bid: Decimal, ask: Decimal, bid_depth: Decimal) -> tuple[Decimal, str, Decimal] | None:
    """Quote one tick better than bid when possible; otherwise join best bid."""
    spread = ask - bid
    if spread <= 0:
        return None
    if spread >= D("0.02"):
        price = bid + TICK
        mode = "IMPROVE_BID_ONE_TICK"
        queue_ahead = D("0")
    else:
        price = bid
        mode = "JOIN_BEST_BID"
        queue_ahead = max(D("0"), bid_depth)
    if not (D("0") < price < ask <= D("1")):
        return None
    return price, mode, queue_ahead


def _route(
    *,
    maker_venue: str,
    maker_leg: str,
    maker_bid: Decimal,
    maker_ask: Decimal,
    maker_bid_depth: Decimal,
    hedge_venue: str,
    hedge_price: Decimal,
    hedge_depth: Decimal,
    poly_rate: Decimal,
    kalshi_rate: Decimal,
) -> dict[str, Any] | None:
    mq = _maker_price(maker_bid, maker_ask, maker_bid_depth)
    if mq is None or hedge_depth < 1:
        return None
    maker_price, quote_mode, queue_ahead = mq

    if hedge_venue == "POLYMARKET":
        hedge_fee = polymarket_taker_fee(1, hedge_price, poly_rate)
    else:
        hedge_fee = kalshi_taker_fee(1, hedge_price, kalshi_rate)

    # Polymarket US sports makers are modeled at zero fee and maker rebates are
    # deliberately ignored. Kalshi maker fee applicability is series-specific;
    # until certified per market, count the taker-fee formula as a deliberately
    # conservative upper fee bound and also report the zero-maker-fee upper edge.
    if maker_venue == "POLYMARKET":
        maker_fee_conservative = D("0")
    else:
        maker_fee_conservative = kalshi_taker_fee(1, maker_price, kalshi_rate)

    profit_conservative = D("1") - maker_price - hedge_price - maker_fee_conservative - hedge_fee
    profit_zero_maker_fee = D("1") - maker_price - hedge_price - hedge_fee
    return {
        "maker_venue": maker_venue,
        "maker_leg": maker_leg,
        "maker_price": maker_price,
        "maker_quote_mode": quote_mode,
        "maker_queue_ahead_displayed": queue_ahead,
        "maker_spread": maker_ask - maker_bid,
        "maker_improvement_vs_taker": maker_ask - maker_price,
        "hedge_venue": hedge_venue,
        "hedge_price": hedge_price,
        "hedge_q1_depth": hedge_depth,
        "hedge_fee": hedge_fee,
        "maker_fee_conservative": maker_fee_conservative,
        "profit_conservative": profit_conservative,
        "profit_zero_maker_fee": profit_zero_maker_fee,
        "quoted_only_not_fill": True,
    }


def _maker_candidates(pair: Any, poly_quote: Any, kalshi_quote: Any, *, poly_rate: Decimal, kalshi_rate: Decimal) -> list[dict[str, Any]]:
    p = _levels(poly_quote, pair.poly.side)
    k = _levels(kalshi_quote, pair.kalshi.side)
    if p is None or k is None:
        return []
    out: list[dict[str, Any]] = []

    specs = (
        # Make the semantic proposition on P, hedge complement on K.
        dict(maker_venue="POLYMARKET", maker_leg="PROP", maker_bid=p["prop_bid"], maker_ask=p["prop_ask"], maker_bid_depth=p["prop_bid_depth"], hedge_venue="KALSHI", hedge_price=k["opp_ask"], hedge_depth=k["opp_ask_depth"]),
        # Make the complement on P, hedge semantic proposition on K.
        dict(maker_venue="POLYMARKET", maker_leg="OPP", maker_bid=p["opp_bid"], maker_ask=p["opp_ask"], maker_bid_depth=p["opp_bid_depth"], hedge_venue="KALSHI", hedge_price=k["prop_ask"], hedge_depth=k["prop_ask_depth"]),
        # Make the semantic proposition on K, hedge complement on P.
        dict(maker_venue="KALSHI", maker_leg="PROP", maker_bid=k["prop_bid"], maker_ask=k["prop_ask"], maker_bid_depth=k["prop_bid_depth"], hedge_venue="POLYMARKET", hedge_price=p["opp_ask"], hedge_depth=p["opp_ask_depth"]),
        # Make the complement on K, hedge semantic proposition on P.
        dict(maker_venue="KALSHI", maker_leg="OPP", maker_bid=k["opp_bid"], maker_ask=k["opp_ask"], maker_bid_depth=k["opp_bid_depth"], hedge_venue="POLYMARKET", hedge_price=p["prop_ask"], hedge_depth=p["prop_ask_depth"]),
    )
    for spec in specs:
        row = _route(poly_rate=poly_rate, kalshi_rate=kalshi_rate, **spec)
        if row is not None:
            out.append(row)
    return out


def _summary(rows: list[dict[str, Any]], *, field: str) -> dict[str, Any]:
    vals = [D(r[field]) for r in rows]
    return {
        "rows": len(rows),
        "positive": sum(1 for v in vals if v > 0),
        "gte_1c": sum(1 for v in vals if v >= D("0.01")),
        "gte_2c": sum(1 for v in vals if v >= D("0.02")),
        "gte_3c": sum(1 for v in vals if v >= D("0.03")),
        "profit": _stats(vals),
        "hedge_q1_depth": _stats([D(r["hedge_q1_depth"]) for r in rows if D(r["hedge_q1_depth"]) > 0]),
        "maker_improvement_vs_taker": _stats([D(r["maker_improvement_vs_taker"]) for r in rows]),
        "join_best_bid": sum(1 for r in rows if r["maker_quote_mode"] == "JOIN_BEST_BID"),
        "improve_bid_one_tick": sum(1 for r in rows if r["maker_quote_mode"] == "IMPROVE_BID_ONE_TICK"),
    }


def _jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in row.items()}


async def run_once() -> dict[str, Any]:
    poly_rate = D(os.getenv("POLYMARKET_TAKER_FEE_RATE", "0.05"))
    kalshi_rate = D(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07"))
    async with httpx.AsyncClient(timeout=10.0) as http:
        poly_events, kalshi_events, kal_meta = await breadth._fetch_inputs(http)
        poly_sides = [
            s for s in breadth.normalize_polymarket_mlb(poly_events)
            if not is_clearly_partial_game_label(s.source_label)
            and not is_clearly_partial_game_label(s.venue_market_id)
        ]
        kalshi_sides = breadth.normalize_kalshi_mlb(kalshi_events)
        pairs, ambiguous, expanded = breadth._pair_normal_event_sides(poly_sides, kalshi_sides)
        poly_store = await breadth._seed_poly_books(http, (pair.poly.venue_market_id for pair in pairs))

    taker_rows: list[dict[str, Any]] = []
    maker_rows: list[dict[str, Any]] = []
    nested_quotes = kal_meta["quotes"]
    for pair in pairs:
        pq = poly_store.quote(pair.poly.venue_market_id, max_age_ms=120000)
        kq = nested_quotes.get(pair.kalshi.venue_market_id)
        if pq is None or kq is None:
            continue
        taker = breadth._q1_economics(pair, pq, kq, poly_rate=poly_rate, kalshi_rate=kalshi_rate)
        if taker is not None:
            taker_rows.append({"family": pair.family, "group_key": pair.group_key, **taker})
        for route in _maker_candidates(pair, pq, kq, poly_rate=poly_rate, kalshi_rate=kalshi_rate):
            maker_rows.append({
                "family": pair.family,
                "group_key": pair.group_key,
                "contract_key": pair.contract_key,
                "poly_market": pair.poly.venue_market_id,
                "poly_side": pair.poly.side,
                "kalshi_market": pair.kalshi.venue_market_id,
                "kalshi_side": pair.kalshi.side,
                "start_skew_ms": pair.start_skew_ms,
                **route,
            })

    # One best taker row per semantic group, and one best maker route per
    # semantic group x maker venue. This prevents physical representation mirrors
    # from inflating opportunity counts.
    best_taker: dict[str, dict[str, Any]] = {}
    for r in taker_rows:
        if r["group_key"] not in best_taker or D(r["profit"]) > D(best_taker[r["group_key"]]["profit"]):
            best_taker[r["group_key"]] = r

    best_maker: dict[tuple[str, str], dict[str, Any]] = {}
    for r in maker_rows:
        key = (r["group_key"], r["maker_venue"])
        if key not in best_maker or D(r["profit_conservative"]) > D(best_maker[key]["profit_conservative"]):
            best_maker[key] = r
    unique_maker = list(best_maker.values())

    families: dict[str, Any] = {}
    for family in ("moneyline", "spread", "game_total"):
        fam = [r for r in unique_maker if r["family"] == family]
        fam_poly = [r for r in fam if r["maker_venue"] == "POLYMARKET"]
        fam_kalshi = [r for r in fam if r["maker_venue"] == "KALSHI"]
        families[family] = {
            "polymarket_maker_conservative": _summary(fam_poly, field="profit_conservative"),
            "kalshi_maker_conservative": _summary(fam_kalshi, field="profit_conservative"),
            "kalshi_maker_zero_fee_upper_bound": _summary(fam_kalshi, field="profit_zero_maker_fee"),
        }

    taker_unique = list(best_taker.values())
    top = sorted(unique_maker, key=lambda r: (D(r["profit_conservative"]), D(r["hedge_q1_depth"])), reverse=True)[:25]
    payload = {
        "classification": "MAKER_QUOTE_FEASIBILITY_SHADOW_RULE_INCOMPLETE_NOT_EXECUTION_ELIGIBLE",
        "source_quality": "REST_SNAPSHOT_QUOTED_MAKER_EDGE_NOT_FILL_EVIDENCE",
        "maker_quote_policy": "ONE_TICK_BETTER_THAN_BID_ELSE_JOIN_BEST_BID_NEVER_CROSS",
        "maker_fill_model": "NONE_YET_QUOTED_EDGE_ONLY_ZERO_CAPACITY_CREDIT",
        "polymarket_maker_fee_model": "ZERO_FEE_REBATE_IGNORED",
        "kalshi_maker_fee_model": "CONSERVATIVE_TAKER_FEE_UPPER_BOUND_PLUS_ZERO_FEE_UPPER_EDGE",
        "poly_events": len(poly_events),
        "kalshi_events": len(kalshi_events),
        "normal_event_pairs": len(pairs),
        "ambiguous_contract_keys_rejected": ambiguous,
        "expanded_shared_contract_keys": expanded,
        "taker_semantic_groups": len(taker_unique),
        "taker_reference": {
            "positive": sum(1 for r in taker_unique if D(r["profit"]) > 0),
            "gte_1c": sum(1 for r in taker_unique if D(r["profit"]) >= D("0.01")),
            "gte_2c": sum(1 for r in taker_unique if D(r["profit"]) >= D("0.02")),
            "gte_3c": sum(1 for r in taker_unique if D(r["profit"]) >= D("0.03")),
        },
        "maker_semantic_group_venue_rows": len(unique_maker),
        "families": families,
        "top_maker_quote_rows": [_jsonable(r) for r in top],
        "rules_complete": False,
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
        "capacity_credit_usd_per_day": 0,
    }
    encoded = json.dumps(payload, separators=(",", ":"), default=str)
    logger.warning("SPORTS_KP_MLB_MAKER_CENSUS %s", encoded)
    print("SPORTS_KP_MLB_MAKER_CENSUS " + encoded, flush=True)
    return payload


async def main_async(interval_seconds: float) -> None:
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("SPORTS_KP_MLB_MAKER_CENSUS_ERROR")
        if interval_seconds <= 0:
            return
        await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only K/P MLB maker->taker quote-feasibility census")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async(args.interval_seconds))


if __name__ == "__main__":
    main()
