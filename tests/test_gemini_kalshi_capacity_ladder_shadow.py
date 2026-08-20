from decimal import Decimal

from src import gemini_kalshi_capacity_ladder_shadow as ladder

D = Decimal


class FakeBook:
    def __init__(self, *, yes=None, no=None):
        self.yes = yes or {}
        self.no = no or {}


def test_extended_ladder_is_ordered_and_bounded_for_research():
    assert ladder.CAPACITY_LADDER == (
        D("5"), D("10"), D("15"), D("25"), D("50"), D("100")
    )


def test_extended_ladder_reports_dynamic_max_from_same_prices_and_depth():
    # Enough Gemini depth for 50 but not 100; ample Kalshi depth.  This should
    # support every tested rung through q50 and reject q100 on Gemini depth.
    book = FakeBook(yes={D("0.40"): D("200")}, no={})
    rows = ladder._evaluate_extended(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_price=D("0.32"),
        original_kalshi_yes_price=D("0.41"),
        gemini_available_size=D("60"),
        kalshi_book=book,
        kalshi_fee_multiplier=D("1"),
    )
    by_q = {D(row["quantity"]): row for row in rows}
    assert all(by_q[q]["supported"] for q in (D("5"), D("10"), D("15"), D("25"), D("50")))
    assert by_q[D("100")]["supported"] is False
    assert by_q[D("100")]["reason"] == "gemini_displayed_depth_insufficient"


def test_extended_ladder_can_be_capped_by_kalshi_depth():
    # Gemini has plenty, but only 31 contracts can be reached on Kalshi within
    # the allowed same-price/cushion envelope. q25 is supported; q50/q100 are not.
    book = FakeBook(yes={D("0.40"): D("10"), D("0.39"): D("21")}, no={})
    rows = ladder._evaluate_extended(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_price=D("0.32"),
        original_kalshi_yes_price=D("0.41"),
        gemini_available_size=D("200"),
        kalshi_book=book,
        kalshi_fee_multiplier=D("1"),
    )
    by_q = {D(row["quantity"]): row for row in rows}
    assert by_q[D("25")]["supported"] is True
    assert by_q[D("50")]["supported"] is False
    assert by_q[D("50")]["reason"] == "kalshi_cumulative_depth_insufficient"
