from decimal import Decimal

from src import gemini_same_venue_crypto_maker_q1_atomic as a

D = Decimal


def test_atomic_top_preserves_transformed_no_depth():
    top = a._to_top("H", {"bids": [{"price": "0.97", "amount": "515"}], "asks": []}, 1000)
    assert top.ts_ms == 1000
    assert top.yes_bid == D("0.97")
    assert top.yes_bid_size == D("515")
    assert top.no_ask == D("0.03")
    assert top.no_ask_size == D("515")


def test_stale_top_has_no_tradeable_depth_and_old_timestamp():
    top = a._stale_top("H", 123)
    assert top.ts_ms == 123
    assert top.yes_bid == D("0")
    assert top.yes_bid_size == D("0")
    assert top.yes_ask == D("1")
    assert top.no_ask == D("1")
    assert top.no_ask_size == D("0")
