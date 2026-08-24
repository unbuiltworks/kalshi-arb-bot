from decimal import Decimal

from src.gemini_kalshi_gemini_maker_safe_quote import choose_quote

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


def test_xrp_like_quote_sacrifices_price_for_quarter_fill_safety():
    out = choose_quote(
        maker_best_bid=D("0.00"),
        maker_best_ask=D("1.00"),
        hedge_observed_price=D("0.073"),
        hedge_outcome="NO",
        kalshi_market=MARKET,
        fee_meta=_Fees(),
        min_safe_first_fill=D("0.25"),
    )
    assert out is not None
    assert out["maker_quote"] == D("0.81")
    assert out["first_fill_economics"]["locked_per_contract"] >= D("0.03")
    assert out["q1_economics"]["locked_per_contract"] >= D("0.03")
    assert out["q001_economics"]["locked_profit"] < 0


def test_more_resilient_first_fill_requires_lower_quote():
    q25 = choose_quote(
        maker_best_bid=D("0"), maker_best_ask=D("1"), hedge_observed_price=D("0.073"),
        hedge_outcome="NO", kalshi_market=MARKET, fee_meta=_Fees(), min_safe_first_fill=D("0.25")
    )
    q10 = choose_quote(
        maker_best_bid=D("0"), maker_best_ask=D("1"), hedge_observed_price=D("0.073"),
        hedge_outcome="NO", kalshi_market=MARKET, fee_meta=_Fees(), min_safe_first_fill=D("0.10")
    )
    assert q25 is not None and q10 is not None
    assert q10["maker_quote"] < q25["maker_quote"]
    assert q10["first_fill_economics"]["locked_per_contract"] >= D("0.03")


def test_no_quote_when_touch_is_too_low():
    out = choose_quote(
        maker_best_bid=D("0"), maker_best_ask=D("0.05"), hedge_observed_price=D("0.95"),
        hedge_outcome="YES", kalshi_market=MARKET, fee_meta=_Fees(), min_safe_first_fill=D("0.25")
    )
    assert out is None
