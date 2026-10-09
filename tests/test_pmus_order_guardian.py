"""The R6 incident, as tests.

Each test below is a failure mode that actually happened or that the brief
names. `test_the_r6_incident_*` reproduce 2026-09-29 18:17:27 exactly: a
process that exits with an order resting, which then fills unsupervised.
"""
import json
import signal

import pytest

from src import pmus_order_guardian as G


def store(tmp_path, name="own.json"):
    return G.OwnershipStore(str(tmp_path / name))


# ----------------------------------------------------------- ownership ---
def test_intent_is_durable_before_any_order_exists(tmp_path):
    """The R6 hole: an order whose ack we never saw must still be known."""
    s = store(tmp_path)
    s.intend("c1", market="m", side="DOWN", price="0.69", qty="10")
    reopened = G.OwnershipStore(str(tmp_path / "own.json"))
    assert "c1" in reopened.orders
    assert reopened.orders["c1"]["state"] == "INTENT"
    assert reopened.may_submit()["may_submit"] is False


def test_an_unreadable_snapshot_is_not_an_empty_one(tmp_path):
    p = tmp_path / "own.json"
    p.write_text('{"schema": "something-else", "orders": {}}')
    s = G.OwnershipStore(str(p))
    assert "SNAPSHOT_SCHEMA_UNKNOWN" in s.uncertain
    assert s.may_submit()["may_submit"] is False


def test_a_fill_is_permanent_and_a_shrinking_read_is_distrusted(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.observe("c1", "ORDER_STATE_NEW", "10")
    s.observe("c1", "ORDER_STATE_NEW", "4")
    assert s.orders["c1"]["filled_qty"] == "10"
    assert any("FILL_QTY_DECREASED" in u for u in s.uncertain)


def test_book_fill_is_idempotent(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.observe("c1", "ORDER_STATE_FILLED", "10")
    assert s.book_fill("c1") is not None
    assert s.book_fill("c1") is None          # second call books nothing


def test_intend_never_overwrites_a_filled_order(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.observe("c1", "ORDER_STATE_FILLED", "10")
    s.intend("c1", price="0.50")
    assert s.orders["c1"]["state"] == "ORDER_STATE_FILLED"
    assert s.orders["c1"]["filled_qty"] == "10"


# ---------------------------------------------------- controlled shutdown ---
def test_cancel_verifies_and_does_not_trust_the_call(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")
    calls = []
    out = G.cancel_outstanding(
        s, cancel=lambda oid: calls.append(oid),
        read_order=lambda oid: {"state": "ORDER_STATE_CANCELLED",
                                "filled_qty": 0})
    assert calls == ["V1"]
    assert out["results"][0]["verdict"] == "TERMINAL:ORDER_STATE_CANCELLED"
    assert s.may_submit()["may_submit"] is True


def test_cancel_failed_against_an_already_filled_order_is_not_an_error(
        tmp_path):
    """R4's sweep reported CANCEL_FAILED and was CORRECT: the order had
    filled. Only the re-read distinguishes that from a real failure."""
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")

    def cancel(_oid):
        raise RuntimeError("CANCEL_FAILED")

    out = G.cancel_outstanding(
        s, cancel=cancel,
        read_order=lambda oid: {"state": "ORDER_STATE_FILLED",
                                "filled_qty": 10})
    r = out["results"][0]
    assert r["cancel_call"].startswith("RAISED")
    assert r["verdict"] == "FILLED_NOT_CANCELLED"
    assert s.orders["c1"]["filled_qty"] == "10"


def test_an_order_still_open_after_cancel_marks_uncertainty(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")
    out = G.cancel_outstanding(
        s, cancel=lambda oid: None,
        read_order=lambda oid: {"state": "ORDER_STATE_NEW",
                                "filled_qty": 0})
    assert out["results"][0]["verdict"] == "STILL_OPEN_AFTER_CANCEL"
    assert s.may_submit()["may_submit"] is False


def test_an_unverifiable_cancel_fails_closed(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")

    def read(_oid):
        raise OSError("gateway down")

    out = G.cancel_outstanding(s, cancel=lambda oid: None, read_order=read)
    assert out["results"][0]["verdict"].startswith("UNVERIFIABLE")
    assert any("CANCEL_UNVERIFIABLE" in u for u in s.uncertain)
    assert s.may_submit()["may_submit"] is False


# -------------------------------------------- startup reconciliation path ---
def test_the_r6_incident_late_fill_is_recovered_and_booked_once(tmp_path):
    """2026-09-29 18:17:27: process dies, CSWG9ZB98YCA rests, then FILLS."""
    s = store(tmp_path)
    s.intend("CSWG9ZB98YCA", market="cpc-btc-updown-15m-2026-09-29-1815z",
             side="DOWN", price="0.69", qty="10")
    s.acknowledged("CSWG9ZB98YCA", "CSWG9ZB98YCA")
    # process dies here. A new process starts and reads the venue:
    s2 = G.OwnershipStore(str(tmp_path / "own.json"))
    rep = G.startup_reconcile(
        s2,
        list_open=lambda: [],                       # not open: it went terminal
        read_order=lambda oid: {"state": "ORDER_STATE_FILLED",
                                "filled_qty": 10})
    assert rep["late_terminal"][0]["state"] == "ORDER_STATE_FILLED"
    assert rep["late_fills_booked"] == ["CSWG9ZB98YCA"]
    # running recovery again books nothing more
    rep2 = G.startup_reconcile(
        s2, list_open=lambda: [],
        read_order=lambda oid: {"state": "ORDER_STATE_FILLED",
                                "filled_qty": 10})
    assert rep2["late_fills_booked"] == []


def test_the_r5_orphan_that_expired_by_itself_is_recovered_clean(tmp_path):
    """R5's CSPERKXEYYB5 went ORDER_STATE_EXPIRED at window end, 0 filled."""
    s = store(tmp_path)
    s.intend("CSPERKXEYYB5", side="UP", price="0.46", qty="10")
    s.acknowledged("CSPERKXEYYB5", "CSPERKXEYYB5")
    rep = G.startup_reconcile(
        s, list_open=lambda: [],
        read_order=lambda oid: {"state": "ORDER_STATE_EXPIRED",
                                "filled_qty": 0})
    assert rep["late_terminal"][0]["state"] == "ORDER_STATE_EXPIRED"
    assert rep["late_fills_booked"] == []
    assert rep["may_submit"]["may_submit"] is True


def test_an_order_still_live_is_adopted_not_resubmitted(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")
    rep = G.startup_reconcile(
        s, list_open=lambda: [{"id": "V1"}],
        read_order=lambda oid: pytest.fail("must not re-read a live order"))
    assert rep["adopted"] == ["c1"]
    assert rep["may_submit"]["may_submit"] is False


def test_an_unknown_live_order_at_the_venue_blocks_submission(tmp_path):
    """An order we have no record of is an unbounded obligation."""
    s = store(tmp_path)
    rep = G.startup_reconcile(
        s, list_open=lambda: [{"id": "STRANGER"}],
        read_order=lambda oid: {"state": "ORDER_STATE_NEW", "filled_qty": 0})
    assert rep["venue_only"] == ["STRANGER"]
    assert rep["may_submit"]["may_submit"] is False


def test_unreadable_open_orders_fails_closed(tmp_path):
    s = store(tmp_path)

    def boom():
        raise OSError("500")

    rep = G.startup_reconcile(s, list_open=boom,
                              read_order=lambda oid: {})
    assert rep["may_submit"]["may_submit"] is False
    assert any("OPEN_ORDERS_UNREADABLE" in u for u in s.uncertain)


def test_an_order_neither_open_nor_terminal_is_uncertain(tmp_path):
    s = store(tmp_path)
    s.intend("c1")
    s.acknowledged("c1", "V1")
    rep = G.startup_reconcile(
        s, list_open=lambda: [],
        read_order=lambda oid: {"state": "ORDER_STATE_NEW",
                                "filled_qty": 0})
    assert rep["may_submit"]["may_submit"] is False
    assert any("NEITHER_OPEN_NOR_TERMINAL" in u for u in s.uncertain)


def test_uncertainty_does_not_decay_on_its_own(tmp_path):
    s = store(tmp_path)
    s.mark_uncertain("X")
    reopened = G.OwnershipStore(str(tmp_path / "own.json"))
    assert reopened.may_submit()["may_submit"] is False
    reopened.clear_uncertainty("X")
    assert reopened.may_submit()["may_submit"] is True


# -------------------------------------------------- honesty about signals ---
def test_signal_handlers_cover_sigterm_and_say_what_they_do_not(tmp_path):
    seen = []
    rep = G.install_signal_handlers(lambda n: seen.append(n),
                                    signals=(signal.SIGTERM,))
    assert "SIGTERM" in rep["wired"]
    assert "SIGKILL" in rep["NOT_COVERED"]
    assert rep["atexit_used"] is False
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def test_available_protections_never_claims_hard_crash_coverage():
    p = G.available_protections()
    assert p["SIGKILL_OOM_POWER_LOSS"] == "NOTHING IN-PROCESS RUNS"
    assert p["atexit_used"] is False
    assert len(p["out_of_process_protections"]) >= 4
