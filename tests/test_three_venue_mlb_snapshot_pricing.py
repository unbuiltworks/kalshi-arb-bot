from decimal import Decimal

from src.three_venue_mlb_snapshot_pricing import kalshi_nested_top_book


def test_kalshi_nested_top_book_prefers_direct_asks():
    book = kalshi_nested_top_book({
        "yes_ask_dollars": "0.44",
        "yes_ask_size_fp": "12.00",
        "no_ask_dollars": "0.58",
        "no_ask_size_fp": "9.00",
        "yes_bid_dollars": "0.42",
        "yes_bid_size_fp": "7.00",
        "no_bid_dollars": "0.54",
        "no_bid_size_fp": "8.00",
    })
    assert book is not None
    assert book.yes_ask == Decimal("0.44")
    assert book.yes_depth == Decimal("12.00")
    assert book.no_ask == Decimal("0.58")
    assert book.no_depth == Decimal("9.00")


def test_kalshi_nested_top_book_can_derive_asks_from_opposite_bids():
    book = kalshi_nested_top_book({
        "yes_bid_dollars": "0.41",
        "yes_bid_size_fp": "5.00",
        "no_bid_dollars": "0.55",
        "no_bid_size_fp": "6.00",
    })
    assert book is not None
    assert book.yes_ask == Decimal("0.45")
    assert book.yes_depth == Decimal("6.00")
    assert book.no_ask == Decimal("0.59")
    assert book.no_depth == Decimal("5.00")


def test_kalshi_nested_top_book_fails_closed_without_executable_depth():
    assert kalshi_nested_top_book({"yes_ask_dollars": "0.44", "no_ask_dollars": "0.58"}) is None
