"""Read-only safety census for fractional-grid same-Gemini resting makers.

A q=1 resting maker may partially fill in contract quantity increments. Because
Gemini Predictions fees round each leg up to the next cent, a q=1 quote is live-safe
only if EVERY possible first cumulative fill quantity can be hedged exactly on the
sibling and still locks >=3c per filled contract after maker+taker fees and one
adverse hedge tick.

This module does not submit/cancel orders and does not change live admission.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_FLOOR
import json
import logging
from typing import Any, Mapping

import httpx

from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_maker_q1 as maker
from src.gemini_kalshi_execution_shadow import (
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto

D = Decimal
ONE = D("1")
TARGET = D("0.03")
logger = logging.getLogger("kalshi_arb.gemini_fractional_maker_census")


def _qty(contract: Mapping[str, Any], field: str, default: str) -> D:
    raw = contract.get(field)
    if raw is None:
        raw = contract.get("quantity_" + ("minimum" if field == "quantityMinimum" else "increment"))
    try:
        return D(str(raw if raw is not None else default))
    except Exception:
        return D(default)


def _multiple(value: D, increment: D) -> bool:
    if value <= 0 or increment <= 0:
        return False
    units = value / increment
    return units == units.to_integral_value()


def possible_fill_quantities(increment: D) -> list[D]:
    """Conservative first/cumulative partial fills for a q=1 resting order."""
    if increment <= 0 or increment > ONE or not _multiple(ONE, increment):
        return []
    n = int((ONE / increment).to_integral_value())
    return [increment * i for i in range(1, n + 1)]


def hedge_qty_compatible(quantity: D, hedge: Mapping[str, Any]) -> bool:
    hmin = _qty(hedge, "quantityMinimum", "1")
    hinc = _qty(hedge, "quantityIncrement", "1")
    return quantity >= hmin and _multiple(quantity, hinc)


def profit(*, maker_price: D, hedge_price: D, quantity: D) -> D:
    return (
        quantity
        - maker_price * quantity
        - hedge_price * quantity
        - gemini_predictions_maker_fee(maker_price, quantity)
        - gemini_predictions_taker_fee(hedge_price, quantity)
    )


def worst_fill_floor(*, maker_price: D, hedge_price: D, maker_contract: Mapping[str, Any],
                     hedge_contract: Mapping[str, Any], hedge_depth: D) -> tuple[bool, dict[str, Any]]:
    inc = _qty(maker_contract, "quantityIncrement", "1")
    fills = possible_fill_quantities(inc)
    if not fills:
        return False, {"reason": "INVALID_MAKER_INCREMENT", "maker_increment": str(inc)}
    worst_ppc: D | None = None
    worst_q: D | None = None
    first_failure: dict[str, Any] | None = None
    for q in fills:
        if q > hedge_depth:
            first_failure = {"reason": "HEDGE_DEPTH", "quantity": str(q)}
            break
        if not hedge_qty_compatible(q, hedge_contract):
            first_failure = {"reason": "HEDGE_GRID", "quantity": str(q)}
            break
        p = profit(maker_price=maker_price, hedge_price=hedge_price, quantity=q)
        ppc = p / q
        if worst_ppc is None or ppc < worst_ppc:
            worst_ppc, worst_q = ppc, q
        if ppc < TARGET:
            first_failure = {
                "reason": "FEE_ROUNDED_FLOOR",
                "quantity": str(q),
                "profit": str(p),
                "profit_per_contract": str(ppc),
                "maker_fee": str(gemini_predictions_maker_fee(maker_price, q)),
                "hedge_fee": str(gemini_predictions_taker_fee(hedge_price, q)),
            }
            break
    return first_failure is None, {
        "maker_increment": str(inc),
        "maker_minimum": str(_qty(maker_contract, "quantityMinimum", "1")),
        "hedge_increment": str(_qty(hedge_contract, "quantityIncrement", "1")),
        "hedge_minimum": str(_qty(hedge_contract, "quantityMinimum", "1")),
        "smallest_possible_fill": str(fills[0]),
        "fill_sizes_tested": len(fills) if first_failure is None else None,
        "worst_profit_per_contract": None if worst_ppc is None else str(worst_ppc),
        "worst_quantity": None if worst_q is None else str(worst_q),
        "first_failure": first_failure,
    }


def partial_safe_frontier(*, maker_ask: D | None, observed_hedge_ask: D,
                          maker_tick: D, hedge_tick: D,
                          maker_contract: Mapping[str, Any], hedge_contract: Mapping[str, Any],
                          hedge_depth: D) -> tuple[D, D, dict[str, Any]] | None:
    hedge_limit = min(D("0.99"), observed_hedge_ask + hedge_tick)
    if not (D("0") < hedge_limit < ONE):
        return None
    cap = maker_ask - maker_tick if maker_ask is not None else ONE - maker_tick
    if cap <= 0:
        return None
    steps = (cap / maker_tick).to_integral_value(rounding=ROUND_FLOOR)
    while steps > 0:
        quote = steps * maker_tick
        ok, details = worst_fill_floor(
            maker_price=quote,
            hedge_price=hedge_limit,
            maker_contract=maker_contract,
            hedge_contract=hedge_contract,
            hedge_depth=hedge_depth,
        )
        if ok:
            return quote, hedge_limit, details
        steps -= 1
    return None


async def run() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        contracts: dict[str, dict[str, Any]] = {}
        strict_by_event: list[tuple[Mapping[str, Any], list[dict[str, Any]]]] = []
        for event in events:
            rows = scan.ladder_contracts(event)
            strict_by_event.append((event, rows))
            for c in rows:
                contracts[scan._symbol(c)] = c
        symbols = set(contracts)
        books, statuses = await scan._books(http, symbols)

    increments: dict[str, int] = {}
    excluded_contracts = 0
    for c in contracts.values():
        inc = _qty(c, "quantityIncrement", "1")
        increments[str(inc)] = increments.get(str(inc), 0) + 1
        if not maker.whole_contract_only(c):
            excluded_contracts += 1

    rows: list[dict[str, Any]] = []
    q1_frontier_rows = 0
    pair_directions_seen = 0
    for event, ladder in strict_by_event:
        if len(ladder) < 2:
            continue
        for low, high in scan.monotone_pairs(ladder):
            lb, hb = books.get(scan._symbol(low)), books.get(scan._symbol(high))
            if not isinstance(lb, Mapping) or not isinstance(hb, Mapping):
                continue
            lt, ht = scan.top(lb), scan.top(hb)
            directions = (
                (low, "YES", lt, high, "NO", ht),
                (high, "NO", ht, low, "YES", lt),
            )
            for mc, mo, mt, hc, ho, ht2 in directions:
                pair_directions_seen += 1
                if maker.whole_contract_only(mc):
                    continue
                mb = scan.outcome_book(mt, mo)
                hb2 = scan.outcome_book(ht2, ho)
                hedge_ask = hb2.get("ask")
                hedge_depth = hb2.get("ask_qty")
                maker_tick = scan._tick(mc)
                hedge_tick = scan._tick(hc)
                if not all(isinstance(x, D) for x in (hedge_ask, hedge_depth, maker_tick, hedge_tick)):
                    continue
                assert isinstance(hedge_ask, D) and isinstance(hedge_depth, D)
                assert isinstance(maker_tick, D) and isinstance(hedge_tick, D)
                if hedge_depth <= 0:
                    continue
                maker_ask = mb.get("ask") if isinstance(mb.get("ask"), D) else None
                q1_quote = scan.safe_frontier(maker_ask=maker_ask, hedge_ask=hedge_ask, tick=maker_tick)
                if q1_quote is None:
                    continue
                q1_frontier_rows += 1
                safe = partial_safe_frontier(
                    maker_ask=maker_ask,
                    observed_hedge_ask=hedge_ask,
                    maker_tick=maker_tick,
                    hedge_tick=hedge_tick,
                    maker_contract=mc,
                    hedge_contract=hc,
                    hedge_depth=hedge_depth,
                )
                if safe is None:
                    continue
                quote, hedge_limit, details = safe
                rows.append({
                    "event_ticker": str(event.get("ticker") or ""),
                    "event_title": str(event.get("title") or ""),
                    "maker_symbol": scan._symbol(mc),
                    "maker_outcome": mo,
                    "hedge_symbol": scan._symbol(hc),
                    "hedge_outcome": ho,
                    "maker_quote": str(quote),
                    "hedge_observed_ask": str(hedge_ask),
                    "hedge_limit": str(hedge_limit),
                    "hedge_depth": str(hedge_depth),
                    **details,
                    "settlement_classification": "SAME_EVENT_SOURCE_EXPIRY_L_LT_H",
                    "read_only": True,
                    "order_entry": False,
                    "realized": False,
                })

    rows.sort(key=lambda r: (
        D(str(r.get("smallest_possible_fill") or "0")),
        -D(str(r.get("worst_profit_per_contract") or "0")),
        -D(str(r.get("hedge_depth") or "0")),
    ))
    out = {
        "strict_contracts": len(contracts),
        "excluded_non_whole_maker_contracts": excluded_contracts,
        "quantity_increment_distribution": increments,
        "books_requested": len(symbols),
        "books_ok": sum(1 for s in symbols if statuses.get(s) == 200),
        "pair_directions_seen": pair_directions_seen,
        "fractional_q1_frontier_rows": q1_frontier_rows,
        "fractional_worst_fill_safe_3c_rows": len(rows),
        "top_safe": rows[:20],
        "target_per_filled_contract": str(TARGET),
        "worst_case_policy": "test every maker quantityIncrement multiple from first partial fill through q=1; exact sibling grid/depth; one adverse hedge tick; exact ceil-cent fees",
        "read_only": True,
        "order_entry": False,
        "realized": False,
    }
    print("GEMINI_FRACTIONAL_MAKER_CENSUS " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
