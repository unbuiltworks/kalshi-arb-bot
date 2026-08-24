from decimal import Decimal

from src import gemini_same_venue_crypto_maker_q1_rest as r

D = Decimal


def test_rest_book_to_top_preserves_yes_and_transformed_no_depth():
    top = r._to_top(
        "GEMI-XRP2608312100-HI0D8",
        {
            "bids": [{"price": "0.97", "amount": "515"}],
            "asks": [],
        },
        123,
    )
    assert top.yes_bid == D("0.97")
    assert top.yes_bid_size == D("515")
    assert top.yes_ask == D("1")
    assert top.no_ask == D("0.03")
    assert top.no_ask_size == D("515")


def test_empty_rest_book_fails_closed_without_fake_depth():
    top = r._to_top("X", {"bids": [], "asks": []}, 123)
    assert top.yes_bid == D("0")
    assert top.yes_bid_size == D("0")
    assert top.yes_ask == D("1")
    assert top.yes_ask_size == D("0")
    assert top.no_ask == D("1")
    assert top.no_ask_size == D("0")
