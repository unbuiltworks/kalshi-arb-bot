import inspect
from decimal import Decimal

from src.strict_mlb_prop_snapshot import (
    TopBook,
    best_conservative_plan,
    conservative_kalshi_fee,
    discover_strict_candidates,
    kalshi_top_book,
    poly_top_book,
    polymarket_fee,
)


def test_poly_book_derives_no_ask_from_best_yes_bid() -> None:
    book = poly_top_book(
        {
            "marketData": {
                "bids": [{"px": {"value": "0.42"}, "qty": "12"}],
                "offers": [{"px": {"value": "0.44"}, "qty": "8"}],
            }
        }
    )
    assert book is not None
    assert book.yes_ask == Decimal("0.44")
    assert book.yes_depth == Decimal("8")
    assert book.no_ask == Decimal("0.58")
    assert book.no_depth == Decimal("12")


def test_kalshi_book_uses_highest_bid_as_reciprocal_ask() -> None:
    book = kalshi_top_book(
        {
            "orderbook_fp": {
                "yes_dollars": [["0.30", "100"], ["0.39", "7"]],
                "no_dollars": [["0.40", "100"], ["0.52", "9"]],
            }
        }
    )
    assert book is not None
    assert book.yes_ask == Decimal("0.48")
    assert book.yes_depth == Decimal("9")
    assert book.no_ask == Decimal("0.61")
    assert book.no_depth == Decimal("7")


def test_fee_models_are_conservative() -> None:
    assert polymarket_fee(100, Decimal("0.50")) == Decimal("1.25")
    # Standard quadratic: 0.07 * fee_multiplier(1) * 1 * .5 * .5 = .0175,
    # deliberately ceiled to a whole cent for the snapshot.
    assert conservative_kalshi_fee(1, Decimal("0.50"), Decimal("1")) == Decimal("0.02")
    # Series fee_multiplier scales the standard 0.07 coefficient rather than replacing it.
    assert conservative_kalshi_fee(100, Decimal("0.50"), Decimal("1")) == Decimal("1.75")
    assert conservative_kalshi_fee(100, Decimal("0.50"), Decimal("2")) == Decimal("3.50")


def test_positive_plan_requires_after_fee_lock() -> None:
    # Raw cost .90 leaves enough room for both modeled fees.
    poly = TopBook(
        yes_ask=Decimal("0.40"),
        yes_depth=Decimal("5"),
        no_ask=Decimal("0.61"),
        no_depth=Decimal("5"),
    )
    kalshi = TopBook(
        yes_ask=Decimal("0.62"),
        yes_depth=Decimal("5"),
        no_ask=Decimal("0.50"),
        no_depth=Decimal("5"),
    )
    plan = best_conservative_plan(
        poly, kalshi, kalshi_fee_multiplier=Decimal("1")
    )
    assert plan is not None
    assert plan["direction"] == "POLY_YES_KALSHI_NO"
    assert Decimal(plan["conservative_locked_profit"]) > 0
    assert plan["quantity"] <= 5


def test_raw_gap_that_fails_fees_is_rejected() -> None:
    poly = TopBook(
        yes_ask=Decimal("0.49"),
        yes_depth=Decimal("2"),
        no_ask=Decimal("0.52"),
        no_depth=Decimal("2"),
    )
    kalshi = TopBook(
        yes_ask=Decimal("0.50"),
        yes_depth=Decimal("2"),
        no_ask=Decimal("0.50"),
        no_depth=Decimal("2"),
    )
    assert best_conservative_plan(
        poly, kalshi, kalshi_fee_multiplier=Decimal("1")
    ) is None


def test_depth_caps_quantity() -> None:
    poly = TopBook(
        yes_ask=Decimal("0.30"),
        yes_depth=Decimal("3.9"),
        no_ask=Decimal("0.71"),
        no_depth=Decimal("20"),
    )
    kalshi = TopBook(
        yes_ask=Decimal("0.72"),
        yes_depth=Decimal("20"),
        no_ask=Decimal("0.50"),
        no_depth=Decimal("20"),
    )
    plan = best_conservative_plan(
        poly, kalshi, kalshi_fee_multiplier=Decimal("1")
    )
    assert plan is not None
    assert plan["quantity"] <= 3


def test_strict_candidate_identity_does_not_use_host_local_calendar_date() -> None:
    source = inspect.getsource(discover_strict_candidates)
    assert ".astimezone().date()" not in source
