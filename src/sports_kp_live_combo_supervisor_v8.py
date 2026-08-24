"""Production combo supervisor v8: search both scalar-safe MVE coverage directions.

For selected settlement values x_i in [0,1], both baskets have a $1 floor:

  A) product(x_i) + sum(1 - x_i) >= 1
     -> buy combo YES + buy complement of every selected component.

  B) 1 - product(x_i) + sum(x_i) >= 1
     -> buy combo NO + buy every selected component.

The prior generator only manufactured candidates optimized for A.  This layer
interleaves A candidates with candidates optimized for B, evaluates both sides
of each live RFQ, and logs B as shadow-only until its live executor is separately
validated.  Existing A q=1 execution remains armed and fail-closed.
"""
from __future__ import annotations

from decimal import Decimal
import itertools
import json
import os
from typing import Any

from src import sports_kp_live_combo_supervisor_v7 as v7

base = v7.base
generator = v7.generator
logger = v7.logger
D = Decimal

# Twelve creations every ~15 minutes still remains bounded by the existing
# 600/day hard cap (< 5000/week if the cap is respected every day).
os.environ["MVE_GENERATOR_MAX_CREATIONS_PER_RUN"] = os.getenv("MVE_GENERATOR_MAX_CREATIONS_PER_RUN", "12")

_ORIGINAL_EVENT_MARKETS = generator._event_markets
_ORIGINAL_CANDIDATES = generator._candidate_combos
_ORIGINAL_RFQ_QUOTES = generator._rfq_quotes
_ORIGINAL_GENERATE = base.generate_mves

_MARKETS: dict[str, dict[str, Any]] = {}
_QUOTES: dict[str, list[dict[str, Any]]] = {}
_INVERSE_KEYS: set[str] = set()


def _row_key(collection: str, legs: list[dict[str, Any]] | tuple[Any, ...]) -> str:
    pieces: list[str] = []
    for leg in legs:
        if isinstance(leg, dict):
            pieces.append(f"{leg.get('event_ticker')}:{leg.get('market_ticker')}:{str(leg.get('side') or '').lower()}")
        else:
            pieces.append(f"{leg.event_ticker}:{leg.market_ticker}:{leg.side}")
    return collection + "|" + "|".join(sorted(pieces))


def _selected_quote(leg: Any) -> tuple[Decimal, Decimal, Decimal] | None:
    market = _MARKETS.get(leg.market_ticker) or {}
    if leg.side == "yes":
        price = generator._d(market.get("yes_ask_dollars"))
        depth = generator._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
    else:
        price = v7.v6._derived_no_ask(market)
        depth = generator._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
    if price is None or depth is None or depth < generator.ONE or not (generator.ZERO < price < generator.ONE):
        return None
    return price, generator._fee(price), depth


async def _capture_event_markets(client: Any, event_ticker: str) -> list[dict[str, Any]]:
    rows = await _ORIGINAL_EVENT_MARKETS(client, event_ticker)
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if ticker:
            _MARKETS[ticker] = dict(row)
    return rows


async def _capture_rfq_quotes(client: Any, ticker: str, wait_s: float):
    rid, quotes = await _ORIGINAL_RFQ_QUOTES(client, ticker, wait_s)
    _QUOTES[ticker] = [dict(q) for q in quotes]
    return rid, quotes


def _dual_candidates(
    collection: dict[str, Any],
    event_legs: dict[str, list[Any]],
    *,
    max_candidates: int,
    min_locked: Decimal,
):
    global _INVERSE_KEYS
    normal = _ORIGINAL_CANDIDATES(
        collection, event_legs, max_candidates=max_candidates, min_locked=min_locked
    )
    collection_ticker = str(collection.get("collection_ticker") or "")
    lo, hi = generator._collection_leg_bounds(collection)
    single_per_event = bool(collection.get("is_single_market_per_event", True))
    all_yes = bool(collection.get("is_all_yes", False))

    pools: list[Any] = []
    for rows in event_legs.values():
        eligible = [leg for leg in rows if not all_yes or leg.side == "yes"]
        eligible = [leg for leg in eligible if _selected_quote(leg) is not None]
        eligible.sort(key=lambda leg: sum(_selected_quote(leg)[:2], generator.ZERO))
        pools.extend(eligible[:4])
    pools.sort(key=lambda leg: sum(_selected_quote(leg)[:2], generator.ZERO))
    pools = pools[:40]

    threshold = generator.ONE - min_locked
    inverse_ranked: list[tuple[Decimal, tuple[Any, ...]]] = []
    for n in generator.SUPPORTED_LEGS:
        if n < lo or n > hi or len(pools) < n:
            continue
        for legs in itertools.combinations(pools, n):
            if single_per_event and len({leg.event_ticker for leg in legs}) != n:
                continue
            selected_cost = generator.ZERO
            valid = True
            for leg in legs:
                quote = _selected_quote(leg)
                if quote is None:
                    valid = False
                    break
                selected_cost += quote[0] + quote[1]
            if not valid or selected_cost >= threshold:
                continue
            inverse_ranked.append((selected_cost, legs))
    inverse_ranked.sort(key=lambda item: (item[0], max(leg.close_time for leg in item[1]), -len(item[1])))
    inverse_ranked = inverse_ranked[:max_candidates]

    # Interleave the cheapest candidates from each coverage identity so the
    # bounded creation budget explores both tails instead of only favorites.
    out: list[tuple[Decimal, tuple[Any, ...]]] = []
    seen: set[str] = set()
    normal_i = inverse_i = 0
    while len(out) < max_candidates and (normal_i < len(normal) or inverse_i < len(inverse_ranked)):
        if normal_i < len(normal):
            hedge_all, legs = normal[normal_i]
            normal_i += 1
            key = _row_key(collection_ticker, legs)
            if key not in seen:
                seen.add(key)
                out.append((hedge_all, legs))
                if len(out) >= max_candidates:
                    break
        if inverse_i < len(inverse_ranked):
            _selected_cost, legs = inverse_ranked[inverse_i]
            inverse_i += 1
            key = _row_key(collection_ticker, legs)
            if key not in seen:
                seen.add(key)
                _INVERSE_KEYS.add(key)
                complement_all = sum((leg.hedge_all_in for leg in legs), generator.ZERO)
                out.append((complement_all, legs))
    return out


def _best_buy_no_quote(quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for quote in quotes:
        if str(quote.get("status") or "").lower() not in {"", "open"}:
            continue
        yes_bid = generator._d(quote.get("yes_bid_dollars"))
        quote_id = str(quote.get("id") or "")
        if yes_bid is None or not quote_id or not (generator.ZERO < yes_bid < generator.ONE):
            continue
        cost = generator.ONE - yes_bid
        row = {
            "quote_id": quote_id,
            "yes_bid": yes_bid,
            "combo_no_cost": cost,
            "combo_fee": generator._fee(cost),
            "updated_ts": quote.get("updated_ts"),
        }
        if best is None or cost < best["combo_no_cost"]:
            best = row
    return best


def _score_inverse(row: dict[str, Any]) -> dict[str, Any] | None:
    ticker = str(row.get("ticker") or "")
    quotes = _QUOTES.get(ticker) or []
    best = _best_buy_no_quote(quotes)
    if best is None:
        return None

    component_cost = generator.ZERO
    component_rows: list[dict[str, Any]] = []
    for leg_row in row.get("legs") or []:
        market_ticker = str(leg_row.get("market_ticker") or "")
        side = str(leg_row.get("side") or "").lower()
        market = _MARKETS.get(market_ticker) or {}
        if side == "yes":
            price = generator._d(market.get("yes_ask_dollars"))
            depth = generator._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
        elif side == "no":
            price = v7.v6._derived_no_ask(market)
            depth = generator._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
        else:
            return None
        if price is None or depth is None or depth < generator.ONE or not (generator.ZERO < price < generator.ONE):
            return None
        fee = generator._fee(price)
        component_cost += price + fee
        component_rows.append({
            "market_ticker": market_ticker,
            "selected_side": side,
            "selected_ask_dollars": str(price),
            "depth_fp": str(depth),
            "fee_conservative": str(fee),
        })

    all_in = component_cost + best["combo_no_cost"] + best["combo_fee"]
    locked = generator.ONE - all_in
    return {
        "direction": "COMBO_NO_PLUS_SELECTED",
        "best_quote": {
            "quote_id": best["quote_id"],
            "combo_no_equivalent": str(best["combo_no_cost"]),
            "combo_fee_conservative": str(best["combo_fee"]),
            "updated_ts": best["updated_ts"],
        },
        "components": component_rows,
        "all_in_cost_conservative": str(all_in),
        "locked_profit_conservative": str(locked),
        "classification": "EXECUTABLE_CANDIDATE" if locked >= generator.D(os.getenv("MVE_GENERATOR_MIN_LOCKED", "0.02")) else "NO_TRADE",
    }


async def _generate_both_directions() -> dict[str, Any]:
    _MARKETS.clear()
    _QUOTES.clear()
    _INVERSE_KEYS.clear()
    result = await _ORIGINAL_GENERATE()
    rows = [row for row in (result.get("ranked") or []) if isinstance(row, dict)] if isinstance(result, dict) else []
    inverse_exec: list[dict[str, Any]] = []
    best_inverse: dict[str, Any] | None = None
    for row in rows:
        inverse = _score_inverse(row)
        if inverse is None:
            continue
        row["inverse_coverage"] = inverse
        enriched = {
            "ticker": row.get("ticker"),
            "collection_ticker": row.get("collection_ticker"),
            "leg_count": row.get("leg_count"),
            "legs": row.get("legs"),
            **inverse,
        }
        if best_inverse is None or D(inverse["locked_profit_conservative"]) > D(best_inverse["locked_profit_conservative"]):
            best_inverse = enriched
        if inverse["classification"] == "EXECUTABLE_CANDIDATE":
            inverse_exec.append(enriched)

    logger.warning(
        "SPORTS_MVE_INVERSE_SHADOW %s",
        json.dumps(
            {
                "markets_scored": len(rows),
                "inverse_executable_candidates": len(inverse_exec),
                "best_inverse": best_inverse,
                "live_execution_enabled": False,
                "coverage_floor": "1.00",
            },
            separators=(",", ":"),
            default=str,
        ),
    )
    result["inverse_executable_candidates"] = len(inverse_exec)
    result["inverse_best"] = best_inverse
    return result


generator._event_markets = _capture_event_markets
generator._candidate_combos = _dual_candidates
generator._rfq_quotes = _capture_rfq_quotes
base.generate_mves = _generate_both_directions

run = base.run
main = base.main


if __name__ == "__main__":
    main()
