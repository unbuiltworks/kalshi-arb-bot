"""Failure injection against the mandatory gate.

Every scenario the brief names: a crash between intent persistence and venue
acknowledgement, a fill arriving after process exit, repeated recovery, API
errors, and ambiguous venue responses.
"""
import signal

import pytest

from src import pmus_guardian_gate as GATE
from src import pmus_order_guardian as G


def store(tmp_path, name="own.json"):
    return G.OwnershipStore(str(tmp_path / name))


def test_a_clean_start_opens_for_business(tmp_path):
    rep = GATE.open_for_business(
        store(tmp_path), list_open=lambda: [],
        read_order=lambda oid: {}, list_recent=lambda: [])
    assert rep["may_submit"] is True
    GATE.require_open_for_business(rep)          # does not raise


def test_submission_is_refused_while_an_order_is_outstanding(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")
    rep = GATE.open_for_business(s, list_open=lambda: [{"id": "V1"}],
                                 read_order=lambda oid: {},
                                 list_recent=lambda: [])
    assert rep["may_submit"] is False
    with pytest.raises(GATE.SubmissionBlocked):
        GATE.require_open_for_business(rep)


def test_the_r6_late_fill_is_booked_before_business_reopens(tmp_path):
    """A fill that arrived after the process exited must be booked, and the
    gate must then reopen -- the order is terminal and nothing is owed."""
    s = store(tmp_path)
    s.intend("CSWG9ZB98YCA", market="cpc-btc-updown-15m-2026-09-29-1815z",
             side="DOWN", price="0.69", qty="10")
    s.acknowledged("CSWG9ZB98YCA", "CSWG9ZB98YCA")
    s2 = G.OwnershipStore(str(tmp_path / "own.json"))
    rep = GATE.open_for_business(
        s2, list_open=lambda: [],
        read_order=lambda oid: {"state": "ORDER_STATE_FILLED",
                                "filled_qty": 10},
        list_recent=lambda: [])
    assert rep["reconcile"]["late_fills_booked"] == ["CSWG9ZB98YCA"]
    assert rep["may_submit"] is True


def test_crash_between_intent_and_ack_blocks_until_bound(tmp_path):
    """The venue issues the id, so an intent whose ack was lost has no key.
    Submission stays blocked until it is matched or explained."""
    s = store(tmp_path)
    s.intend("local-1", market="M", side="DOWN", price="0.69", qty="10",
             submitted_t=1000.0)
    blocked = GATE.open_for_business(s, list_open=lambda: [],
                                     read_order=lambda oid: {},
                                     list_recent=lambda: [])
    assert blocked["may_submit"] is False
    assert blocked["unbound"]["unmatched"] == ["local-1"]

    # a later run finds the venue order and binds it, then reads it terminal
    s2 = G.OwnershipStore(str(tmp_path / "own.json"))
    s2.clear_uncertainty("UNMATCHED_INTENT:local-1")
    ok = GATE.open_for_business(
        s2,
        list_recent=lambda: [{"id": "V9", "market": "M", "side": "DOWN",
                              "price": "0.69", "quantity": "10",
                              "created_t": 1001.0}],
        list_open=lambda: [],
        read_order=lambda oid: {"state": "ORDER_STATE_CANCELLED",
                                "filled_qty": 0})
    assert ok["unbound"]["bound"][0]["venue_order_id"] == "V9"
    assert ok["may_submit"] is True


def test_repeated_recovery_is_idempotent_and_stays_open(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")
    kw = dict(list_open=lambda: [],
              read_order=lambda oid: {"state": "ORDER_STATE_FILLED",
                                      "filled_qty": 5},
              list_recent=lambda: [])
    first = GATE.open_for_business(s, **kw)
    second = GATE.open_for_business(s, **kw)
    third = GATE.open_for_business(s, **kw)
    assert first["reconcile"]["late_fills_booked"] == ["c1"]
    assert second["reconcile"]["late_fills_booked"] == []
    assert third["reconcile"]["late_fills_booked"] == []
    assert s.orders["c1"]["filled_qty"] == "5"


def test_an_api_error_reading_open_orders_blocks_submission(tmp_path):
    def boom():
        raise OSError("502 from gateway")

    rep = GATE.open_for_business(store(tmp_path), list_open=boom,
                                 read_order=lambda oid: {},
                                 list_recent=lambda: [])
    assert rep["may_submit"] is False
    with pytest.raises(GATE.SubmissionBlocked):
        GATE.require_open_for_business(rep)


def test_an_ambiguous_venue_response_blocks_submission(tmp_path):
    """Two identical candidate orders: we cannot say which is ours."""
    s = store(tmp_path)
    s.intend("local-1", market="M", side="UP", price="0.50", qty="10",
             submitted_t=1000.0)
    twin = {"market": "M", "side": "UP", "price": "0.50", "quantity": "10",
            "created_t": 1000.5}
    rep = GATE.open_for_business(
        s, list_recent=lambda: [{**twin, "id": "A"}, {**twin, "id": "B"}],
        list_open=lambda: [], read_order=lambda oid: {})
    assert rep["unbound"]["ambiguous"] == ["local-1"]
    assert rep["may_submit"] is False


def test_shutdown_handler_cancels_verifies_and_reports(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")
    seen = []
    rep = GATE.arm_shutdown(
        s, cancel=lambda oid: None,
        read_order=lambda oid: {"state": "ORDER_STATE_CANCELLED",
                                "filled_qty": 0},
        on_report=seen.append)
    assert "SIGTERM" in rep["wired"]
    assert rep["graceful_only"] is True
    assert "SIGKILL" in rep["NOT_COVERED"]
    # fire it the way the orchestrator would
    import os
    os.kill(os.getpid(), signal.SIGTERM)
    assert seen and seen[0]["attempted"] == 1
    assert seen[0]["results"][0]["verdict"].startswith("TERMINAL")
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def test_a_shutdown_sweep_that_itself_fails_marks_uncertainty(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")
    seen = []

    def read(_oid):
        raise OSError("down")

    GATE.arm_shutdown(s, cancel=lambda oid: None, read_order=read,
                      on_report=seen.append)
    import os
    os.kill(os.getpid(), signal.SIGTERM)
    assert s.may_submit()["may_submit"] is False
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
