"""THE RESERVATION RACE, WITH REAL PROCESSES, AND THE RELEASE RULE.

Everything here exists because the first version of the ledger could not
have passed it. `record_submission` keyed a file by intent id and returned
the existing row if it was already loaded -- so two processes that both
START BEFORE EITHER RESERVES both load zero rows, both decide they may
proceed, and both write. A blocking COUNT is read-then-write, which bounds
nothing under a race, for exactly the reason the attempt latch records for
refusing to be a counter.

So the reservation is now one `O_CREAT|O_EXCL` file: one operation that
exactly one caller can win, however many start at once. These tests fork
real processes and make them start together on a barrier, because an
in-process test of a cross-process lock proves nothing.

The second half is the release rule. `TERMINAL_FILL_UNACCOUNTED` existed in
the first version but could never be reached, because `_fills_accounted`
accepted `derived_net_qty` -- which `read_order_state` computes from the
same per-id `cumQuantity` the fill was read from. "The fill is accounted
because the fill exists" is circular, so the state was decoration. It now
requires an INDEPENDENT representation, and these tests pin that.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from decimal import Decimal as D
from typing import Any, Dict, List

import pytest

from analysis.l5.e1_cohort import obligations as OB
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import UP

SID = "E1-RACE-TEST"


# =====================================================================
# ITEM 2: a true simultaneous two-process reservation race
# =====================================================================

def _worker(state_dir, intent_id, barrier, results):
    """Construct the ledger, WAIT FOR THE OTHER WORKER, then reserve.

    The construction happens before the barrier on purpose: that is the
    dangerous ordering, where both processes have already read the
    directory and found it empty.
    """
    try:
        led = OB.ObligationLedger(state_dir, session_id=SID)
        pre_blocking = len(led.blocking())
        barrier.wait(timeout=30)          # NEITHER has reserved yet
        led.record_submission(intent_id=intent_id, window=0, slot=0,
                              slug="s", side=UP, price="0.49", qty="0.13")
        results.append({"intent": intent_id, "won": True,
                        "pre_blocking": pre_blocking, "pid": os.getpid()})
    except OB.ReservationHeld as held:
        results.append({"intent": intent_id, "won": False,
                        "holder": held.holder.get("intent_id"),
                        "pre_blocking": pre_blocking, "pid": os.getpid()})
    except BaseException as exc:          # noqa: BLE001
        results.append({"intent": intent_id, "won": False,
                        "error": "%s: %s" % (type(exc).__name__, exc)})


@pytest.mark.parametrize("n_workers", [2, 5])
def test_exactly_one_worker_wins_the_first_leg_reservation(tmp_path,
                                                           n_workers):
    """REAL PROCESSES, STARTED TOGETHER. Exactly one reservation succeeds."""
    ctx = mp.get_context("fork")
    sd = str(tmp_path / "e1_cohort")
    os.makedirs(OB.ledger_dir(sd), exist_ok=True)
    with ctx.Manager() as mgr:
        results = mgr.list()
        barrier = mgr.Barrier(n_workers)
        procs = [ctx.Process(target=_worker,
                            args=(sd, "W%d" % i, barrier, results))
                 for i in range(n_workers)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
            assert p.exitcode == 0, (p.name, p.exitcode)
        rows = [dict(r) for r in results]

    assert len(rows) == n_workers, rows
    assert not any("error" in r for r in rows), rows
    # EVERY worker found an empty ledger before the barrier -- the race
    # really was simultaneous, and a count would have let them all through.
    assert all(r["pre_blocking"] == 0 for r in rows), rows
    winners = [r for r in rows if r["won"]]
    losers = [r for r in rows if not r["won"]]
    assert len(winners) == 1, rows
    assert len(losers) == n_workers - 1, rows
    # and every loser names the SAME holder, which is the winner
    assert {r["holder"] for r in losers} == {winners[0]["intent"]}, rows

    # the surviving durable state agrees: one obligation, one reservation
    after = OB.ObligationLedger(sd, session_id=SID)
    assert len(after.blocking()) == 1, after.report()
    assert after.reservation_holder()["intent_id"] == winners[0]["intent"]
    files = [f for f in os.listdir(OB.ledger_dir(sd))
             if f.endswith(".json")]
    assert len(files) == 1, files


def test_the_reservation_is_not_a_counter(tmp_path):
    """THE REASON IT IS A FILE. Two ledgers constructed before either
    writes both see a blocking count of zero; only the O_EXCL create
    separates them."""
    sd = str(tmp_path / "e1_cohort")
    a = OB.ObligationLedger(sd, session_id=SID)
    b = OB.ObligationLedger(sd, session_id=SID)
    assert len(a.blocking()) == len(b.blocking()) == 0
    a.record_submission(intent_id="A", window=0, slot=0, slug="s",
                        side=UP, price="0.49", qty="0.13")
    with pytest.raises(OB.ReservationHeld):
        b.record_submission(intent_id="B", window=0, slot=0, slug="s",
                            side=UP, price="0.49", qty="0.13")
    assert a.report()["RESERVATION_IS_O_EXCL_NOT_A_COUNTER"] is True


def test_re_entering_our_own_reservation_is_idempotent(tmp_path):
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    first = led.reserve_first_leg("A")
    again = led.reserve_first_leg("A")
    assert again["intent_id"] == first["intent_id"] == "A"
    assert len(led.blocking()) == 0        # reserving is not submitting


def test_an_unreadable_reservation_is_held_not_free(tmp_path):
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    led.reserve_first_leg("A")
    with open(led._reservation(), "w") as fh:
        fh.write("{corrupt")
    holder = led.reservation_holder()
    assert holder["unreadable"] is True
    with pytest.raises(OB.ReservationHeld):
        led.record_submission(intent_id="B", window=0, slot=0, slug="s",
                              side=UP, price="0.49", qty="0.13")


# =====================================================================
# ITEM 3: TERMINAL_FILL_UNACCOUNTED cannot become RECONCILED early
# =====================================================================

def _filled(ledger, qty="0.13"):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty=qty)
    ledger.bind_venue_id("I1", "OID-1")
    return {"id": "OID-1", "state": "ORDER_STATE_FILLED", "terminal": True,
            "cum_quantity": qty, "outstanding": False}


@pytest.fixture()
def ledger(tmp_path):
    return OB.ObligationLedger(str(tmp_path / "e1_cohort"), session_id=SID)


def test_our_own_derived_quantity_cannot_account_for_the_fill(ledger):
    """THE CIRCULARITY, CLOSED. `derived_net_qty` comes from the same
    per-id cumQuantity the fill was read from, so it is not evidence that
    anything else knows about the fill."""
    row = _filled(ledger)
    ledger.reconcile({"ok": True, "as_of": 1e12, "orders": [row],
                      # generous, and deliberately the circular field
                      "derived_net_qty": "99",
                      "residual": None,
                      "venue_net_qty": "0",
                      "historical_resolved_filled_qty": "0"})
    assert ledger.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED
    assert len(ledger.blocking()) == 1


@pytest.mark.parametrize("field,value", [
    ("venue_net_qty", "0.13"),
    ("historical_resolved_filled_qty", "0.13"),
])
def test_an_independent_representation_releases(ledger, field, value):
    row = _filled(ledger)
    view = {"ok": True, "as_of": 1e12, "orders": [row], "residual": None,
            "derived_net_qty": "0", "venue_net_qty": "0",
            "historical_resolved_filled_qty": "0"}
    view[field] = value
    rep = ledger.reconcile(view)
    assert rep["released"] == ["I1"], (field, ledger.report())
    assert ledger.rows["I1"]["fills_incorporated"] is True


def test_the_residual_the_gate_reads_releases(ledger):
    """`residual` is what `residual_for_snapshot` publishes as
    `our_open_residual`, which is the field E3 reads. If the gate can see
    it, the gate can block on it."""
    row = _filled(ledger)
    rep = ledger.reconcile({"ok": True, "as_of": 1e12, "orders": [row],
                            "residual": {"qty": "0.13",
                                         "outcome_side": UP},
                            "derived_net_qty": "0"})
    assert rep["released"] == ["I1"]


def test_a_representation_smaller_than_the_fill_does_not_release(ledger):
    """PARTIAL ACCOUNTING IS NOT ACCOUNTING. 0.05 of a 0.13 fill
    represented means 0.08 is unhedged and invisible."""
    row = _filled(ledger, qty="0.13")
    ledger.reconcile({"ok": True, "as_of": 1e12, "orders": [row],
                      "residual": {"qty": "0.05", "outcome_side": UP},
                      "venue_net_qty": "0.05"})
    assert ledger.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED
    assert len(ledger.blocking()) == 1


def test_the_unaccounted_state_is_reachable_and_sticky(ledger):
    """It stays until something accounts for the fill -- it does not time
    out, decay, or resolve by being asked again."""
    row = _filled(ledger)
    bare = {"ok": True, "as_of": 1e12, "orders": [row], "residual": None,
            "venue_net_qty": "0", "historical_resolved_filled_qty": "0"}
    for _ in range(8):
        ledger.reconcile(bare)
        assert ledger.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED
    assert len(ledger.blocking()) == 1
    # and the reservation is still held, so no other worker may proceed
    assert ledger.reservation_holder()["intent_id"] == "I1"


# =====================================================================
# ITEM 4: partials, cancellation races, stale snapshots, restart
# =====================================================================

def test_a_stale_inventory_snapshot_cannot_release(ledger):
    """STALENESS IS A REFUSAL UPSTREAM, and arrives here as `ok: False`.
    `read_order_state` fails the whole view closed past
    MAX_ORDER_STATE_AGE_S, so a stale snapshot advances nothing."""
    row = _filled(ledger)
    stale = {"ok": False, "reason": "ORDER_STATE_STALE:9.1s>5.0s",
             "orders": [row], "residual": {"qty": "0.13"}}
    rep = ledger.reconcile(stale)
    assert rep["view_ok"] is False
    assert rep["released"] == []
    assert ledger.rows["I1"]["state"] == OB.SUBMITTED, (
        "a stale view must not even advance it to OBSERVED")
    assert len(ledger.blocking()) == 1


def test_a_cancellation_race_does_not_release(ledger):
    """ONE ENDPOINT SAYS CANCELLED, THE OTHER SAYS RESTING.
    `read_order_state` returns that as ORDER_STATE_INCONSISTENT with
    `ok: False`, so nothing advances."""
    _filled(ledger)
    rep = ledger.reconcile({
        "ok": False,
        "reason": "ORDER_STATE_INCONSISTENT:OID-1_OPEN_AND_"
                  "ORDER_STATE_CANCELED"})
    assert rep["released"] == []
    assert len(ledger.blocking()) == 1


def test_a_partial_fill_then_cancel_keeps_the_filled_part_blocking(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    # resting, then partial, then cancelled with the partial standing
    ledger.reconcile({"ok": True, "as_of": 1e12, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_PARTIALLY_FILLED",
         "terminal": False, "cum_quantity": "0.05", "outstanding": True}]})
    assert ledger.rows["I1"]["state"] == OB.OBSERVED
    ledger.reconcile({"ok": True, "as_of": 1e12, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_CANCELED", "terminal": True,
         "cum_quantity": "0.05", "outstanding": False}],
        "residual": None, "venue_net_qty": "0"})
    assert ledger.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED
    rep = ledger.reconcile({"ok": True, "as_of": 1e12, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_CANCELED", "terminal": True,
         "cum_quantity": "0.05", "outstanding": False}],
        "residual": {"qty": "0.05", "outcome_side": UP}})
    assert rep["released"] == ["I1"]
    assert ledger.rows["I1"]["filled_qty"] == "0.05"


def test_restart_preserves_the_unaccounted_state_and_the_reservation(
        tmp_path):
    sd = str(tmp_path / "e1_cohort")
    first = OB.ObligationLedger(sd, session_id=SID)
    row = _filled(first)
    first.reconcile({"ok": True, "as_of": 1e12, "orders": [row], "residual": None,
                     "venue_net_qty": "0"})
    assert first.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED

    # THE PROCESS DIES. Nothing was released on the way out.
    again = OB.ObligationLedger(sd, session_id=SID)
    assert again.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED
    assert len(again.blocking()) == 1
    assert again.reservation_holder()["intent_id"] == "I1"
    # a new worker on the same volume cannot take the reservation
    with pytest.raises(OB.ReservationHeld):
        again.record_submission(intent_id="I2", window=0, slot=0, slug="s",
                                side=UP, price="0.49", qty="0.13")


# =====================================================================
# ITEM 5: cancellation and shutdown may not retire an obligation
# =====================================================================

def test_retire_refuses_anything_that_carries_a_venue_id(ledger):
    """AN ACKNOWLEDGED ORDER MAY EXIST, so it is never retired -- whatever
    reason the caller gives."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    for why in ("shutdown", "cancelled at handover", "operator asked",
                "LATCH_REFUSED_BEFORE_THE_POST"):
        ledger.retire_never_sent("I1", why=why)
        assert ledger.rows["I1"]["state"] == OB.SUBMITTED, why
    assert len(ledger.blocking()) == 1
    assert ledger.reservation_holder()["intent_id"] == "I1"


def test_retire_refuses_anything_already_observed(ledger):
    """Observation means the venue showed us the order. It cannot then be
    retired as never-sent."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    ledger.reconcile({"ok": True, "as_of": 1e12, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_NEW", "terminal": False,
         "cum_quantity": "0", "outstanding": True}]})
    assert ledger.rows["I1"]["state"] == OB.OBSERVED
    ledger.retire_never_sent("I1", why="shutdown")
    assert ledger.rows["I1"]["state"] == OB.OBSERVED
    assert len(ledger.blocking()) == 1


def test_a_shutdown_cancel_releases_only_through_verified_terminal_state(
        ledger):
    """THE SHUTDOWN HANDOVER CANCELS BY ID, and the obligation follows the
    venue's answer rather than the cancel's claim.

    A cancel whose outcome is unverified leaves the obligation blocking; a
    cancel the venue confirms, with its fills accounted, releases it. The
    handover never touches the ledger directly -- which is the property
    this test exists to hold.
    """
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    # the handover fired a cancel and could not verify it
    rep = ledger.reconcile({"ok": False,
                            "reason": "ORDER_STATE_NOT_ESTABLISHED"})
    assert rep["released"] == []
    assert len(ledger.blocking()) == 1
    # the venue later confirms the cancel, nothing filled
    rep2 = ledger.reconcile({"ok": True, "as_of": 1e12, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_CANCELED", "terminal": True,
         "cum_quantity": "0", "outstanding": False}]})
    assert rep2["released"] == ["I1"]
    assert ledger.blocking() == []
    assert ledger.reservation_holder() is None, (
        "a fully reconciled obligation releases the reservation")


def test_the_ledger_has_no_cancel_submit_or_venue_read_surface():
    """IT IS A RECORD, NOT AN ACTOR. A bad read can make it refuse; it can
    never make it act."""
    names = dir(OB.ObligationLedger)
    for forbidden in ("cancel", "submit", "post", "request", "deadman",
                      "read_open_orders", "read_order"):
        assert not any(forbidden in n for n in names), forbidden
    # EXECUTABLE SURFACE, not prose: the module's docstrings name the
    # venue endpoints to explain what it does NOT do, so the check is for
    # anything that could actually make a call.
    src = open(OB.__file__, encoding="utf8").read()
    for forbidden in ("requests.", "urlopen", "urllib", "ssl.",
                      "BoundedOrderTransport", "cancel_exact",
                      "submit_one", "deadman_cancel_all"):
        assert forbidden not in src, forbidden
