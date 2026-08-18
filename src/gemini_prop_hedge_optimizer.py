"""Pure sizing math for sportsbook YES + Gemini NO prediction hedges.

The optimizer works backward from Gemini's executable YES-space depth. A BUY NO
limit at price (1 - YES bid) can consume the corresponding YES-bid quantity.
For guaranteed arbitrage accounting, the Gemini leg is charged at the *worst FOK
limit price for the entire requested quantity*, not at the depth-weighted expected
fill price. Price improvement is tracked separately and never required for profit.

No network I/O or order entry is present here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Sequence

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee

D = Decimal
ONE = D("1")


@dataclass(frozen=True)
class FillLevel:
    no_price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class HedgePlan:
    sportsbook_stake: Decimal
    sportsbook_decimal_odds: Decimal
    sportsbook_total_return: Decimal
    gemini_quantity: Decimal
    gemini_no_limit_price: Decimal
    gemini_guaranteed_max_raw_debit: Decimal
    gemini_guaranteed_max_fee: Decimal
    gemini_guaranteed_max_total_debit: Decimal
    expected_depth_raw_cost: Decimal
    expected_depth_fee: Decimal
    expected_depth_total_cost: Decimal
    expected_fills: tuple[FillLevel, ...]
    pnl_if_prop_hits_guaranteed: Decimal
    pnl_if_prop_misses_guaranteed: Decimal
    worst_case_locked_profit: Decimal
    guaranteed_roi_on_capital: Decimal
    available_depth_through_limit: Decimal


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("increment must be positive")
    if value <= 0:
        return D("0")
    return (value / increment).to_integral_value(rounding=ROUND_FLOOR) * increment


def expected_buy_no_sweep(
    yes_bids: Sequence[tuple[Decimal, Decimal]], quantity: Decimal,
) -> tuple[Decimal, Decimal, tuple[FillLevel, ...]] | None:
    """Expected price-improved BUY NO sweep across a static YES-space snapshot."""
    if quantity <= 0:
        return None
    remaining = quantity
    raw = D("0")
    fees = D("0")
    fills: list[FillLevel] = []
    for yes_price, level_qty in sorted(yes_bids, key=lambda x: x[0], reverse=True):
        if remaining <= 0:
            break
        if level_qty <= 0 or not (D("0") <= yes_price <= ONE):
            continue
        take = min(remaining, level_qty)
        no_price = ONE - yes_price
        if take <= 0 or not (D("0") < no_price < ONE):
            continue
        raw += take * no_price
        # Conservative expected fee: round independently at each consumed level.
        fees += gemini_predictions_taker_fee(no_price, take)
        fills.append(FillLevel(no_price=no_price, quantity=take))
        remaining -= take
    if remaining > 0 or not fills:
        return None
    return raw, fees, tuple(fills)


def guaranteed_pair_pnl(
    *,
    sportsbook_stake: Decimal,
    sportsbook_decimal_odds: Decimal,
    gemini_quantity: Decimal,
    gemini_max_total_debit: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return P/L if prop hits, if prop misses, and the worse of the two."""
    if sportsbook_stake < 0 or sportsbook_decimal_odds <= ONE or gemini_quantity < 0:
        raise ValueError("invalid hedge inputs")
    sportsbook_return = sportsbook_stake * sportsbook_decimal_odds
    total_capital = sportsbook_stake + gemini_max_total_debit
    hit = sportsbook_return - total_capital
    miss = gemini_quantity - total_capital
    return hit, miss, min(hit, miss)


def best_guaranteed_buy_no_hedge(
    *,
    yes_bids: Sequence[tuple[Decimal, Decimal]],
    sportsbook_decimal_odds: Decimal,
    max_matched_payout: Decimal = D("50"),
    sportsbook_stake_increment: Decimal = D("0.01"),
    gemini_quantity_increment: Decimal = D("0.01"),
) -> HedgePlan | None:
    """Choose the largest profitable depth-safe hedge at each reachable NO limit.

    Each cumulative YES-bid level defines a possible FOK BUY NO limit. The plan is
    priced pessimistically as though *every* Gemini contract fills at that worst
    limit price. Therefore any actual price improvement only increases realized P/L.
    Sportsbook stake and Gemini quantity are floored to their configured increments.
    """
    if sportsbook_decimal_odds <= ONE or max_matched_payout <= 0:
        return None

    levels = [
        (p, q) for p, q in sorted(yes_bids, key=lambda x: x[0], reverse=True)
        if D("0") <= p <= ONE and q > 0
    ]
    cumulative_depth = D("0")
    best: HedgePlan | None = None

    for yes_price, level_qty in levels:
        cumulative_depth += level_qty
        usable_depth = min(cumulative_depth, max_matched_payout)
        no_limit = ONE - yes_price
        if not (D("0") < no_limit < ONE):
            continue

        max_stake = floor_to_increment(
            usable_depth / sportsbook_decimal_odds,
            sportsbook_stake_increment,
        )
        if max_stake <= 0:
            continue
        sportsbook_return = max_stake * sportsbook_decimal_odds
        gemini_qty = floor_to_increment(
            min(usable_depth, sportsbook_return),
            gemini_quantity_increment,
        )
        if gemini_qty <= 0:
            continue

        # Guarantee against the submitted FOK limit, not expected price improvement.
        worst_raw = gemini_qty * no_limit
        worst_fee = gemini_predictions_taker_fee(no_limit, gemini_qty)
        worst_total = worst_raw + worst_fee
        hit, miss, worst_profit = guaranteed_pair_pnl(
            sportsbook_stake=max_stake,
            sportsbook_decimal_odds=sportsbook_decimal_odds,
            gemini_quantity=gemini_qty,
            gemini_max_total_debit=worst_total,
        )
        if worst_profit <= 0:
            continue

        sweep = expected_buy_no_sweep(levels, gemini_qty)
        if sweep is None:
            continue
        expected_raw, expected_fee, expected_fills = sweep
        capital = max_stake + worst_total
        plan = HedgePlan(
            sportsbook_stake=max_stake,
            sportsbook_decimal_odds=sportsbook_decimal_odds,
            sportsbook_total_return=sportsbook_return,
            gemini_quantity=gemini_qty,
            gemini_no_limit_price=no_limit,
            gemini_guaranteed_max_raw_debit=worst_raw,
            gemini_guaranteed_max_fee=worst_fee,
            gemini_guaranteed_max_total_debit=worst_total,
            expected_depth_raw_cost=expected_raw,
            expected_depth_fee=expected_fee,
            expected_depth_total_cost=expected_raw + expected_fee,
            expected_fills=expected_fills,
            pnl_if_prop_hits_guaranteed=hit,
            pnl_if_prop_misses_guaranteed=miss,
            worst_case_locked_profit=worst_profit,
            guaranteed_roi_on_capital=worst_profit / capital,
            available_depth_through_limit=usable_depth,
        )
        if best is None or (
            plan.worst_case_locked_profit,
            plan.guaranteed_roi_on_capital,
        ) > (
            best.worst_case_locked_profit,
            best.guaranteed_roi_on_capital,
        ):
            best = plan

    return best
