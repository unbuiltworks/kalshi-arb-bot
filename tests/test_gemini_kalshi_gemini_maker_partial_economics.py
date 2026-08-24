from decimal import Decimal

from src.gemini_kalshi_gemini_maker_partial_economics import (
    _tick_for_yes_price,
    partial_economics,
)

D = Decimal


class _Fees:
    def taker_fee_fn(self):
        return lambda price, quantity: D("0.01") if quantity > 0 else D("0")


MARKET = {
    "price_ranges": [
        {"start": "0.0000", "end": "0.1000", "step": "0.0010"},
        {"start": "0.1000", "end": "0.9000", "step": "0.0100"},
        {"start": "0.9000", "end": "1.0000", "step": "0.0010"},
    ]
}


def test_boundary_tick_fails_conservatively_to_larger_step():
    assert _tick_for_yes_price(MARKET, D("0.1000")) == D("0.0100")
    assert _tick_for_yes_price(MARKET, D("0.9000")) == D("0.0100")
    assert _tick_for_yes_price(MARKET, D("0.9500")) == D("0.0010")


def test_tiny_partial_is_rejected_but_large_fill_can_clear_target():
    candidate = {
        "maker_quote": D("0.89"),
        "maker_outcome": "YES",
        "hedge_outcome": "NO",
        "hedge_price": D("0.044"),
    }
    out = partial_economics(candidate, _Fees(), MARKET)
    assert out["kalshi_adverse_tick"] == "0.0010"
    assert out["hedge_cushioned_price"] == "0.0450"
    assert out["q_0_01"]["locked_profit"] < "0"
    assert D(out["q_1_00"]["locked_per_contract"]) >= D("0.03")
    assert D(out["smallest_nonnegative_quantity"]) > D("0.01")
    assert D(out["smallest_3c_per_contract_quantity"]) > D("0.50")


def test_partial_grid_covers_every_possible_gemini_increment():
    candidate = {
        "maker_quote": D("0.89"),
        "maker_outcome": "YES",
        "hedge_outcome": "NO",
        "hedge_price": D("0.044"),
    }
    out = partial_economics(candidate, _Fees(), MARKET)
    rows = out["all_quantity_rows"]
    assert len(rows) == 100
    assert rows[0]["quantity"] == "0.01"
    assert rows[-1]["quantity"] == "1"
