"""Pure post-q5 allocator for conservative Gemini/Kalshi size selection.

This module performs no network or order-entry work. It consumes already-validated
capacity rows (for example q=5/10/25) and chooses the largest size that is supported
by both venue books, exact after-fee economics, and available capital on each venue.

It is intentionally separate from the current live q<=5 guard. Promotion into any
order-capable path requires an explicit later integration step.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

D = Decimal
ALLOWED_QUANTITIES = (D("5"), D("10"), D("25"))
MIN_PROFIT_PER_CONTRACT = D("0.03")


@dataclass(frozen=True)
class ScaleAllocation:
    quantity: Decimal
    gemini_capital: Decimal
    kalshi_capital: Decimal
    total_capital: Decimal
    total_profit: Decimal
    profit_per_contract: Decimal
    gemini_free_after: Decimal
    kalshi_free_after: Decimal


def _d(value: Any) -> Decimal:
    return D(str(value))


def _parse_supported_row(row: dict[str, Any]) -> ScaleAllocation | None:
    if not bool(row.get("supported")):
        return None
    q = _d(row.get("quantity"))
    if q not in ALLOWED_QUANTITIES:
        raise ValueError(f"unsupported dynamic scale quantity={q}")
    if q != q.to_integral_value():
        raise ValueError("dynamic scale quantity must be integral")

    gemini_capital = _d(row.get("gemini_capital"))
    kalshi_capital = _d(row.get("kalshi_capital"))
    total_capital = _d(row.get("total_capital"))
    total_profit = _d(row.get("cushioned_profit", row.get("total_profit")))
    profit_per_contract = _d(
        row.get("cushioned_profit_per_contract", row.get("profit_per_contract"))
    )

    if min(gemini_capital, kalshi_capital, total_capital) < D("0"):
        raise ValueError("capital cannot be negative")
    if abs(total_capital - (gemini_capital + kalshi_capital)) > D("0.000001"):
        raise ValueError("venue capital does not sum to total capital")
    if profit_per_contract < MIN_PROFIT_PER_CONTRACT:
        raise ValueError("supported row violates 3c per-contract profit floor")
    if abs(total_profit - profit_per_contract * q) > D("0.000001"):
        raise ValueError("total profit inconsistent with per-contract profit")

    return ScaleAllocation(
        quantity=q,
        gemini_capital=gemini_capital,
        kalshi_capital=kalshi_capital,
        total_capital=total_capital,
        total_profit=total_profit,
        profit_per_contract=profit_per_contract,
        gemini_free_after=D("0"),
        kalshi_free_after=D("0"),
    )


def choose_dynamic_scale(
    rows: Iterable[dict[str, Any]],
    *,
    gemini_available: Decimal,
    kalshi_available: Decimal,
    max_total_capital: Decimal,
    reserve_buffer_per_venue: Decimal = D("0"),
) -> ScaleAllocation | None:
    """Choose the largest conservative supported size that can be fully reserved.

    Capital is checked separately on Gemini and Kalshi before selecting a row. This
    prevents submitting the first leg of a larger trade when the opposing venue does
    not have enough free capital to complete the pair.
    """
    g_available = D(gemini_available)
    k_available = D(kalshi_available)
    total_limit = D(max_total_capital)
    buffer = D(reserve_buffer_per_venue)
    if min(g_available, k_available, total_limit, buffer) < D("0"):
        raise ValueError("available capital, limit and buffer must be non-negative")

    parsed: list[ScaleAllocation] = []
    seen: set[Decimal] = set()
    for row in rows:
        alloc = _parse_supported_row(row)
        if alloc is None:
            continue
        if alloc.quantity in seen:
            raise ValueError(f"duplicate capacity row for q={alloc.quantity}")
        seen.add(alloc.quantity)
        parsed.append(alloc)

    for alloc in sorted(parsed, key=lambda x: x.quantity, reverse=True):
        if alloc.total_capital > total_limit:
            continue
        if alloc.gemini_capital + buffer > g_available:
            continue
        if alloc.kalshi_capital + buffer > k_available:
            continue
        return ScaleAllocation(
            quantity=alloc.quantity,
            gemini_capital=alloc.gemini_capital,
            kalshi_capital=alloc.kalshi_capital,
            total_capital=alloc.total_capital,
            total_profit=alloc.total_profit,
            profit_per_contract=alloc.profit_per_contract,
            gemini_free_after=g_available - alloc.gemini_capital,
            kalshi_free_after=k_available - alloc.kalshi_capital,
        )
    return None
