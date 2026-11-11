"""Fail-closed order ownership and shared-exposure admission for concurrent L5 markets.

This module does not submit orders or alter existing live configuration. The
execution adapter must call authorize_cancel before any cancel and
authorize_new_buy before submitting a maker BUY. Reservation accounting must
include pending cancels/replaces until venue acknowledgement and reconciliation.
"""
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence


class UnsafeOrderAction(ValueError):
    """Action denied without side effects."""


def _money(value):
    if isinstance(value, bool):
        raise UnsafeOrderAction("invalid numeric value")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise UnsafeOrderAction("invalid numeric value") from None
    if not d.is_finite() or d < 0:
        raise UnsafeOrderAction("invalid numeric value")
    return d


def authorize_cancel(*, actor_strategy: str, actor_market: str,
                     order: Mapping) -> str:
    """Return venue order ID only if ownership is exact and unambiguous."""
    if not actor_strategy or not actor_market or not isinstance(order, Mapping):
        raise UnsafeOrderAction("missing actor or order")
    order_id = order.get("id")
    if not isinstance(order_id, str) or not order_id.strip():
        raise UnsafeOrderAction("missing order id")
    if order.get("strategy") != actor_strategy or order.get("market") != actor_market:
        raise UnsafeOrderAction("cross-strategy or cross-market cancellation")
    return order_id


def authorize_new_buy(*, actor_strategy: str, actor_market: str,
                      pending_orders: Sequence[Mapping],
                      filled_cost_by_strategy: Mapping[str, object],
                      shared_cap_usd: object, strategy_cap_usd: object,
                      price: object, qty: object,
                      halt: bool, reconciliation_clean: bool) -> Decimal:
    """Return additional notional after checking *all* account reservations.

    Pending includes orders in submit, resting, cancel-pending and
    replace-pending states, including both old and replacement orders.
    Callers must atomically reserve the returned notional before submission;
    this check alone is NOT a cross-process lock or a trading authorization.
    """
    if type(halt) is not bool or type(reconciliation_clean) is not bool:\n        raise UnsafeOrderAction("ambiguous risk state")\n    if not actor_strategy or not actor_market or halt or not reconciliation_clean:
        raise UnsafeOrderAction("halt, uncertain reconciliation or missing actor")
    if not isinstance(pending_orders, (list, tuple)) or not isinstance(filled_cost_by_strategy, Mapping):
        raise UnsafeOrderAction("incomplete account exposure")
    if not isinstance(actor_strategy, str) or not isinstance(actor_market, str):\n        raise UnsafeOrderAction("invalid actor")\n    cap, own_cap = _money(shared_cap_usd), _money(strategy_cap_usd)
    px, quantity = _money(price), _money(qty)
    if not Decimal("0") < px < Decimal("1") or quantity <= 0:
        raise UnsafeOrderAction("invalid buy")
    add = px * quantity
    filled = {}
    for strategy, cost in filled_cost_by_strategy.items():
        if not isinstance(strategy, str) or not strategy:
            raise UnsafeOrderAction("unknown inventory owner")
        filled[strategy] = _money(cost)
    pending_total = Decimal("0")
    own_pending = Decimal("0")
    seen = set()
    for order in pending_orders:
        if not isinstance(order, Mapping):
            raise UnsafeOrderAction("unreadable pending order")
        oid, strategy, market = order.get("id"), order.get("strategy"), order.get("market")
        if not isinstance(oid, str) or not oid or oid in seen:
            raise UnsafeOrderAction("missing or duplicate pending order ID")
        if not isinstance(strategy, str) or not strategy or not isinstance(market, str) or not market:
            raise UnsafeOrderAction("unowned pending order")
        seen.add(oid)
        notional = _money(order.get("reserved_usd"))
        pending_total += notional
        if strategy == actor_strategy:
            own_pending += notional
    if sum(filled.values(), Decimal("0")) + pending_total + add > cap:
        raise UnsafeOrderAction("shared account cap")
    if filled.get(actor_strategy, Decimal("0")) + own_pending + add > own_cap:
        raise UnsafeOrderAction("strategy cap")
    return add
