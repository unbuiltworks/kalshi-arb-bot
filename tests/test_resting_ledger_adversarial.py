"""Adversarial restart tests for the durable resting-order ledger.

THE FAILURE BEING PINNED. A process places, partially fills, re-rests, then
dies. Before this ledger the working set lived only in process memory, so the
restart came back believing it was flat and could place again on top of
exposure it still owed. Every test below drives a sequence to a crash and then
re-opens the ledger FROM DISK, exactly as a restart would.

`reopen()` is the whole point: no test asserts against the in-memory object
that wrote the rows.
"""
import json
import os
from decimal import Decimal as D

import pytest

from src import pmus_resting_ledger as R


@pytest.fixture()
def path(tmp_path):
    return str(tmp_path / "resting.jsonl")


def reopen(p):
    """What a restart sees: the ledger rebuilt from disk alone."""
    return R.RestingOrderLedger(p, session_id="restarted")


def crash_sequence(p):
    """rest -> partial -> second rest -> first fills further -> ambiguous
    cancel -> opposite-side partial -> process dies."""
    led = R.RestingOrderLedger(p, session_id="original")
    a = led.record_place_intent(leg="YES", price="0.40", qty="1")
    led.record_place_result(a, R.WORKING, venue_order_id="V-A")
    led.record_fill(a, "0.3")                       # partial
    b = led.record_place_intent(leg="YES", price="0.41", qty="1")
    led.record_place_result(b, R.WORKING, venue_order_id="V-B")
    led.record_fill(a, "0.2")                       # first fills further
    led.record_cancel_intent(a)
    led.record_cancel_result(a, "TIMEOUT")          # ambiguous cancel
    c = led.record_place_intent(leg="NO", price="0.55", qty="1")
    led.record_place_result(c, R.WORKING, venue_order_id="V-C")
    led.record_fill(c, "0.4")                       # opposite-side partial
    return a, b, c                                  # process dies here


# ----------------------------------------------------------------- the core
def test_after_restart_no_new_place_can_occur(path):
    crash_sequence(path)
    led = reopen(path)
    v = R.admit_place(led, leg="YES", price="0.40", qty="1")
    assert v["admit"] is False
    assert "UNCERTAIN" in v["reason"]


def test_unresolved_orders_remain_represented_after_restart(path):
    a, b, c = crash_sequence(path)
    led = reopen(path)
    o = led.orders()
    assert set(o) == {a, b, c}
    assert o[a]["state"] == R.UNCERTAIN          # the ambiguous cancel
    assert o[b]["state"] == R.WORKING
    assert o[c]["state"] == R.PARTIAL
    assert {e["client_order_id"] for e in led.uncertain()} == {a}


def test_committed_exposure_cannot_be_forgotten(path):
    crash_sequence(path)
    led = reopen(path)
    # a: 1 - 0.5 filled = 0.5 remaining @0.40; b: 1 @0.41; c: 0.6 @0.55
    assert led.committed_usd() == D("0.40") * D("0.5") + D("0.41") + D("0.55") * D("0.6")
    assert led.working_orders() == 3


def test_partial_fill_arithmetic_survives_restart(path):
    a, _, c = crash_sequence(path)
    o = reopen(path).orders()
    assert o[a]["filled_qty"] == D("0.5") and o[a]["remaining_qty"] == D("0.5")
    assert o[c]["filled_qty"] == D("0.4") and o[c]["remaining_qty"] == D("0.6")


def test_a_confirmed_cancel_releases_only_the_unfilled_part(path):
    led = R.RestingOrderLedger(path)
    a = led.record_place_intent(leg="YES", price="0.40", qty="1")
    led.record_place_result(a, R.WORKING, venue_order_id="V-A")
    led.record_fill(a, "0.25")
    led.record_cancel_result(a, R.CANCELLED)
    o = reopen(path).orders()[a]
    assert o["remaining_qty"] == D("0") and o["filled_qty"] == D("0.25")
    assert o["state"] == R.FILLED                # partially filled, then closed
    assert o["committed_usd"] == D("0")          # no live exposure remains


def test_cleanup_cannot_accidentally_create_additional_exposure(path):
    """Cancelling everything must never raise committed exposure."""
    a, b, c = crash_sequence(path)
    led = reopen(path)
    before = led.committed_usd()
    for coid in (a, b, c):
        led.record_cancel_intent(coid)
        led.record_cancel_result(coid, R.CANCELLED)
    after = reopen(path).committed_usd()
    assert after == D("0") and after <= before


# ------------------------------------------------------- preflight refusals
def test_venue_and_store_disagreement_cannot_arm(path):
    crash_sequence(path)
    led = reopen(path)
    pf = R.preflight(led, [{"orderId": "V-Z"}])      # a venue order we never wrote
    assert pf["may_arm"] is False
    assert any("DISAGREEMENT" in r for r in pf["reasons"])


def test_unreadable_venue_cannot_arm(path):
    led = R.RestingOrderLedger(path)                 # nothing placed at all
    assert R.preflight(led, [])["may_arm"] is True
    pf = R.preflight(led, None)
    assert pf["may_arm"] is False and pf["verdict"] == R.UNREADABLE


def test_unreadable_store_cannot_arm(path, monkeypatch):
    crash_sequence(path)
    led = reopen(path)
    led.readable = False                             # simulate an unreadable store
    led._err = "EIO"
    pf = R.preflight(led, [])
    assert pf["may_arm"] is False and pf["verdict"] == R.UNREADABLE
    assert R.admit_place(led, leg="YES", price="0.40", qty="1")["admit"] is False


def test_either_source_showing_exposure_refuses(path):
    led = R.RestingOrderLedger(path)                 # store empty
    pf = R.preflight(led, [{"orderId": "V-Q"}])      # venue is not
    assert pf["may_arm"] is False


def test_a_torn_final_line_becomes_exposure_not_a_skipped_row(path):
    """The line that failed to finish writing is the one we may still owe."""
    led = R.RestingOrderLedger(path)
    a = led.record_place_intent(leg="YES", price="0.40", qty="1")
    led.record_place_result(a, R.WORKING, venue_order_id="V-A")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"kind": "PLACE_INTENT", "client_order_id": "q1-tr')  # torn
    led2 = reopen(path)
    assert led2.torn == 1
    assert any(e["client_order_id"].startswith("TORN-") for e in led2.uncertain())
    assert R.admit_place(led2, leg="YES", price="0.40", qty="1")["admit"] is False
    # and it charges the full envelope rather than nothing
    assert led2.committed_usd() >= R.MAX_COMMITTED_USD


# ------------------------------------------- the persistence/transmission seam
def test_intent_is_on_disk_before_the_caller_can_transmit(path):
    """The crash window between 'venue accepted' and 'ledger recorded'.

    The intent must already be durable when record_place_intent RETURNS, since
    the very next instruction is the send. Read the raw file, not the object.
    """
    led = R.RestingOrderLedger(path)
    coid = led.record_place_intent(leg="YES", price="0.40", qty="1")
    rows = [json.loads(l) for l in open(path)]
    assert rows and rows[-1]["kind"] == R.PLACE_INTENT
    assert rows[-1]["client_order_id"] == coid
    assert rows[-1]["notional"] == "0.40"


def test_an_intent_whose_result_never_arrived_blocks_on_restart(path):
    """Crash exactly between transmit and ack: the worst case."""
    led = R.RestingOrderLedger(path)
    led.record_place_intent(leg="YES", price="0.40", qty="1")
    led2 = reopen(path)
    assert led2.orders()[list(led2.orders())[0]]["state"] == R.INTENT
    assert led2.working_orders() == 1
    assert R.preflight(led2, [])["may_arm"] is False
    assert R.admit_place(led2, leg="YES", price="0.40", qty="1")["admit"] is False


def test_an_unrecognised_place_outcome_becomes_uncertain_not_accepted(path):
    led = R.RestingOrderLedger(path)
    a = led.record_place_intent(leg="YES", price="0.40", qty="1")
    led.record_place_result(a, "WEIRD_TRANSPORT_STATE")
    assert reopen(path).orders()[a]["state"] == R.UNCERTAIN


def test_a_fill_arriving_before_the_ack_is_not_lost(path):
    led = R.RestingOrderLedger(path)
    a = led.record_place_intent(leg="YES", price="0.40", qty="1")
    led.record_fill(a, "0.5")                        # fill beats the ack
    led.record_place_result(a, R.WORKING, venue_order_id="V-A")
    o = reopen(path).orders()[a]
    assert o["filled_qty"] == D("0.5") and o["state"] == R.PARTIAL


# ------------------------------------------------------------ envelope caps
def test_budgets_are_computed_from_durable_state_not_process_memory(path):
    led = R.RestingOrderLedger(path)
    a = led.record_place_intent(leg="YES", price="0.40", qty="1")
    led.record_place_result(a, R.WORKING, venue_order_id="V-A")
    fresh = reopen(path)                             # no memory of anything
    v = R.admit_place(fresh, leg="NO", price="0.55", qty="1")
    assert v["computed_from"] == "DURABLE_LEDGER_NOT_PROCESS_MEMORY"
    assert v["working_after"] == 2


def test_max_working_orders_counts_durable_live_orders(path):
    led = R.RestingOrderLedger(path)
    for i, px in enumerate(("0.40", "0.41")):
        c = led.record_place_intent(leg="YES", price=px, qty="1")
        led.record_place_result(c, R.WORKING, venue_order_id="V-%d" % i)
    v = R.admit_place(reopen(path), leg="YES", price="0.42", qty="1")
    assert v["admit"] is False and "max_working_orders" in v["reason"]


def test_max_committed_usd_is_enforced_against_the_durable_total(path):
    led = R.RestingOrderLedger(path)
    c = led.record_place_intent(leg="YES", price="0.80", qty="1")
    led.record_place_result(c, R.WORKING, venue_order_id="V-A")
    v = R.admit_place(reopen(path), leg="NO", price="0.79", qty="1",
                      max_committed=D("1.00"))
    assert v["admit"] is False and "max_committed_usd" in v["reason"]


def test_projected_residual_counts_resting_orders_not_only_fills(path):
    """A cap checked only against settled fills admits an order that, together
    with what is already resting, breaks it."""
    led = R.RestingOrderLedger(path)
    c = led.record_place_intent(leg="YES", price="0.40", qty="1")
    led.record_place_result(c, R.WORKING, venue_order_id="V-A")
    fresh = reopen(path)
    assert fresh.directional_residual_usd() == D("0")      # nothing FILLED yet
    assert fresh.projected_residual_usd("YES", D("1")) == D("2") * D("2.00")
    v = R.admit_place(fresh, leg="YES", price="0.40", qty="1")
    assert v["admit"] is False and "residual" in v["reason"]


def test_q1_explicit_only_refuses_any_other_size(path):
    led = R.RestingOrderLedger(path)
    for bad in ("2", "0.5", "0", None, True):
        assert R.admit_place(led, leg="YES", price="0.40", qty=bad)["admit"] is False
    assert R.admit_place(led, leg="YES", price="0.40", qty="1")["admit"] is True


def test_envelope_constants_are_the_owner_stated_values():
    assert R.MAX_COMMITTED_USD == D("2.50")
    assert R.MAX_DIRECTIONAL_RESIDUAL_USD == D("2.00")
    assert R.EXPLICIT_QTY == D("1")


def test_this_module_authorizes_nothing():
    """No credential, no transport, no order verb."""
    src = open("src/pmus_resting_ledger.py", encoding="utf-8").read()
    for forbidden in ("requests", "urllib", "http", "api_key", "secret",
                      "REQUIRED_AUTH_ID", "AUTHORIZED_SHA", "AUTHORIZED_ENVELOPE"):
        assert forbidden not in src, forbidden
