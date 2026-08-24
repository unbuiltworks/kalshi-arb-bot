from decimal import Decimal

from src import gemini_same_venue_crypto_maker_q1_depth as d

D = Decimal


def test_depth_snapshot_preserves_one_sided_bid_for_no_hedge():
    top = d._snapshot_top(
        "GEMI-XRP2608312100-HI0D8",
        {"bids": [["0.97", "515"], ["0.96", "12"]], "asks": []},
        1234,
    )
    assert top.ts_ms == 1234
    assert top.yes_bid == D("0.97")
    assert top.yes_bid_size == D("515")
    assert top.yes_ask == D("1")
    assert top.no_ask == D("0.03")
    assert top.no_ask_size == D("515")


def test_depth_snapshot_selects_best_yes_bid_and_ask():
    top = d._snapshot_top(
        "X", {"bids": [["0.40", "2"], ["0.41", "3"]], "asks": [["0.60", "4"], ["0.59", "5"]]}, 1
    )
    assert top.yes_bid == D("0.41")
    assert top.yes_bid_size == D("3")
    assert top.yes_ask == D("0.59")
    assert top.yes_ask_size == D("5")
