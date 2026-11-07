"""Venue-confirmed 2026-11-07 12:30Z BTC15m settlement regression fixture.

This is a historical evidence fixture, NOT a simulator, trading authorization,
or claim that a counterfactual quote would have filled.
"""
from decimal import Decimal

WINDOW = "cpc-btc-updown-15m-2026-11-07-1230z"
ORDERS = (
    ("DOWN", "0.5000", "FILLED", "l5f-b195e7360f86e0e4b6c9363a"),
    ("UP", "0.4500", "FILLED", "l5f-83557f64dd82a6809828ef7d"),
    ("UP", "0.3500", "CANCELED", "l5f-2c1c0f64c13ec71c5cc7ab5e"),
    ("UP", "0.4600", "FILLED", "l5f-fc3f9809135cd723b717ab4f"),
    ("DOWN", "0.4900", "CANCELED", "l5f-19329b90120cb4523271ea9f"),
)
# Venue-reconciled average bases (not necessarily displayed order prices).
UP_TOTAL_BASIS = Decimal("0.9100")
DOWN_TOTAL_BASIS = Decimal("0.5000")
UP_QTY = 2
DOWN_QTY = 1
UP_SETTLEMENT = Decimal("0")
CONFIRMED_FEES = Decimal("0")
CONFIRMED_REBATES = Decimal("0")


def settlement_net(up_qty, down_qty, up_basis, down_basis, up_settlement,
                   fees=Decimal("0"), confirmed_rebates=Decimal("0")):
    """Exact binary-contract payout; never include uncredited rebate estimates."""
    assert up_settlement in (Decimal("0"), Decimal("1"))
    payout = Decimal(up_qty) * up_settlement + Decimal(down_qty) * (1 - up_settlement)
    return payout - up_basis - down_basis - fees + confirmed_rebates


def test_live_window_order_lifecycle_is_not_three_submissions():
    assert len(ORDERS) == 5
    assert sum(o[2] == "FILLED" for o in ORDERS) == 3
    assert sum(o[2] == "CANCELED" for o in ORDERS) == 2
    assert len({o[3] for o in ORDERS}) == 5


def test_live_settlement_loss_is_unmatched_inventory_not_pair():
    # First matched pair cost = 0.9550 based on venue basis accounting.
    matched_profit = Decimal("1") - Decimal("0.9550")
    residual_loss = -Decimal("0.4550")
    assert matched_profit == Decimal("0.0450")
    assert residual_loss == Decimal("-0.4550")
    assert matched_profit + residual_loss == Decimal("-0.4100")
    assert settlement_net(UP_QTY, DOWN_QTY, UP_TOTAL_BASIS, DOWN_TOTAL_BASIS,
                          UP_SETTLEMENT, CONFIRMED_FEES, CONFIRMED_REBATES) == Decimal("-0.4100")


def test_hedge_does_not_make_unequal_inventory_risk_free():
    if_up_wins = settlement_net(UP_QTY, DOWN_QTY, UP_TOTAL_BASIS, DOWN_TOTAL_BASIS, Decimal("1"))
    if_down_wins = settlement_net(UP_QTY, DOWN_QTY, UP_TOTAL_BASIS, DOWN_TOTAL_BASIS, Decimal("0"))
    assert if_up_wins == Decimal("0.5900")
    assert if_down_wins == Decimal("-0.4100")
    assert if_up_wins != if_down_wins


def test_uncredited_rebate_cannot_offset_settled_loss():
    estimated_uncredited_rebate = Decimal("0.0152")
    assert CONFIRMED_REBATES == Decimal("0")
    assert estimated_uncredited_rebate != CONFIRMED_REBATES
    assert settlement_net(UP_QTY, DOWN_QTY, UP_TOTAL_BASIS, DOWN_TOTAL_BASIS,
                          UP_SETTLEMENT, confirmed_rebates=CONFIRMED_REBATES) == Decimal("-0.4100")
