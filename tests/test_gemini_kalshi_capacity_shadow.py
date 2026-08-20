from decimal import Decimal

from src.gemini_kalshi_capacity_shadow import (
    best_capacity_cushion,
    capacity_economics,
    evaluate_capacity_sizes,
)

D = Decimal


class FakeBook:
    def __init__(self, *, yes=None, no=None):
        self.yes = yes or {}
        self.no = no or {}


def test_gemini_yes_kalshi_no_uses_yes_ask_as_inverse_no_cost():
    row = capacity_economics(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_price=D("0.32"),
        kalshi_yes_price=D("0.41"),
        quantity=D("5"),
        kalshi_fee_multiplier=D("1"),
    )
    assert row.kalshi_contract_cost == D("0.59")
    assert row.gemini_fee == D("0.08")
    assert row.kalshi_fee == D("0.09")
    assert row.total_capital == D("4.72")
    assert row.total_profit == D("0.28")
    assert row.profit_per_contract == D("0.056")


def test_kalshi_yes_gemini_no_uses_yes_price_directly():
    row = capacity_economics(
        direction="KALSHI_YES_GEMINI_NO",
        gemini_price=D("0.25"),
        kalshi_yes_price=D("0.60"),
        quantity=D("5"),
        kalshi_fee_multiplier=D("1"),
    )
    assert row.kalshi_contract_cost == D("0.60")
    assert row.gemini_fee == D("0.07")
    assert row.kalshi_fee == D("0.09")
    assert row.total_capital == D("4.41")
    assert row.total_profit == D("0.59")
    assert row.profit_per_contract == D("0.118")


def test_two_cent_cushion_preserves_three_cent_floor_for_xrp_style_inverse_hedge():
    price, economics, cents = best_capacity_cushion(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_price=D("0.32"),
        original_kalshi_yes_price=D("0.41"),
        quantity=D("5"),
        kalshi_fee_multiplier=D("1"),
    )
    assert cents == 2
    assert price == D("0.39")
    assert economics.profit_per_contract >= D("0.03")


def test_capacity_ladder_can_be_kalshi_rescued_but_gemini_capped_at_q10():
    # Original ask=41c has no resting YES bid.  The 2c safer ask=39c reaches
    # 40c/39c YES bids with ample Kalshi depth.  Gemini shows only 12 contracts,
    # so q5 and q10 are supported while q25 is conservatively rejected.
    book = FakeBook(
        yes={D("0.40"): D("8"), D("0.39"): D("22")},
        no={},
    )
    rows = evaluate_capacity_sizes(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_price=D("0.32"),
        original_kalshi_yes_price=D("0.41"),
        gemini_available_size=D("12"),
        kalshi_book=book,
        kalshi_fee_multiplier=D("1"),
    )
    by_q = {D(row["quantity"]): row for row in rows}
    assert by_q[D("5")]["supported"] is True
    assert by_q[D("10")]["supported"] is True
    assert by_q[D("25")]["supported"] is False
    assert by_q[D("25")]["reason"] == "gemini_displayed_depth_insufficient"
    assert by_q[D("5")]["rescued_by_cushion"] is True
    assert by_q[D("10")]["rescued_by_cushion"] is True


def test_capacity_ladder_rejects_size_when_fee_aware_profit_falls_below_floor():
    # Near the floor, cent-rounded total fees can make a larger same-price size fail
    # the 3c/contract requirement even though raw complementary cost is below $1.
    book = FakeBook(yes={D("0.50"): D("100")}, no={D("0.50"): D("100")})
    rows = evaluate_capacity_sizes(
        direction="KALSHI_YES_GEMINI_NO",
        gemini_price=D("0.44"),
        original_kalshi_yes_price=D("0.50"),
        gemini_available_size=D("100"),
        kalshi_book=book,
        kalshi_fee_multiplier=D("1"),
        sizes=(D("5"),),
    )
    assert rows[0]["supported"] is False
    assert rows[0]["reason"] == "profit_per_contract_below_3c"


def test_invalid_non_integer_capacity_quantity_fails_closed():
    try:
        capacity_economics(
            direction="KALSHI_YES_GEMINI_NO",
            gemini_price=D("0.25"),
            kalshi_yes_price=D("0.60"),
            quantity=D("5.5"),
            kalshi_fee_multiplier=D("1"),
        )
    except ValueError as exc:
        assert "positive integer" in str(exc)
    else:
        raise AssertionError("non-integer capacity quantity should fail closed")
