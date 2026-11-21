"""PARENT_ORDER_SURFACE_GEOMETRY_V1 -- the leg decode, and the two limits.

One function here can silently invert every price in the study: `_leg` decides
whether the order owner BOUGHT or SOLD from which asset id is the collateral.
Getting it backwards turns a 0.04 buy into a 25.0 sell. It is tested against
the real decoded L5 transaction that anchored the whole order-hash result.

The other two tests are about honesty rather than arithmetic: executed quantity
must never be called order size, and first->last execution must never be called
order lifetime.
"""
from __future__ import annotations

import inspect

import pytest

from analysis.l5.causal import parent_geometry as G


def test_a_usdc_maker_leg_is_a_buy_and_reproduces_the_anchor_transaction():
    """The real decoded L5 row: makerAssetId 0 (USDC), 5,000,000 units of
    collateral for 125,000,000 units of token = 125 shares at $0.04, which
    matched the public feed's `price 0.04, size 125` exactly."""
    shares, usd, direction = G._leg({
        "maker_asset_id": 0,
        "taker_asset_id": 113116193808386768531610752894886301079296714459607398262432426932154043938214,
        "maker_amount_filled": 5_000_000,
        "taker_amount_filled": 125_000_000,
    })
    assert direction == "BUY"
    assert shares == pytest.approx(125.0)
    assert usd == pytest.approx(5.0)
    assert usd / shares == pytest.approx(0.04)


def test_a_token_maker_leg_is_a_sell_and_the_price_is_not_inverted():
    shares, usd, direction = G._leg({
        "maker_asset_id": 99,
        "taker_asset_id": 0,
        "maker_amount_filled": 125_000_000,
        "taker_amount_filled": 5_000_000,
    })
    assert direction == "SELL"
    assert shares == pytest.approx(125.0)
    assert usd == pytest.approx(5.0)
    assert usd / shares == pytest.approx(0.04)


def test_a_leg_with_no_collateral_side_is_refused_rather_than_guessed():
    assert G._leg({"maker_asset_id": 1, "taker_asset_id": 2,
                   "maker_amount_filled": 1, "taker_amount_filled": 1}) is None
    assert G._leg({"maker_asset_id": 0, "taker_asset_id": 0,
                   "maker_amount_filled": 1, "taker_amount_filled": 1}) is None


def test_quantity_is_named_executed_and_labelled_a_lower_bound():
    """OrderFilled reports what MATCHED. An order for 200 that filled 100 and
    was cancelled is indistinguishable from an order for 100."""
    src = inspect.getsource(G)
    assert "executed_qty" in src and '"order_size"' not in src
    assert "LOWER BOUND on order size" in src
    assert "EXECUTED QUANTITY IS NOT ORDER SIZE" in src


def test_lifetime_is_named_an_execution_lower_bound_not_persistence():
    src = inspect.getsource(G)
    assert "execution_lifetime_lower_bound_s" in src
    assert "EXECUTION LIFETIME IS NOT ORDER LIFETIME" in src
    assert "says nothing about when the order was submitted" in src


def test_the_base_lot_question_is_asked_per_regime_not_pooled():
    """A base lot appears at a similar rate in every probability regime; a
    regime artifact concentrates. Pooling the shares would hide that."""
    src = inspect.getsource(G.geometry)
    assert "share_100_by_probability" in src
    assert "a regime artifact concentrates" in src


@pytest.mark.parametrize("p,expect", [
    (0.05, "<=0.10"), (0.20, "0.10-0.30"), (0.45, "0.30-0.50"),
    (0.60, "0.50-0.70"), (0.85, "0.70-0.90"), (0.95, ">0.90")])
def test_probability_bins_partition_the_line(p, expect):
    assert G._bin_p(p) == expect
