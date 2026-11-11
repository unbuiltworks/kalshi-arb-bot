"""The seam between the authenticated trade tail and the frozen classifier.

The one error that would matter here is a mis-read aggressor: it turns
non-interaction into TRADE_AT_PRICE and fabricates exactly the evidence the
run exists to find. So the `dir` vocabulary is never guessed, and these tests
pin that it is not.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_authenticated_evidence as E
from src import pmus_interaction_classifier as clf


def test_an_unrecognised_dir_leaves_the_aggressor_unset():
    """Not 'assume yes'. Unset makes the classifier fall through to
    QUEUE_AMBIGUOUS instead of inventing an interaction."""
    n = E.normalize_print({"t": 1, "px": "0.22", "qty": "10", "dir": "buy"})
    assert "taker_side" not in n
    assert n["raw_dir"] == "buy"


def test_a_recognised_dir_maps_and_is_recorded_raw():
    n = E.normalize_print({"t": 1, "px": "0.22", "qty": "10", "dir": "no"})
    assert n["taker_side"] == "no" and n["raw_dir"] == "no"


def test_buy_and_sell_are_deliberately_not_in_the_vocabulary():
    """'buy' does not say buy-of-what, and the wrong reading flips every
    interaction."""
    assert "buy" not in E.DIR_TO_TAKER and "sell" not in E.DIR_TO_TAKER


def test_the_vocabulary_audit_reports_what_actually_appeared():
    a = E.audit_dir_vocabulary([{"dir": "no"}, {"dir": "yes"}, {"dir": "?"}])
    assert a["n"] == 3 and a["mapped"] == 2 and a["unmapped"] == 1
    assert a["verdict"] == "PARTIAL"
    assert E.audit_dir_vocabulary([{"dir": "no"}])["verdict"] == "USABLE"
    assert E.audit_dir_vocabulary([{"dir": "x"}])["verdict"] == "UNREADABLE"


def test_a_row_without_a_price_is_dropped_not_defaulted():
    assert E.normalize_print({"t": 1, "qty": "10", "dir": "no"}) is None


def test_prints_are_bounded_to_the_interval_and_sorted():
    rows = [{"t": 30, "px": "0.2", "dir": "no"},
            {"t": 10, "px": "0.3", "dir": "no"},
            {"t": 99, "px": "0.4", "dir": "no"}]
    got = E.prints_for_interval(rows, 5, 40)
    assert [r["_epoch"] for r in got] == [10, 30]


def test_an_unreadable_aggressor_cannot_produce_an_interaction_class():
    """End to end: the same print classifies as interaction with a readable
    dir and falls back without one."""
    d = {"action": "QUOTE", "desired_price": "0.22", "side": "UP",
         "t_utc_epoch": 0.0, "book": {"bid_shares": "10"}}
    readable = E.classify_decisions(
        [d], [{"t": 1, "px": "0.22", "qty": "500", "dir": "no"}])
    assert readable[0]["klass"] in (clf.TRADE_AT_PRICE, clf.FILL_PLAUSIBLE)
    unreadable = E.classify_decisions(
        [d], [{"t": 1, "px": "0.22", "qty": "500", "dir": "buy"}])
    assert unreadable[0]["klass"] == clf.QUEUE_AMBIGUOUS


def test_no_classification_is_ever_a_fill():
    d = {"action": "QUOTE", "desired_price": "0.22", "side": "UP",
         "t_utc_epoch": 0.0, "book": {"bid_shares": "10"}}
    rows = E.classify_decisions(
        [d], [{"t": 1, "px": "0.20", "qty": "9000", "dir": "no"}])
    assert rows[0]["is_fill"] is False
    assert rows[0]["fill_evidence_source"] is None


def test_the_sequence_stops_where_the_evidence_stops():
    none = E.sequence_metrics([])
    assert none["sequence_reached"] == "none"
    one = E.sequence_metrics([
        {"klass": clf.TRADE_AT_PRICE, "side": "UP", "t_utc_epoch": 10.0}])
    assert one["sequence_reached"] == "first_side_only"
    assert one["opposite_side_opportunity"] is None


def test_the_opposite_side_and_its_latency_are_measured():
    rows = [
        {"klass": clf.TRADE_AT_PRICE, "side": "UP", "t_utc_epoch": 10.0},
        {"klass": clf.TRADE_AT_PRICE, "side": "UP", "t_utc_epoch": 40.0},
        {"klass": clf.TRADE_THROUGH_PRICE, "side": "DOWN", "t_utc_epoch": 70.0},
    ]
    s = E.sequence_metrics(rows)
    assert s["sequence_reached"] == "opposite_side_observed"
    assert s["repeated_same_side_before_opposite"] == 1
    assert s["latency_first_to_opposite_s"] == 60.0


def test_queue_ambiguous_rows_never_count_as_interaction():
    s = E.sequence_metrics([
        {"klass": clf.QUEUE_AMBIGUOUS, "side": "UP", "t_utc_epoch": 1.0},
        {"klass": clf.NO_INTERACTION, "side": "DOWN", "t_utc_epoch": 2.0}])
    assert s["observed_interactions"] == 0
    assert s["sequence_reached"] == "none"


def test_the_classifier_definitions_were_not_edited_by_this_module():
    """They were fixed before any authenticated row existed and stay fixed."""
    assert clf.CLASSES == (clf.NO_INTERACTION, clf.TRADE_AT_PRICE,
                           clf.TRADE_THROUGH_PRICE, clf.QUEUE_AMBIGUOUS,
                           clf.FILL_PLAUSIBLE)
    assert clf.NEEDS_TRADE_TAPE == (clf.TRADE_AT_PRICE,
                                    clf.TRADE_THROUGH_PRICE,
                                    clf.FILL_PLAUSIBLE)
