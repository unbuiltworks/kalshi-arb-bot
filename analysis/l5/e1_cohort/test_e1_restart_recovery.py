"""RESTART RECOVERY: NO DUPLICATE PLACEMENT, EVER.

`/v1/orders` is HTTP 501 on PMUS, so order state can only be established by
reading ids individually -- which works only if the ids survive a restart.
They used to live in memory and die with the process, so a restarted E1
came back believing it had no orders. That is the same belief that produced
twenty resting orders in one window.

These tests cover the three recoveries the owner named -- partial fills,
ambiguous acknowledgements, and plain restarts -- and in each case assert
the thing that matters: NO SECOND FIRST LEG.

The ambiguous case is the sharpest. Before this repair, `SubmissionAmbiguous`
wrote NOTHING to the tape: the single most dangerous state E1 can occupy,
"we may have created an order and cannot prove we did not", left no durable
trace at all, and the next process saw a clean slate.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import order_registry as ORG
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import (FakeVenue, SLUG,
                                                          UP, DOWN)

SID = "E1-COHORT-20261008T005816Z-051888d8"


def write_tape(tmp_path, rows, *, session_id=SID):
    d = tmp_path / "e1_cohort" / "tape"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "events.jsonl"
    with open(p, "a", encoding="utf8") as fh:
        for r in rows:
            r = dict(r)
            r.setdefault("session_id", session_id)
            fh.write(json.dumps(r) + "\n")
    return str(tmp_path / "e1_cohort")


def ev(event, detail, *, window=0, slot=0):
    return {"event": event, "window": window, "slot": slot,
            "detail": detail, "kind": "event", "append_only": True}


class Provider:
    """Only the three attributes `ORG.install` touches."""

    def __init__(self):
        self.known_order_ids = []
        self.order_roles = {}
        self.pending_order_ids = []


# =====================================================================
# THE REGISTRY ITSELF
# =====================================================================

def test_confirmed_ids_and_roles_come_back_from_the_tape(tmp_path):
    sd = write_tape(tmp_path, [
        ev("FIRST_LEG_SUBMITTED", {"order_id": "FL1"}),
        ev("COMPLEMENT_SUBMITTED", {"order_id": "CP1"}),
    ])
    reg = ORG.load_from_tape(sd, session_id=SID)
    assert reg["known_ids"] == ["FL1", "CP1"]
    assert reg["roles"] == {"FL1": OS.FIRST_LEG, "CP1": OS.COMPLEMENT}
    assert reg["pending"] == []


def test_the_forty_real_ids_shape_round_trips(tmp_path):
    """The real 1215z/1230z tape shape, as the reconciliation found it."""
    ids = ["CZGVWJ5HTZ7D", "CZGW0DRSAZ7J", "CZH22KCSPZ8H", "CZH2HF6RAZ8E"]
    sd = write_tape(tmp_path, [
        ev("FIRST_LEG_SUBMITTED", {"order_id": i, "latch_path": "/x"})
        for i in ids])
    reg = ORG.load_from_tape(sd, session_id=SID)
    assert reg["known_ids"] == ids
    assert all(reg["roles"][i] == OS.FIRST_LEG for i in ids)


def test_an_intent_with_no_confirmation_comes_back_PENDING(tmp_path):
    """The crash-between-request-and-acknowledgement case."""
    sd = write_tape(tmp_path, [
        ev("FIRST_LEG_SUBMISSION_INTENT",
           {"intent_id": "LOCAL_INTENT:w0:%s:UP:0.49" % SLUG}),
    ])
    reg = ORG.load_from_tape(sd, session_id=SID)
    assert reg["known_ids"] == []
    assert reg["pending"] == ["LOCAL_INTENT:w0:%s:UP:0.49" % SLUG]
    assert reg["AN_INTENT_WITHOUT_A_CONFIRMED_ID_BLOCKS"] is True


def test_an_intent_the_tape_later_confirmed_is_retired(tmp_path):
    """Otherwise every successful order would block the next one forever."""
    iid = "LOCAL_INTENT:w0:%s:UP:0.49" % SLUG
    sd = write_tape(tmp_path, [
        ev("FIRST_LEG_SUBMISSION_INTENT", {"intent_id": iid}),
        ev("FIRST_LEG_SUBMITTED", {"order_id": "FL1", "intent_id": iid}),
    ])
    reg = ORG.load_from_tape(sd, session_id=SID)
    assert reg["known_ids"] == ["FL1"]
    assert reg["pending"] == []
    assert reg["intents_superseded_by_a_confirmed_id"] == {iid: "FL1"}


def test_an_ambiguous_submission_row_blocks(tmp_path):
    sd = write_tape(tmp_path, [
        ev("AMBIGUOUS_SUBMISSION", {"slug": SLUG, "never_retried": True}),
    ])
    reg = ORG.load_from_tape(sd, session_id=SID)
    assert len(reg["pending"]) == 1
    assert reg["pending"][0].startswith(OS.LOCAL_INTENT)


def test_a_submission_row_with_no_order_id_blocks(tmp_path):
    """An unidentified submission is the ambiguous case under another name."""
    sd = write_tape(tmp_path, [ev("FIRST_LEG_SUBMITTED", {})])
    reg = ORG.load_from_tape(sd, session_id=SID)
    assert reg["known_ids"] == []
    assert len(reg["pending"]) == 1


def test_another_sessions_ids_are_not_inherited(tmp_path):
    """Both prior invalidated cohorts live on this volume permanently."""
    sd = write_tape(tmp_path, [ev("FIRST_LEG_SUBMITTED",
                                  {"order_id": "OLD1"})],
                    session_id="E1-COHORT-SOME-OTHER-SESSION")
    write_tape(tmp_path, [ev("FIRST_LEG_SUBMITTED", {"order_id": "MINE1"})])
    reg = ORG.load_from_tape(sd, session_id=SID)
    assert reg["known_ids"] == ["MINE1"]


def test_a_row_with_no_session_is_KEPT(tmp_path):
    """An unattributable order of ours is what must not be waved past."""
    d = tmp_path / "e1_cohort" / "tape"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "events.jsonl", "w", encoding="utf8") as fh:
        fh.write(json.dumps(ev("FIRST_LEG_SUBMITTED",
                               {"order_id": "NOSESSION"})) + "\n")
    reg = ORG.load_from_tape(str(tmp_path / "e1_cohort"), session_id=SID)
    assert reg["known_ids"] == ["NOSESSION"]


def test_a_truncated_last_line_does_not_discard_the_registry(tmp_path):
    sd = write_tape(tmp_path, [ev("FIRST_LEG_SUBMITTED",
                                  {"order_id": "FL1"})])
    with open(os.path.join(sd, "tape", "events.jsonl"), "a",
              encoding="utf8") as fh:
        fh.write('{"event": "FIRST_LEG_SUB')     # killed mid-write
    reg = ORG.load_from_tape(sd, session_id=SID)
    assert reg["known_ids"] == ["FL1"]
    assert reg["unparseable_rows"] == 1


def test_a_missing_tape_is_an_empty_registry_not_a_crash(tmp_path):
    reg = ORG.load_from_tape(str(tmp_path / "nope"), session_id=SID)
    assert reg["tape_exists"] is False
    assert reg["known_ids"] == [] and reg["pending"] == []


def test_install_mutates_in_place_rather_than_rebinding(tmp_path):
    """`submit_first_leg` appends to these very lists; rebinding them would
    silently disconnect the two halves."""
    sd = write_tape(tmp_path, [ev("FIRST_LEG_SUBMITTED",
                                  {"order_id": "FL1"})])
    p = Provider()
    known_before = p.known_order_ids
    roles_before = p.order_roles
    pending_before = p.pending_order_ids
    ORG.install(p, ORG.load_from_tape(sd, session_id=SID))
    assert p.known_order_ids is known_before
    assert p.order_roles is roles_before
    assert p.pending_order_ids is pending_before
    assert p.known_order_ids == ["FL1"]


# =====================================================================
# RECOVERY, END TO END: NO DUPLICATE PLACEMENT
# =====================================================================

def _snapshot(view, *, price="0.49"):
    return {"window": 0, "window_open": True, "within_handover_tte": True,
            "market": SLUG, "pin": {"slug": SLUG}, "qty": "0.13",
            "first_leg_price": price, "order_state": view,
            "our_outstanding_orders": OS.outstanding_for_snapshot(view),
            "our_open_residual": OS.residual_for_snapshot(view)}


def test_restart_into_a_PARTIAL_fill_places_no_second_first_leg(tmp_path):
    v = FakeVenue()
    v.rest("FL1", side=UP, wire="0.49")
    v.fill("FL1", "0.05")
    sd = write_tape(tmp_path, [ev("FIRST_LEG_SUBMITTED",
                                  {"order_id": "FL1"})])
    reg = ORG.load_from_tape(sd, session_id=SID)
    p = Provider()
    ORG.install(p, reg)
    view = v.view(known=p.known_order_ids, roles=p.order_roles,
                  pending_ids=p.pending_order_ids)
    el = R.eligible(_snapshot(view), SP.CohortTracker(), now=1000.0)
    assert el["ok"] is False
    assert any(str(r).startswith("E3_OUR_ORDER_IS_OUTSTANDING:1")
               for r in el["refusals"])
    # the inventory is recognised and its complement is permitted
    assert D(view["residual"]["qty"]) == D("0.05")
    assert OS.complement_gate(view)["may_submit_complement"] is True


def test_restart_into_an_AMBIGUOUS_submission_places_nothing(tmp_path):
    """No order id exists, the venue shows nothing, and we still refuse --
    because we cannot prove an order does not exist."""
    v = FakeVenue()                     # the venue shows NOTHING
    sd = write_tape(tmp_path, [
        ev("FIRST_LEG_SUBMISSION_INTENT",
           {"intent_id": "LOCAL_INTENT:w0:%s:UP:0.49" % SLUG}),
        ev("AMBIGUOUS_SUBMISSION", {"slug": SLUG}),
    ])
    reg = ORG.load_from_tape(sd, session_id=SID)
    p = Provider()
    ORG.install(p, reg)
    assert len(p.pending_order_ids) == 2
    view = v.view(known=p.known_order_ids, roles=p.order_roles,
                  pending_ids=p.pending_order_ids)
    assert view["ok"] is True
    assert view["n_outstanding_first_legs"] == 2
    el = R.eligible(_snapshot(view), SP.CohortTracker(), now=1000.0)
    assert el["ok"] is False
    assert any(str(r).startswith("E3_OUR_ORDER_IS_OUTSTANDING")
               for r in el["refusals"])


def test_restart_into_a_COMPLETED_pair_may_open_a_new_first_leg(tmp_path):
    """Recovery must not deadlock on finished work: a matched, settled pair
    leaves nothing outstanding and nothing unmatched."""
    v = FakeVenue()
    v.rest("FL1", side=UP, wire="0.49")
    v.fill("FL1", "0.13")
    v.rest("CP1", side=DOWN, wire="0.53")
    v.fill("CP1", "0.13")
    sd = write_tape(tmp_path, [
        ev("FIRST_LEG_SUBMITTED", {"order_id": "FL1"}),
        ev("COMPLEMENT_SUBMITTED", {"order_id": "CP1"}),
    ])
    p = Provider()
    ORG.install(p, ORG.load_from_tape(sd, session_id=SID))
    view = v.view(known=p.known_order_ids, roles=p.order_roles,
                  pending_ids=p.pending_order_ids)
    assert view["n_outstanding"] == 0
    assert view["residual"] is None
    assert OS.reconcile_at_startup(view)["may_submit"] is True


def test_twenty_restarts_place_nothing_while_one_order_rests(tmp_path):
    """THE RUNAWAY, AS A RESTART LOOP. Each cycle is a fresh process:
    registry from disk, state from the venue, decision from `eligible`."""
    v = FakeVenue()
    v.rest("FL1", side=UP, wire="0.49")
    sd = write_tape(tmp_path, [ev("FIRST_LEG_SUBMITTED",
                                  {"order_id": "FL1"})])
    for _ in range(20):
        p = Provider()                               # a NEW process
        ORG.install(p, ORG.load_from_tape(sd, session_id=SID))
        view = v.view(known=p.known_order_ids, roles=p.order_roles,
                      pending_ids=p.pending_order_ids)
        el = R.eligible(_snapshot(view), SP.CohortTracker(), now=1000.0)
        assert el["ok"] is False
        assert any(str(r).startswith("E3_OUR_ORDER_IS_OUTSTANDING")
                   for r in el["refusals"])
    # the venue still holds exactly the one order it started with
    assert len(v.orders) == 1


def test_the_intent_row_is_written_BEFORE_the_post():
    """Read from the SOURCE: the ordering is the whole point. An intent
    recorded after the fact leaves the dangerous window uncovered."""
    import inspect
    src = inspect.getsource(R.submit_first_leg)
    i_intent = src.index("FIRST_LEG_SUBMISSION_INTENT")
    i_post = src.index("_submit_once")
    assert i_intent < i_post, \
        "the intent must be recorded before the POST, not after it"


def test_an_ambiguous_submission_now_reaches_the_tape():
    """It wrote nothing at all before this repair."""
    import inspect
    src = inspect.getsource(R.run_cohort)
    assert 'event="AMBIGUOUS_SUBMISSION"' in src
    assert "blocks_until_venue_reconciled" in src
