"""Partial-resilient Gemini-maker quote selection for 15m crypto.

Pure economics helpers only; no network or order entry.  The quote policy differs
from the q=1 census: it deliberately lowers the resting maker price until a chosen
single FIRST execution quantity preserves >=3c/contract after:
- Gemini maker fee rounded conservatively by the shared fee function;
- live Kalshi taker fee;
- one adverse valid Kalshi price-grid tick on the hedge.

This cannot make arbitrarily tiny partial fills safe because fixed cent-rounded fees
can exceed the payout on tiny quantities. Live transport must therefore cancel the
remainder immediately on the first execution and flatten/hard-halt if that execution
is below the configured safe first-fill quantity.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_kalshi_gemini_maker_partial_economics import _hedge_tick

D = Decimal
ONE = D("1")
TARGET = D("0.03")
GEMINI_TICK = D("0.01")


def economics(*, maker_price: D, hedge_price: D, quantity: D, fee_meta: Any) -> dict[str, D]:
    if not (D("0") < maker_price < ONE and D("0") < hedge_price < ONE and quantity > 0):
        raise ValueError("invalid quote economics inputs")
    maker_fee = gemini_predictions_maker_fee(maker_price, quantity)
    hedge_fee = fee_meta.taker_fee_fn()(hedge_price, quantity)
    raw = quantity * (maker_price + hedge_price)
    locked = quantity - raw - maker_fee - hedge_fee
    return {
        "maker_fee": maker_fee,
        "hedge_fee": hedge_fee,
        "raw_cost": raw,
        "locked_profit": locked,
        "locked_per_contract": locked / quantity,
    }


def choose_quote(
    *,
    maker_best_bid: D,
    maker_best_ask: D,
    hedge_observed_price: D,
    hedge_outcome: str,
    kalshi_market: Mapping[str, Any],
    fee_meta: Any,
    min_safe_first_fill: D = D("0.25"),
    target: D = TARGET,
) -> dict[str, Any] | None:
    if min_safe_first_fill < D("0.01") or min_safe_first_fill > ONE:
        raise ValueError("min_safe_first_fill must be in [0.01,1]")
    tick = _hedge_tick(kalshi_market, hedge_outcome, hedge_observed_price)
    hedge_cushioned = min(D("0.999"), hedge_observed_price + tick)
    highest_post_only = maker_best_ask - GEMINI_TICK
    if highest_post_only <= 0:
        return None

    safe_prices: list[D] = []
    p = GEMINI_TICK
    while p <= highest_post_only + D("0.0000001") and p < ONE:
        first = economics(
            maker_price=p,
            hedge_price=hedge_cushioned,
            quantity=min_safe_first_fill,
            fee_meta=fee_meta,
        )
        full = economics(
            maker_price=p,
            hedge_price=hedge_cushioned,
            quantity=ONE,
            fee_meta=fee_meta,
        )
        if first["locked_per_contract"] >= target and full["locked_per_contract"] >= target:
            safe_prices.append(p)
        p += GEMINI_TICK
    if not safe_prices:
        return None

    quote = max(safe_prices)
    first = economics(maker_price=quote, hedge_price=hedge_cushioned, quantity=min_safe_first_fill, fee_meta=fee_meta)
    full = economics(maker_price=quote, hedge_price=hedge_cushioned, quantity=ONE, fee_meta=fee_meta)
    penny = economics(maker_price=quote, hedge_price=hedge_cushioned, quantity=D("0.01"), fee_meta=fee_meta)
    return {
        "maker_quote": quote,
        "maker_best_bid": maker_best_bid,
        "maker_best_ask": maker_best_ask,
        "gap_to_ask": maker_best_ask - quote,
        "improves_bid": quote > maker_best_bid,
        "min_safe_first_fill": min_safe_first_fill,
        "hedge_observed_price": hedge_observed_price,
        "kalshi_adverse_tick": tick,
        "hedge_cushioned_price": hedge_cushioned,
        "first_fill_economics": first,
        "q1_economics": full,
        "q001_economics": penny,
        "tiny_fill_policy": "CANCEL_REMAINDER_FLATTEN_HARD_HALT",
        "target_per_contract": target,
    }
