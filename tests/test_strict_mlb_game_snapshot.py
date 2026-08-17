from decimal import Decimal

from src.strict_mlb_game_snapshot import capacity_ladder, exact_q_plan, invert_book
from src.strict_mlb_prop_snapshot import TopBook


def test_invert_book_swaps_yes_and_no_executable_sides():
    book = TopBook(
        yes_ask=Decimal("0.61"),
        yes_depth=Decimal("7"),
        no_ask=Decimal("0.42"),
        no_depth=Decimal("11"),
    )
    inv = invert_book(book)
    assert inv.yes_ask == Decimal("0.42")
    assert inv.yes_depth == Decimal("11")
    assert inv.no_ask == Decimal("0.61")
    assert inv.no_depth == Decimal("7")


def test_exact_q_plan_requires_depth_and_positive_after_fees():
    poly = TopBook(
        yes_ask=Decimal("0.42"),
        yes_depth=Decimal("20"),
        no_ask=Decimal("0.60"),
        no_depth=Decimal("20"),
    )
    kalshi = TopBook(
        yes_ask=Decimal("0.60"),
        yes_depth=Decimal("20"),
        no_ask=Decimal("0.50"),
        no_depth=Decimal("20"),
    )
    plan = exact_q_plan(poly, kalshi, kalshi_fee_multiplier=Decimal("0.5"), q=5)
    assert plan is not None
    assert plan["quantity"] == 5
    assert Decimal(plan["conservative_locked_profit"]) > 0
    assert exact_q_plan(poly, kalshi, kalshi_fee_multiplier=Decimal("0.5"), q=25) is None


def test_capacity_ladder_does_not_invent_depth_beyond_top_level():
    poly = TopBook(
        yes_ask=Decimal("0.40"),
        yes_depth=Decimal("10"),
        no_ask=Decimal("0.62"),
        no_depth=Decimal("10"),
    )
    kalshi = TopBook(
        yes_ask=Decimal("0.61"),
        yes_depth=Decimal("10"),
        no_ask=Decimal("0.50"),
        no_depth=Decimal("10"),
    )
    rows = capacity_ladder(poly, kalshi, kalshi_fee_multiplier=Decimal("0.5"))
    by_q = {row["quantity"]: row["positive_plan"] for row in rows}
    assert by_q[1] is not None
    assert by_q[2] is not None
    assert by_q[5] is not None
    assert by_q[10] is not None
    assert by_q[25] is None
    assert by_q[50] is None
    assert by_q[100] is None
