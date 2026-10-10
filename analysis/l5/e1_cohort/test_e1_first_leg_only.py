"""ONE POST, ACROSS EVERY PATH THAT CAN REACH A SUBMISSION.

The supervised probe's requirement is not "one attempt" -- the latch already
bounds attempts, and on 2026-10-08 it permitted twenty of them. It is "at
most one POST /v1/orders, ever, and no complement". So the proof here is
behavioural and uses the burst harness itself: the same loop that produced
twenty first legs is run with this mode ON and must produce exactly ONE.

The control matters as much as the test: with the mode OFF the burst must
still reproduce at twenty. A guard that quietly changes the engine when
nobody asked for it is a worse defect than the one it fixes.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import first_leg_only as FLO
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort.spec import MAX_ATTEMPTS_PER_WINDOW
from analysis.l5.e1_cohort.test_e1_live_path import (FakeTransport, arm,
                                                     fake_pin, BASE)
from analysis.l5.e1_cohort.test_e1_burst_1215z import (LaggingVenue, _run,
                                                       wired)            # noqa: F401
from analysis.l5.e1_cohort.test_e1_run_cohort_lifecycle import Clock, LoopVenue


@pytest.fixture()
def flo_dir(monkeypatch, tmp_path):
    d = str(tmp_path / "flo")
    monkeypatch.setenv(FLO.DIR_ENV, d)
    monkeypatch.setenv("PMUS_E1_PROBE_EPOCH", "test-epoch")
    return d


def _on(monkeypatch):
    monkeypatch.setenv(FLO.ENV, "YES")


# =====================================================================
# 1. DEFAULT OFF, and only one spelling turns it on
# =====================================================================

def test_the_mode_is_off_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv(FLO.ENV, raising=False)
    assert FLO.enabled() is False


@pytest.mark.parametrize("value", ["", " ", "yes", "Yes", "1", "true",
                                   "TRUE", "Y", "ON", "NO", "0"])
def test_no_other_spelling_enables_the_mode(monkeypatch, value):
    monkeypatch.setenv(FLO.ENV, value)
    assert FLO.enabled() is False, value


def test_exactly_YES_enables_it(monkeypatch):
    monkeypatch.setenv(FLO.ENV, " YES ")
    assert FLO.enabled() is True


def test_when_off_every_entry_point_is_inert(monkeypatch, flo_dir):
    monkeypatch.delenv(FLO.ENV, raising=False)
    FLO.refuse_complement()                      # must not raise
    assert FLO.report()["first_leg_only"] is False
    assert not os.path.exists(FLO.claim_path())


# =====================================================================
# 2. THE BEHAVIOURAL PROOF: the burst harness, one POST instead of twenty
# =====================================================================

def test_the_burst_that_placed_twenty_places_exactly_one(wired, monkeypatch,
                                                         flo_dir):
    """The 2026-10-08 defect restored AND this mode on. One POST."""
    _on(monkeypatch)
    clock = Clock(BASE)
    venue = LoopVenue(clock, wired["slug"])
    _run(wired, venue, clock, outstanding_literal=0)
    assert len(FakeTransport.posts) == 1, FakeTransport.posts
    assert os.path.exists(FLO.claim_path())


def test_the_same_burst_with_the_mode_off_still_reproduces(wired, monkeypatch,
                                                           flo_dir):
    """THE CONTROL. Without the flag the engine is untouched."""
    monkeypatch.delenv(FLO.ENV, raising=False)
    clock = Clock(BASE)
    venue = LoopVenue(clock, wired["slug"])
    _run(wired, venue, clock, outstanding_literal=0)
    assert len(FakeTransport.posts) == MAX_ATTEMPTS_PER_WINDOW, (
        "the reproduction stopped reproducing, so the test above measures "
        "nothing")
    assert not os.path.exists(FLO.claim_path())


def test_a_normal_loop_under_the_mode_also_stops_at_one(wired, monkeypatch,
                                                        flo_dir):
    """Not just the defective literal: the repaired loop too."""
    _on(monkeypatch)
    clock = Clock(BASE)
    venue = LaggingVenue(clock, wired["slug"], lag_reads=3)
    _run(wired, venue, clock)
    assert len(FakeTransport.posts) <= 1, FakeTransport.posts


# =====================================================================
# 3. THE PERMISSION IS SINGLE-USE AND DURABLE
# =====================================================================

def test_a_second_claim_in_this_process_is_refused(monkeypatch, flo_dir):
    _on(monkeypatch)
    FLO.claim_the_single_post({"n": 1})
    with pytest.raises(FLO.OnePostSpent):
        FLO.claim_the_single_post({"n": 2})


def test_the_claim_survives_a_process_restart(monkeypatch, flo_dir):
    """Nothing is held in memory: a fresh read of the same volume refuses.

    This is the SIGKILL case -- the guard's whole reason to be a file.
    """
    _on(monkeypatch)
    FLO.claim_the_single_post({"before": "crash"})
    assert FLO.claimed() is True
    with pytest.raises(FLO.OnePostSpent):
        FLO.claim_the_single_post({"after": "restart"})


def test_the_claim_is_never_empty_on_disk(monkeypatch, flo_dir):
    _on(monkeypatch)
    FLO.claim_the_single_post({"slug": "x"})
    body = open(FLO.claim_path()).read()
    assert body.strip(), "an empty claim would be unreadable evidence"
    assert "claimed_utc" in body and "test-epoch" in body


def test_a_new_epoch_is_a_new_permission(monkeypatch, flo_dir):
    """An operator authorizing a SECOND probe does not delete the first."""
    _on(monkeypatch)
    FLO.claim_the_single_post({"probe": 1})
    first = FLO.claim_path()
    monkeypatch.setenv("PMUS_E1_PROBE_EPOCH", "test-epoch-2")
    FLO.claim_the_single_post({"probe": 2})
    assert FLO.claim_path() != first
    assert os.path.exists(first), "the first probe's record must survive"


def test_no_temp_files_are_left_behind(monkeypatch, flo_dir):
    _on(monkeypatch)
    FLO.claim_the_single_post({})
    d = os.path.dirname(FLO.claim_path())
    assert [f for f in os.listdir(d) if f.endswith(".tmp")] == []


# =====================================================================
# 4. FAIL CLOSED
# =====================================================================

def test_an_unwritable_volume_permits_nothing(monkeypatch, tmp_path):
    _on(monkeypatch)
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    monkeypatch.setenv(FLO.DIR_ENV, str(blocker / "under_a_file"))
    with pytest.raises(FLO.GuardUnusable):
        FLO.claim_the_single_post({})


def test_a_failed_write_permits_nothing(monkeypatch, flo_dir):
    _on(monkeypatch)
    real_open = os.open

    def boom(path, *a, **k):
        if str(path).endswith(".tmp"):
            raise OSError("ENOSPC")
        return real_open(path, *a, **k)
    monkeypatch.setattr(os, "open", boom)
    with pytest.raises(FLO.GuardUnusable):
        FLO.claim_the_single_post({})
    monkeypatch.undo()
    assert not os.path.exists(FLO.claim_path())


# =====================================================================
# 5. THE COMPLEMENT IS REFUSED BEFORE THE VENUE CAN SEE ANYTHING
# =====================================================================

def test_refuse_complement_raises_when_on(monkeypatch, flo_dir):
    _on(monkeypatch)
    with pytest.raises(FLO.OnePostSpent):
        FLO.refuse_complement()


def test_submit_complement_sends_nothing_when_on(monkeypatch, flo_dir,
                                                 wired):
    """Refused before preview: the transport is never even constructed."""
    _on(monkeypatch)
    built = []

    def factory():
        built.append(1)
        raise AssertionError("a transport must not be built")

    with pytest.raises(FLO.OnePostSpent):
        R.submit_complement(
            window=0, slot=0,
            snapshot={"market": wired["slug"], "first_leg_price": "0.41",
                      "complement": {"side": "OUTCOME_SIDE_NO",
                                     "best_bid": "0.55"},
                      "pin": wired},
            first_leg_filled_qty="0.13",
            tracker=None, transport_factory=factory)
    assert built == [], "nothing may be constructed before the refusal"


def test_the_complement_refusal_does_not_need_the_claim(monkeypatch, flo_dir):
    """A complement is refused even if no first leg was ever submitted."""
    _on(monkeypatch)
    assert FLO.claimed() is False
    with pytest.raises(FLO.OnePostSpent):
        FLO.refuse_complement()


# =====================================================================
# 6. THE REPORT AN OPERATOR READS
# =====================================================================

def test_the_report_states_the_bound_and_its_claim(monkeypatch, flo_dir):
    _on(monkeypatch)
    before = FLO.report()
    assert before["first_leg_only"] is True
    assert before["one_post_already_claimed"] is False
    assert before["COMPLEMENT_REFUSED_BEFORE_PREVIEW"] is True
    assert before["DEFAULT_IS_OFF"] is True
    FLO.claim_the_single_post({})
    assert FLO.report()["one_post_already_claimed"] is True


# =====================================================================
# 7. THE BOUND IS A TERMINAL, RECORDED STOP -- never a classified fault
# =====================================================================

def test_the_bound_stops_the_run_with_its_own_reason(wired, monkeypatch,
                                                     flo_dir):
    """Not a traceback, and not a fault the loop paces and retries."""
    _on(monkeypatch)
    clock = Clock(BASE)
    venue = LoopVenue(clock, wired["slug"])
    out, _tracker = _run(wired, venue, clock, outstanding_literal=0)
    assert out.get("stopped_because") == "FIRST_LEG_ONLY_BOUND_REACHED", out
    assert len(FakeTransport.posts) == 1


def test_the_guard_being_unusable_also_stops_the_run(wired, monkeypatch,
                                                     tmp_path):
    """Fail closed: an unwritable volume stops the run and submits nothing."""
    _on(monkeypatch)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv(FLO.DIR_ENV, str(blocker / "under_a_file"))
    clock = Clock(BASE)
    venue = LoopVenue(clock, wired["slug"])
    out, _tracker = _run(wired, venue, clock, outstanding_literal=0)
    assert out.get("stopped_because") == "FIRST_LEG_ONLY_GUARD_UNUSABLE", out
    assert FakeTransport.posts == [], "no POST may leave on an unusable guard"
