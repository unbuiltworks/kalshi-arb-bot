"""Read-only partial-fill audit for same-Gemini crypto ladder maker candidates.

For every current best-bid-creating candidate, inspect the actual quantity grids on
both sibling contracts and test every possible maker fill increment up to q=1. The
hedge price is cushioned by one hedge tick and current Gemini maker+taker fees use
ceil-cent rounding. Live promotion should require exact hedgeability and >=3c per
contract for every possible first-fill size, unless a separately-budgeted flatten
path is explicitly accepted.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_execution_shadow import (
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto
from src import gemini_same_venue_crypto_ladder_scan as scan

D = Decimal
ONE = D("1")
TARGET = D("0.03")
logger = logging.getLogger("kalshi_arb.gemini_same_venue_crypto_partial_audit")


def _qty(contract: Mapping[str, Any], name: str, default: str) -> D:
    value = contract.get(name)
    if value is None:
        snake = "quantity_" + ("minimum" if name == "quantityMinimum" else "increment")
        value = contract.get(snake)
    return D(str(value if value is not None else default))


def _contract_map(events: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        for contract in scan.ladder_contracts(event):
            out[scan._symbol(contract)] = dict(contract)
    return out


def audit_candidate(row: Mapping[str, Any], contracts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    maker = contracts[str(row["maker_symbol"])]
    hedge = contracts[str(row["hedge_symbol"])]
    maker_inc = _qty(maker, "quantityIncrement", "1")
    maker_min = _qty(maker, "quantityMinimum", "1")
    hedge_inc = _qty(hedge, "quantityIncrement", "1")
    hedge_min = _qty(hedge, "quantityMinimum", "1")
    hedge_tick = scan._tick(hedge)
    if hedge_tick is None:
        raise ValueError("missing hedge tick")
    maker_price = D(str(row["safe_maker_quote"]))
    observed_hedge = D(str(row["hedge_ask"]))
    hedge_limit = min(D("0.99"), observed_hedge + hedge_tick)

    if maker_inc <= 0 or hedge_inc <= 0 or maker_min <= 0 or hedge_min <= 0:
        raise ValueError("invalid quantity metadata")
    possible: list[dict[str, Any]] = []
    q = maker_inc
    while q <= ONE + D("0.0000001"):
        maker_fill_grid_ok = (q / maker_inc) == (q / maker_inc).to_integral_value()
        hedge_grid_ok = q >= hedge_min and (q / hedge_inc) == (q / hedge_inc).to_integral_value()
        maker_fee = gemini_predictions_maker_fee(maker_price, q)
        hedge_fee = gemini_predictions_taker_fee(hedge_limit, q) if hedge_grid_ok else D("0")
        locked = q - q * (maker_price + hedge_limit) - maker_fee - hedge_fee if hedge_grid_ok else D("-999")
        per_contract = locked / q if hedge_grid_ok and q > 0 else D("-999")
        possible.append({
            "quantity": str(q),
            "maker_fill_grid_ok": maker_fill_grid_ok,
            "hedge_grid_ok": hedge_grid_ok,
            "maker_fee": str(maker_fee),
            "hedge_fee": str(hedge_fee),
            "hedge_limit_one_tick": str(hedge_limit),
            "locked_profit": str(locked),
            "locked_per_contract": str(per_contract),
            "target_3c": bool(hedge_grid_ok and per_contract >= TARGET),
        })
        q += maker_inc

    every_fill = bool(possible) and all(r["target_3c"] for r in possible)
    hedgeable_every_fill = bool(possible) and all(r["hedge_grid_ok"] for r in possible)
    safe_quantities = [r for r in possible if r["target_3c"]]
    return {
        **dict(row),
        "maker_quantity_minimum": str(maker_min),
        "maker_quantity_increment": str(maker_inc),
        "hedge_quantity_minimum": str(hedge_min),
        "hedge_quantity_increment": str(hedge_inc),
        "hedge_tick": str(hedge_tick),
        "hedge_limit_one_tick": str(hedge_limit),
        "possible_first_fill_count": len(possible),
        "hedgeable_every_fill": hedgeable_every_fill,
        "every_possible_fill_preserves_3c": every_fill,
        "smallest_3c_fill": safe_quantities[0]["quantity"] if safe_quantities else None,
        "q_1": possible[-1] if possible else None,
        "worst_fill": min(possible, key=lambda r: D(str(r["locked_per_contract"]))) if possible else None,
        "fill_grid": possible,
        "read_only": True,
        "order_entry": False,
        "realized": False,
    }


async def run() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        strict = [(event, scan.ladder_contracts(event)) for event in events]
        symbols = {scan._symbol(c) for _, contracts in strict for c in contracts}
        books, statuses = await scan._books(http, symbols)
    actionable, joins, telemetry = scan.score(events, books)
    contracts = _contract_map(events)
    audited = [audit_candidate(row, contracts) for row in actionable]
    safe_all = [row for row in audited if row["hedgeable_every_fill"] and row["every_possible_fill_preserves_3c"]]
    near_safe = [row for row in safe_all if isinstance(row.get("seconds_to_expiry"), (int, float)) and 300 < row["seconds_to_expiry"] <= 86400]
    out = {
        **telemetry,
        "books_requested": len(symbols),
        "books_ok": sum(1 for symbol in symbols if statuses.get(symbol) == 200),
        "actionable_best_bid_candidates": len(actionable),
        "every_fill_safe_candidates": len(safe_all),
        "every_fill_safe_next_24h": len(near_safe),
        "top_every_fill_safe": near_safe[:10] or safe_all[:10],
        "audited_actionable": audited[:20],
        "target_per_contract": str(TARGET),
        "hedge_cushion": "one sibling-contract price tick",
        "fee_policy": "Gemini maker+taker ceil-cent; rebates and taker rewards excluded",
        "read_only": True,
        "order_entry": False,
        "realized": False,
    }
    logger.warning("GEMINI_SAME_VENUE_CRYPTO_PARTIAL_AUDIT %s", json.dumps(out, separators=(",", ":"), default=str))
    return out


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
