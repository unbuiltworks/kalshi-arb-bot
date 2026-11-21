"""Concurrency safety checks for joint BTC15m/BTC5m execution."""
from decimal import Decimal
import pytest

from src.l5_joint_order_guard import UnsafeOrderAction, authorize_cancel, authorize_new_buy


def test_cancel_only_owned_exact_market():
    order = {"id": "venue-15", "strategy": "btc15m", "market": "15m-window"}
    assert authorize_cancel(actor_strategy="btc15m", actor_market="15m-window", order=order) == "venue-15"
    for strategy, market in [("btc5m", "15m-window"), ("btc15m", "5m-window")]:
        with pytest.raises(UnsafeOrderAction):
            authorize_cancel(actor_strategy=strategy, actor_market=market, order=order)


def test_shared_cap_counts_both_markets_and_pending_cancel():
    pending = [
        {"id": "old15", "strategy": "btc15m", "market": "15m", "reserved_usd": "1.00"},
        {"id": "cancel5", "strategy": "btc5m", "market": "5m", "reserved_usd": "1.00"},
        {"id": "replace5", "strategy": "btc5m", "market": "5m", "reserved_usd": "1.00"},
    ]
    kwargs = dict(actor_strategy="btc5m", actor_market="5m", pending_orders=pending,
                  filled_cost_by_strategy={"btc15m": "1.00", "btc5m": "0.50"},
                  shared_cap_usd="5.00", strategy_cap_usd="3.00",
                  price="0.50", qty="1", halt=False, reconciliation_clean=True)
    assert authorize_new_buy(**kwargs) == Decimal("0.50")
    with pytest.raises(UnsafeOrderAction, match="shared account cap"):
        authorize_new_buy(**{**kwargs, "qty": "1.01"})


def test_halt_and_uncertainty_fail_closed():
    base = dict(actor_strategy="btc15m", actor_market="15m", pending_orders=[],
                filled_cost_by_strategy={}, shared_cap_usd="5", strategy_cap_usd="1",
                price="0.5", qty="1", halt=False, reconciliation_clean=True)
    for override in [{"halt": True}, {"reconciliation_clean": False},
                     {"pending_orders": [{"id": "x", "reserved_usd": "0.1"}]},
                     {"price": "NaN"}, {"qty": "-1"}]:
        with pytest.raises(UnsafeOrderAction):
            authorize_new_buy(**{**base, **override})


def test_strategy_cap_and_duplicate_reservations():
    base = dict(actor_strategy="btc5m", actor_market="5m",
                pending_orders=[{"id": "x", "strategy": "btc5m", "market": "5m", "reserved_usd": "0.9"}],
                filled_cost_by_strategy={"btc5m": "0.5"}, shared_cap_usd="5",
                strategy_cap_usd="1.5", price="0.5", qty="0.5",
                halt=False, reconciliation_clean=True)
    with pytest.raises(UnsafeOrderAction, match="strategy cap"):
        authorize_new_buy(**base)
    with pytest.raises(UnsafeOrderAction, match="duplicate"):
        authorize_new_buy(**{**base, "pending_orders": base["pending_orders"] * 2})


def test_ambiguous_risk_flags_are_rejected():
    base = dict(actor_strategy="btc5m", actor_market="5m", pending_orders=[],
                filled_cost_by_strategy={}, shared_cap_usd="5", strategy_cap_usd="2",
                price="0.50", qty="1", halt=False, reconciliation_clean=True)
    for changes in ({"halt": "false"}, {"halt": 0},
                    {"reconciliation_clean": "true"},
                    {"reconciliation_clean": 1},
                    {"actor_strategy": 5}, {"actor_market": 5}):
        with pytest.raises(UnsafeOrderAction):
            authorize_new_buy(**{**base, **changes})


def test_pending_replacement_must_count_both_reservations():
    base = dict(actor_strategy="btc5m", actor_market="5m",
                pending_orders=[
                    {"id": "old", "strategy": "btc5m", "market": "5m", "reserved_usd": "0.8"},
                    {"id": "new", "strategy": "btc5m", "market": "5m", "reserved_usd": "0.8"},
                ],
                filled_cost_by_strategy={"btc15m": "1.0"},
                shared_cap_usd="2.0", strategy_cap_usd="2.0",
                price="0.5", qty="0.1", halt=False, reconciliation_clean=True)
    with pytest.raises(UnsafeOrderAction, match="shared account cap"):
        authorize_new_buy(**base)
