from decimal import Decimal as D

from src.gemini_prediction_depth import parse_depth_top


def test_depth10_native_yes_book_maps_buy_no_to_best_yes_bid():
    top = parse_depth_top(
        "GEMI-X",
        {
            "bids": [["0.42", "3"], ["0.41", "9"]],
            "asks": [["0.45", "4"], ["0.46", "8"]],
        },
        received_ms=123,
    )
    assert top is not None
    assert top.yes_bid == D("0.42")
    assert top.yes_ask == D("0.45")
    assert top.yes_bid_size == D("3")
    assert top.yes_ask_size == D("4")
    assert top.no_ask == D("0.58")
    assert top.no_ask_size == D("3")
    assert top.received_ms == 123


def test_crossed_or_one_sided_book_fails_closed():
    assert parse_depth_top("GEMI-X", {"bids": [["0.50", "1"]], "asks": []}) is None
    assert parse_depth_top("GEMI-X", {"bids": [["0.51", "1"]], "asks": [["0.50", "1"]]}) is None
