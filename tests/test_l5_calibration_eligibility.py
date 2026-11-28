"""Four calibration-eligibility false positives, from the owner's audit.

READ-ONLY SOURCE AUDIT at `ec97d8ef2` (PR #208 comment 6720786353) found
that a record could read `usable_for_calibration: true` despite:

  1. **future-dated queue evidence** -- `_queue_block` set `stale=False`
     for a NEGATIVE `frame_age_s` inside the 1 s race tolerance, and
  2. `_fold(pre_submit)` set `queue_estimate=PRESENT` whenever
     `queue_ahead` was not None, ignoring `frame_age_s`, `stale`, `why`
     and clock provenance entirely;
  3. **insufficient lifetime samples** -- `_recompute_coverage` marked
     `lifetime_observation=PRESENT` on ONE observation, because the gap
     check between a single point and itself is vacuous, while the
     documented pre-flight gate always required two;
  4. **failed durable writes** -- `completeness()` decided eligibility
     from the five capture statuses alone and reported
     `ledger_write_failed` beside the verdict as if it were a footnote.

Every fix NARROWS eligibility. None can make an ineligible record
eligible, none refuses anything, and none is on the money path -- the last
section pins that.

No order, no venue call, no network.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_fill_calibration as CALIB
from src import l5_maker_fill_model as FM

SLUG = "cpc-btc-updown-15m-2026-11-08-1200z"


class Ladder:
    """`books`, `ahead_of_us`, `depth_at` -- the three `_queue_block` uses.

    `frame_t` is the only knob that matters here: it is what the frame age,
    and therefore the causality verdict, is computed from.
    """

    def __init__(self, frame_t: float, ahead="120.0", depth="300.0"):
        self.books = {SLUG: {"t": frame_t, "updates": 42}}
        self._a, self._d = D(ahead), D(depth)

    def ahead_of_us(self, slug, side, wire):
        return self._a

    def depth_at(self, slug, side, wire):
        return self._d


class Clock:
    """The recorder reads `read_t` from its OWN injectable clock, not from
    the `now` a caller passes -- which is the whole point: the frame age is
    `read_t - frame_t`, two independent sources. Driving it is what makes
    the causality cases reproducible."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def rec(tmp_path, name="calib.ndjson"):
    c = Clock()
    r = CALIB.CalibrationRecorder(str(tmp_path), path=str(tmp_path / name),
                                  clock=c)
    r._clock = c
    return r


def presubmit(r, coid, *, frame_t, now, book=None):
    r._clock.t = now
    r.pre_submit(coid, slug=SLUG, side=FM.UP, own_px="0.50", qty="1",
                 book=book if book is not None else {"bid": "0.50",
                                                     "ask": "0.51",
                                                     "t": now},
                 ladder=Ladder(frame_t), now=now)


def obs(r, coid, t, *, frame_t=None, prints=None):
    r._clock.t = t
    r.observe(t, slug=SLUG, book={"bid": "0.50", "ask": "0.51", "t": t},
              ladder=Ladder(t - 0.1 if frame_t is None else frame_t),
              prints=prints if prints is not None else [],
              open_coids=[coid])


# =============================================================== finding 1+2
def test_a_future_dated_queue_frame_is_not_usable_queue_evidence(tmp_path):
    """THE AUDIT'S CASE 1. The frame is dated 0.4 s AFTER our read.

    It is not stale -- it is newer than the read -- but it may hold depth
    that arrived after our decision, so it is not evidence of the queue
    our order joined. Calibrating on it fits the order against a book from
    its own future.
    """
    r = rec(tmp_path)
    presubmit(r, "future", frame_t=1000.4, now=1000.0)
    c = r.completeness("future")
    q = r.orders["future"]["pre_submit"]["queue"]
    assert q["frame_age_s"] == -0.4
    assert q["stale"] is False          # genuinely not stale
    assert q["causal"] is False         # and genuinely not causally prior
    assert c["capture"]["queue_estimate"].startswith(
        "MISSING:queue_frame_future_dated_by:0.4")
    assert c["usable_for_calibration"] is False


def test_freshness_and_causality_are_recorded_as_two_separate_fields(tmp_path):
    """The conflation is the defect; keep them distinguishable."""
    r = rec(tmp_path)
    presubmit(r, "prior", frame_t=999.5, now=1000.0)
    q = r.orders["prior"]["pre_submit"]["queue"]
    assert (q["stale"], q["causal"]) == (False, True)


def test_a_causally_prior_fresh_frame_is_still_usable(tmp_path):
    """The fix must not disqualify the good case."""
    r = rec(tmp_path)
    presubmit(r, "good", frame_t=999.0, now=1000.0)
    assert r.completeness("good")["capture"]["queue_estimate"] == "PRESENT"


def test_a_stale_frame_is_not_usable_and_names_its_age(tmp_path):
    r = rec(tmp_path)
    presubmit(r, "stale", frame_t=1000.0 - (CALIB.STALE_FRAME_S + 3),
              now=1000.0)
    cap = r.completeness("stale")["capture"]["queue_estimate"]
    assert cap.startswith("MISSING:queue_frame_stale:")
    assert "8.0" in cap


def test_a_frame_whose_clock_is_not_comparable_is_not_usable(tmp_path):
    """Beyond the race tolerance the two timestamps are not one clock."""
    r = rec(tmp_path)
    presubmit(r, "skew", frame_t=1005.0, now=1000.0)
    q = r.orders["skew"]["pre_submit"]["queue"]
    assert q["stale"] is None and q["causal"] is None
    assert (r.completeness("skew")["capture"]["queue_estimate"]
            == "MISSING:frame_clock_mismatch")


def test_a_frame_with_no_timestamp_is_not_usable(tmp_path):
    """Depth with unknown freshness is not calibration evidence."""
    class NoT(Ladder):
        def __init__(self):
            super().__init__(0.0)
            self.books = {SLUG: {"updates": 7}}
    r = rec(tmp_path)
    r.pre_submit("not", slug=SLUG, side=FM.UP, own_px="0.50", qty="1",
                 book={"bid": "0.50", "ask": "0.51", "t": 1000.0},
                 ladder=NoT(), now=1000.0)
    q = r.orders["not"]["pre_submit"]["queue"]
    assert q["frame_age_s"] is None
    assert q["stale"] is None and q["causal"] is None
    assert r.completeness("not")["capture"]["queue_estimate"].startswith(
        "MISSING:")


def test_absent_depth_still_names_its_own_reason_not_the_new_ones(tmp_path):
    """A missing depth figure must keep reporting WHY it is missing."""
    class NoDepth(Ladder):
        def ahead_of_us(self, slug, side, wire):
            return None
    r = rec(tmp_path)
    r.pre_submit("nod", slug=SLUG, side=FM.UP, own_px="0.50", qty="1",
                 book={"bid": "0.50", "ask": "0.51", "t": 1000.0},
                 ladder=NoDepth(999.0), now=1000.0)
    assert (r.completeness("nod")["capture"]["queue_estimate"]
            == "MISSING:price_outside_published_depth")


def test_precapture_ok_now_declines_a_future_dated_frame(tmp_path):
    """The probe hook follows the capture status, so a probe can decline."""
    r = rec(tmp_path)
    presubmit(r, "future", frame_t=1000.4, now=1000.0)
    presubmit(r, "good", frame_t=999.0, now=1000.0)
    assert r.precapture_ok("future") is False
    assert r.precapture_ok("good") is True


# ================================================================= finding 3
def test_one_observation_does_not_meet_the_documented_two(tmp_path):
    """THE AUDIT'S CASE 2. A single sample passes the gap check vacuously:
    there is no interval between one point and itself."""
    r = rec(tmp_path)
    presubmit(r, "one", frame_t=999.0, now=1000.0)
    obs(r, "one", 1001.0)
    c = r.completeness("one")
    assert c["capture"]["lifetime_observation"] == (
        "MISSING:insufficient_observations:1_of_2")
    assert c["usable_for_calibration"] is False


def test_two_observations_with_no_gap_do_meet_it(tmp_path):
    r = rec(tmp_path)
    presubmit(r, "two", frame_t=999.0, now=1000.0)
    for t in (1001.0, 1006.0):
        obs(r, "two", t)
    assert (r.completeness("two")["capture"]["lifetime_observation"]
            == "PRESENT")
    assert r.orders["two"]["coverage"]["observations"] == 2


def test_a_coverage_gap_still_outranks_the_count(tmp_path):
    """Two observations 40 s apart are a gap, not coverage, and the record
    must say `coverage_gap` rather than the new count reason."""
    r = rec(tmp_path)
    presubmit(r, "gap", frame_t=999.0, now=1000.0)
    for t in (1001.0, 1051.0):
        obs(r, "gap", t)
    assert (r.completeness("gap")["capture"]["lifetime_observation"]
            == "MISSING:coverage_gap")


def test_no_observations_still_reads_no_observations(tmp_path):
    r = rec(tmp_path)
    presubmit(r, "none", frame_t=999.0, now=1000.0)
    assert (r.completeness("none")["capture"]["lifetime_observation"]
            == "MISSING:no_observations")


# ================================================================= finding 4
def test_a_failed_append_makes_that_record_ineligible(tmp_path):
    """THE AUDIT'S CASE 3. The capture statuses come from the IN-MEMORY
    fold, which succeeds whether or not the append did -- so they cannot
    see a durability failure. A record that exists only in this process
    and would vanish on restart is not a calibration."""
    r = rec(tmp_path)
    presubmit(r, "lost", frame_t=999.0, now=1000.0)

    # MAKE THE REAL WRITE FAIL, rather than replacing `_append` -- the
    # attribution lives inside its own `except OSError`, so stubbing the
    # method out would bypass the very code under test. A directory where
    # the ledger file should be makes `open(..., "a")` raise
    # IsADirectoryError, which is an OSError, through the real path.
    import os
    os.remove(r.path)
    os.mkdir(r.path)
    for t in (1001.0, 1006.0):
        obs(r, "lost", t)
    r.on_fill("lost", qty="1", price="0.50", cumulative_qty="1", now=1007.0)
    r.on_fees_actual("lost", fee_usd="0", rebate_usd="0", source="venue",
                     now=1007.1)
    r.on_terminal("lost", state="CANCELED", venue_confirmed=True,
                  now=1010.0)
    c = r.completeness("lost")
    # every category present...
    assert c["missing"] == [], c["capture"]
    # ...and still not usable, because the events are not durable
    assert c["usable_for_calibration"] is False
    assert c["why"].startswith("events_not_durable:")
    assert c["write_failures"] >= 1


def test_a_failure_on_another_order_does_not_disqualify_this_one(tmp_path):
    """PER RECORD, not recorder-wide. A failed append for some other
    order says nothing about this record's durability, and disqualifying
    every record for one unrelated ENOSPC would be wrong the other way."""
    r = rec(tmp_path)
    presubmit(r, "mine", frame_t=999.0, now=1000.0)
    for t in (1001.0, 1006.0):
        obs(r, "mine", t)
    r.on_terminal("mine", state="CANCELED", venue_confirmed=True, now=1010.0)
    # a failure attributed to a DIFFERENT record
    r.orders.setdefault("other", {"write_failures": 0})
    r.orders["other"]["write_failures"] = 3
    r.errors["ledger_write_failed"] = 3
    c = r.completeness("mine")
    assert c["usable_for_calibration"] is True
    assert c["write_failures"] == 0
    assert c["recorder_write_failures"] == 3


# ========================================= finding 4b: the positive fixture
def _full_lifecycle(r, coid="good"):
    presubmit(r, coid, frame_t=999.0, now=1000.0)
    r.on_ack(coid, venue_order_id="V-1", now=1000.2)
    for t in (1001.0, 1006.0):
        obs(r, coid, t)
    r.on_fill(coid, qty="1", price="0.50", cumulative_qty="1", now=1007.0)
    r.on_fees_actual(coid, fee_usd="0", rebate_usd="0.00727",
                     source="venue", now=1007.1)
    r.on_terminal(coid, state="FILLED", venue_confirmed=True, now=1007.2)


def test_a_causal_two_observation_lifecycle_is_eligible(tmp_path):
    """THE AUDIT'S CASE 4. The fixes must leave the good case good."""
    r = rec(tmp_path)
    _full_lifecycle(r)
    c = r.completeness("good")
    assert c["missing"] == [], c["capture"]
    assert c["usable_for_calibration"] is True
    assert c["why"] is None
    assert r.report()["usable_for_calibration"] == 1


def test_a_restart_replay_preserves_the_eligible_classification(tmp_path):
    """Reload from the ledger alone and the verdict must not move."""
    r = rec(tmp_path)
    _full_lifecycle(r)
    before = r.completeness("good")
    r2 = rec(tmp_path)
    after = r2.completeness("good")
    assert after["usable_for_calibration"] is True
    assert after["capture"] == before["capture"]
    assert r2.appended == 0, "a replay must append nothing"


def test_a_restart_replay_preserves_an_INELIGIBLE_classification(tmp_path):
    """The direction that matters more: a reload must not launder a
    one-observation record into a usable one."""
    r = rec(tmp_path)
    presubmit(r, "one", frame_t=999.0, now=1000.0)
    obs(r, "one", 1001.0)
    r.on_terminal("one", state="CANCELED", venue_confirmed=True, now=1010.0)
    r2 = rec(tmp_path)
    c = r2.completeness("one")
    assert c["usable_for_calibration"] is False
    assert c["capture"]["lifetime_observation"].startswith(
        "MISSING:insufficient_observations")


# ====================================================== containment
def test_the_recorder_still_refuses_nothing(tmp_path):
    """NARROWING ELIGIBILITY IS NOT A VETO. The recorder has no method
    that returns a refusal, and `precapture_ok` is a hook a probe may
    consult -- the money path never asks it."""
    import inspect
    src = inspect.getsource(CALIB)
    for banned in ("def submit", "def cancel", "raise PermissionError"):
        assert banned not in src, banned
    r = rec(tmp_path)
    presubmit(r, "future", frame_t=1000.4, now=1000.0)      # ineligible
    # ...and the record is still fully written and readable
    assert r.record("future") is not None
    assert r.orders["future"]["pre_submit"]["queue"]["queue_ahead"] == "120.0"


def test_an_ineligible_record_is_still_appended_in_full(tmp_path):
    """Ineligible is not unrecorded. The evidence stays on disk."""
    r = rec(tmp_path)
    presubmit(r, "future", frame_t=1000.4, now=1000.0)
    lines = [json.loads(l) for l in
             open(str(tmp_path / "calib.ndjson")).read().splitlines() if l]
    assert len(lines) == 1 and lines[0]["kind"] == "pre_submit"
    assert lines[0]["queue"]["causal"] is False


@pytest.mark.parametrize("bad", [None, {}, {"queue_ahead": None}])
def test_the_queue_classifier_never_raises_on_a_degenerate_block(bad):
    out = CALIB._queue_capture(bad or {})
    assert out.startswith("MISSING:")
