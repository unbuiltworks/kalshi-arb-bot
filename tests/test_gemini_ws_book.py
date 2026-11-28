"""The maintained Gemini book: fresher than REST, and honest about not knowing.

These pin the distinctions that decide whether a Gemini capacity claim means
anything: a book we are not sure of is None rather than half-built, a trade
list we cannot vouch for is None rather than empty, and a sequence gap
invalidates the book instead of being ignored.
"""
import time

from decimal import Decimal as D

from src.l5_shadow.gemini_ws import GeminiBook


def _initial(bids=(("0.60", "500"),), asks=(("0.66", "1"),), seq=0):
    ev = [{"type": "change", "reason": "initial", "side": "bid",
           "price": p, "remaining": s, "delta": s} for p, s in bids]
    ev += [{"type": "change", "reason": "initial", "side": "ask",
            "price": p, "remaining": s, "delta": s} for p, s in asks]
    return {"type": "update", "socket_sequence": seq, "events": ev,
            "timestampms": 1789479000000}


def _change(side, price, remaining, reason, seq):
    return {"type": "update", "socket_sequence": seq, "timestampms":
            1789479000000, "events": [{"type": "change", "reason": reason,
                                       "side": side, "price": price,
                                       "remaining": remaining,
                                       "delta": remaining}]}


def _ready(b):
    b.connected = True
    return b


def test_a_book_is_none_until_the_initial_snapshot_arrives():
    b = _ready(GeminiBook("S"))
    assert b.snapshot() is None
    b.apply(_initial())
    assert b.snapshot() is not None


def test_a_book_is_none_while_the_socket_is_down():
    b = _ready(GeminiBook("S"))
    b.apply(_initial())
    b.connected = False
    assert b.snapshot() is None, "a disconnected socket must not serve a book"


def test_a_book_with_no_recent_message_is_unknown_not_current():
    b = _ready(GeminiBook("S"))
    b.apply(_initial(), now=time.time() - 3600)
    assert b.snapshot() is None


def test_places_and_cancels_are_applied_to_the_right_side():
    b = _ready(GeminiBook("S"))
    b.apply(_initial())
    b.apply(_change("ask", "0.64", "300", "place", 1))
    snap = b.snapshot()
    assert snap["asks"][0] == {"price": "0.64", "amount": "300"}
    b.apply(_change("ask", "0.64", "0", "cancel", 2))
    assert b.snapshot()["asks"][0]["price"] == "0.66"


def test_the_touch_can_be_one_share_with_real_size_behind_it():
    """The exact shape that produced a `depth 1.0` reading."""
    b = _ready(GeminiBook("S"))
    b.apply(_initial(asks=(("0.66", "1"), ("0.68", "500"))))
    snap = b.snapshot()
    assert snap["asks"][0]["amount"] == "1"
    assert sum(D(a["amount"]) for a in snap["asks"]) == D("501")


def test_a_sequence_gap_invalidates_the_book_rather_than_being_ignored():
    b = _ready(GeminiBook("S"))
    b.apply(_initial())
    b.apply(_change("ask", "0.64", "300", "place", 5))     # skipped 1..4
    assert b.gaps == 1
    assert b.snapshot() is None, "a book missing updates must not be served"


def test_the_book_recovers_from_a_gap_on_the_next_initial():
    b = _ready(GeminiBook("S"))
    b.apply(_initial())
    b.apply(_change("ask", "0.64", "300", "place", 9))
    assert b.snapshot() is None
    b.apply(_initial(seq=10))
    assert b.snapshot() is not None


def test_trades_are_recorded_separately_from_quote_churn():
    b = _ready(GeminiBook("S"))
    b.apply(_initial())
    b.apply({"type": "update", "socket_sequence": 1, "timestampms": 1,
             "events": [{"type": "trade", "price": "0.60", "amount": "25",
                         "makerSide": "bid"}]})
    assert b.counts["trade"] == 1
    # asked from the start of the vouchable interval, not from epoch 0: a
    # window that predates the socket is correctly refused (below).
    got = b.prints_since(b.ready_since)
    assert len(got) == 1 and got[0]["size"] == D("25")
    assert b.prints_since(0) is None


def test_a_trade_list_we_cannot_vouch_for_is_none_not_empty():
    """'We saw nothing' and 'we were not watching' are different facts."""
    b = _ready(GeminiBook("S"))
    b.apply(_initial())                       # ready_since = now
    assert b.prints_since(time.time() - 600) is None
    assert b.prints_since(time.time()) == []


def test_a_gap_makes_the_trade_list_unvouchable_again():
    b = _ready(GeminiBook("S"))
    t0 = time.time()
    b.apply(_initial(), now=t0)
    assert b.prints_since(t0 + 0.1) == []
    b.apply(_change("ask", "0.64", "300", "place", 7))      # gap
    assert b.prints_since(t0 + 0.1) is None


def test_quote_churn_is_counted_so_it_can_be_distinguished_from_trading():
    b = _ready(GeminiBook("S"))
    b.apply(_initial())
    for i in range(3):
        b.apply(_change("ask", "0.39", "1", "place", 1 + i * 2))
        b.apply(_change("ask", "0.39", "0", "cancel", 2 + i * 2))
    c = b.snapshot()["counts"]
    assert c["place"] == 3 and c["cancel"] == 3 and c["trade"] == 0


def test_the_snapshot_reports_its_own_age():
    b = _ready(GeminiBook("S"))
    t0 = time.time()
    b.apply(_initial(), now=t0 - 2.0)
    assert b.snapshot()["age_s"] >= 2.0
