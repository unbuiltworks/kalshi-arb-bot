"""Fail-closed MLB paper-probe preference classification.

Read-only research helper. The preferred threshold is expressed per contract, not
per packet, so multi-contract <=$5 plans cannot qualify merely because their total
profit reaches three cents.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

MIN_PREFERRED_NET_PER_CONTRACT = Decimal("0.03")


def preferred_paper_probe(
    probe: dict[str, Any] | None,
    *,
    min_net_per_contract: Decimal = MIN_PREFERRED_NET_PER_CONTRACT,
) -> bool:
    """Return True only when conservative profit is >= threshold per contract.

    Malformed, non-finite, non-positive quantity/profit, or invalid threshold
    evidence fails closed.
    """
    if not isinstance(probe, dict):
        return False
    try:
        quantity = Decimal(str(probe.get("quantity")))
        total_profit = Decimal(str(probe.get("conservative_locked_profit")))
        threshold = Decimal(str(min_net_per_contract))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if not quantity.is_finite() or quantity <= 0:
        return False
    if quantity != quantity.to_integral_value():
        return False
    if not total_profit.is_finite() or total_profit <= 0:
        return False
    if not threshold.is_finite() or threshold <= 0:
        return False
    return (total_profit / quantity) >= threshold
