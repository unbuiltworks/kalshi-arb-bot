from decimal import Decimal as D

import pytest

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3 as v3


def _book(bid="0.36", bid_qty="10", ask="0.48", ask_qty="11"):
    return {
        "bids": [{"price": bid, "amount": bid_qty}],
        "asks": [{"price": ask, "amount": ask_qty}],
    }


def test_rest_top_parses_prediction_book_objects():
    bid, bid_qty, ask, ask_qty = v3._rest_top(_book())
    assert (bid, bid_qty, ask, ask_qty) == (D("0.36"), D("10"), D("0.48"), D("11"))


def test_rest_top_fails_closed_on_missing_or_crossed_book():
    with pytest.raises(RuntimeError):
        v3._rest_top({"bids": [{"price": "0.36", "amount": "10"}], "asks": []})
    with pytest.raises(RuntimeError):
        v3._rest_top(_book(bid="0.50", ask="0.48"))


def test_entry_window_requires_time_buffer_and_edge_floor():
    needed = inv.MIN_SECONDS_TO_EXPIRY + v3.LIVE_ENTRY_BUFFER_S
    assert v3.entry_window_ready(seconds_to_expiry=needed, modeled_edge=inv.ENTRY_PAIR_EDGE)
    assert not v3.entry_window_ready(seconds_to_expiry=needed - 0.001, modeled_edge=inv.ENTRY_PAIR_EDGE)
    assert not v3.entry_window_ready(seconds_to_expiry=needed, modeled_edge=inv.ENTRY_PAIR_EDGE - D("0.001"))
