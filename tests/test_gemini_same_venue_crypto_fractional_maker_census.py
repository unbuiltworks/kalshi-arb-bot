from decimal import Decimal as D

from src import gemini_same_venue_crypto_fractional_maker_census as c


def _contract(qmin: str, qinc: str):
    return {"quantityMinimum": qmin, "quantityIncrement": qinc}


def test_possible_fill_quantities_are_all_increment_multiples():
    assert c.possible_fill_quantities(D("0.25")) == [D("0.25"), D("0.50"), D("0.75"), D("1.00")]


def test_one_cent_grid_worst_fill_cannot_support_three_cent_floor():
    ok, details = c.worst_fill_floor(
        maker_price=D("0.40"),
        hedge_price=D("0.40"),
        maker_contract=_contract("0.01", "0.01"),
        hedge_contract=_contract("0.01", "0.01"),
        hedge_depth=D("10"),
    )
    assert ok is False
    assert details["first_failure"]["quantity"] == "0.01"
    assert details["first_failure"]["reason"] == "FEE_ROUNDED_FLOOR"


def test_hedge_grid_must_accept_smallest_maker_fill():
    ok, details = c.worst_fill_floor(
        maker_price=D("0.10"),
        hedge_price=D("0.10"),
        maker_contract=_contract("0.25", "0.25"),
        hedge_contract=_contract("0.50", "0.50"),
        hedge_depth=D("10"),
    )
    assert ok is False
    assert details["first_failure"] == {"reason": "HEDGE_GRID", "quantity": "0.25"}


def test_large_increment_can_survive_fee_rounding():
    ok, details = c.worst_fill_floor(
        maker_price=D("0.40"),
        hedge_price=D("0.40"),
        maker_contract=_contract("0.50", "0.50"),
        hedge_contract=_contract("0.50", "0.50"),
        hedge_depth=D("10"),
    )
    assert ok is True
    assert D(details["worst_profit_per_contract"]) >= D("0.03")
