"""The evidence Probe B could not produce: what placement did to each price.

Probe B submitted every order at exactly the book bid and its artifact carried
zero `native_price`/`improved` markers, because the decision capture is taken
BEFORE `apply_A` runs. So it could not distinguish "the rule declined" from
"the rule moved us from one tick behind the touch TO the touch" -- which is
the entire question the arm existed to answer.

These tests pin the capture and, just as importantly, pin that it changes
NOTHING: same prices, same actions, same budget.
"""
import json

from decimal import Decimal as D

from src.gemini_execution import ab_arms as AB
from src.gemini_execution import ws_continuous as WC

BOOK = {"yes": {"bid": "0.25", "ask": "0.57"},
        "no": {"bid": "0.43", "ask": "0.75"}}
ST = {"inventory": {"yes_qty": "1", "no_qty": "0", "matched_qty": "0",
                    "directional_residual": "1"}}


class _Rec(WC.Driver):
    """Just the recorder, without constructing a live driver."""
    def __init__(self):
        self._placement = "maker_improve"
        self._placement_log = []
        self.budget_consumed = 3

    def budget_left(self):
        return 9


def _capture(actions, book=BOOK):
    r = _Rec()
    placed = AB.apply_A(actions, book)
    r._record_placement([dict(a) for a in actions], placed, book, ST)
    return r._placement_log, placed


def test_it_records_the_transformation_that_actually_happened():
    rows, placed = _capture([{"leg": "YES", "action": "OPEN",
                              "price": "0.25", "qty": "1"}])
    assert len(rows) == 1
    r = rows[0]
    assert r["native_price"] == "0.25"
    assert r["submitted_price"] == "0.26", "one tick better than the bid"
    assert r["improved"] is True
    assert r["bid"] == "0.25" and r["ask"] == "0.57"
    assert r["from_bid"] == "0.01"
    assert r["from_ask"] == "-0.31"
    assert r["leg"] == "YES" and r["action"] == "OPEN"


def test_a_declined_improvement_is_distinguishable_from_no_rule():
    """THE PROBE B AMBIGUITY, closed. Equal prices plus improved=False is a
    RECORDED refusal; the absence of a row entirely is the rule not running."""
    # one tick better would reach the ask -> the rule declines
    book = {"yes": {"bid": "0.25", "ask": "0.26"}, "no": {"bid": "0.4", "ask": "0.9"}}
    rows, placed = _capture([{"leg": "YES", "action": "OPEN",
                              "price": "0.25", "qty": "1"}], book)
    r = rows[0]
    assert r["improved"] is False
    assert r["native_price"] == r["submitted_price"] == "0.25"
    assert str(placed[0]["price"]) == "0.25", "a declined improvement leaves the price alone"


def test_it_carries_inventory_budget_and_side():
    rows, _ = _capture([{"leg": "NO", "action": "COMPLETE",
                         "price": "0.43", "qty": "1"}])
    r = rows[0]
    assert r["leg"] == "NO" and r["action"] == "COMPLETE"
    assert r["inventory"]["yes_qty"] == "1"
    assert r["inventory"]["directional_residual"] == "1"
    assert r["budget_consumed_before"] == 3
    assert r["budget_left_before"] == 9
    assert r["placement"] == "maker_improve"


def test_the_capture_changes_no_price_and_no_action():
    """It copies what already happened. If it can move a price it is a bug."""
    acts = [{"leg": "YES", "action": "OPEN", "price": "0.25", "qty": "1"},
            {"leg": "NO", "action": "COMPLETE", "price": "0.43", "qty": "1"}]
    before = json.dumps(AB.apply_A(acts, BOOK), sort_keys=True, default=str)
    rows, placed = _capture(acts)
    after = json.dumps(placed, sort_keys=True, default=str)
    assert before == after
    assert len(rows) == 2


def test_it_flushes_into_the_window_report_and_clears():
    r = _Rec()
    r._placement_log = [{"leg": "YES"}]
    rep = {}
    WC.Driver._drain_events(r, None, rep)
    assert rep["placement_capture"] == [{"leg": "YES"}]
    assert r._placement_log == [], "a flushed row must not be recorded twice"
    WC.Driver._drain_events(r, None, rep)
    assert len(rep["placement_capture"]) == 1
