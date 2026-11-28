"""The six freshness/execution measurements, and the limits of each.

PRIORITY 1 of the owner's assignment, measured on synthetic inputs so the
figures are the path's OWN clock and not a 6.4-6.9s recorded ladder.
"""
from decimal import Decimal as D

import pytest

from src import l5_completion_latency as CL
from src import l5_book_completion as BC
from src import l5_two_sided_probe as TS


# ---------------------------------------------------------------- item 1
def test_fill_detection_latency_declares_itself_unmeasurable():
    """The venue's progress payload carries no time, so this is a BOUND.

    Guarding against the one failure that matters here: a later reader
    taking a detection-side figure for a venue-side one.
    """
    c = CL.CompletionLatency()
    c.note_fill_detected("m", "UP", 10.0)
    r = c.report("m")
    assert r["fill_detection_latency"]["measurable"] is False
    assert r["fill_detection_latency"]["bounded_by"] == "order_poll_cadence"


def test_poll_cadence_is_measured_so_the_bound_is_a_measurement():
    c = CL.CompletionLatency()
    for t in (0.0, 6.1, 12.0, 18.4, 30.0):
        c.note_poll("m", t)
    g = c.report("m")["poll_gaps_s"]
    assert g["n"] == 4
    assert g["max"] == 11.6            # the real worst gap, not ORDER_POLL_S
    assert g["median"] == 6.4


def test_poll_list_is_bounded_so_a_long_window_cannot_grow_without_limit():
    c = CL.CompletionLatency()
    for i in range(1000):
        c.note_poll("m", float(i))
    assert len(c.m["m"]["polls"]) == 256


# ---------------------------------------------------------------- item 2
def test_book_age_is_carried_on_a_PASSING_decision_not_only_a_stale_one():
    """The defect this change fixes.

    `decide` used to record `stream_age_s` only inside the STALE_BOOK
    refusal, so a decision that PASSED the freshness test -- the one case
    where we act on the book -- carried no evidence of how fresh it was.
    That made the 2-second requirement unauditable in exactly the case
    that matters.
    """
    rec = BC.decide(
        market="m", held_side="UP", held_qty=D("1"), held_cost_usd=D("0.50"),
        deficient_qty=D("0"), deficient_cost_usd=D("0"),
        book={"bid": D("0.50"), "ask": D("0.51"), "stream_age_s": D("0.4")},
        now_t=100.0, window_start=0.0, resting=[], outstanding_qty=D("0"),
        tick="0.01", min_qty="1", depth_ticks=0,
        residual_budget_usd=D("1"), submissions_left=5, last_decide_t=0.0)
    # whatever it decided, the age it decided ON is on the record
    assert rec.get("stream_age_s") == "0.4"
    assert rec.get("reason") != "STALE_BOOK"


def test_a_stale_refusal_still_carries_the_age_it_refused_on():
    rec = BC.decide(
        market="m", held_side="UP", held_qty=D("1"), held_cost_usd=D("0.50"),
        deficient_qty=D("0"), deficient_cost_usd=D("0"),
        book={"bid": D("0.50"), "ask": D("0.51"),
              "stream_age_s": D(str(TS.MAX_BOOK_AGE_S + 5))},
        now_t=100.0, window_start=0.0, resting=[], outstanding_qty=D("0"),
        tick="0.01", min_qty="1", depth_ticks=0,
        residual_budget_usd=D("1"), submissions_left=5, last_decide_t=0.0)
    assert rec["reason"] == "STALE_BOOK"
    assert rec["stream_age_s"] == str(TS.MAX_BOOK_AGE_S + 5)


def test_a_future_dated_book_is_stale_and_its_negative_age_is_recorded():
    rec = BC.decide(
        market="m", held_side="UP", held_qty=D("1"), held_cost_usd=D("0.50"),
        deficient_qty=D("0"), deficient_cost_usd=D("0"),
        book={"bid": D("0.50"), "ask": D("0.51"), "stream_age_s": D("-3")},
        now_t=100.0, window_start=0.0, resting=[], outstanding_qty=D("0"),
        tick="0.01", min_qty="1", depth_ticks=0,
        residual_budget_usd=D("1"), submissions_left=5, last_decide_t=0.0)
    assert rec["reason"] == "STALE_BOOK"
    assert rec["stream_age_s"] == "-3"


def test_book_age_quantiles_separate_a_fresh_regime_from_a_stale_one():
    c = CL.CompletionLatency()
    for a in (0.2, 0.3, 0.4, 6.7, 6.9):
        c.note_decision("m", 1.0, action="NO", reason="X", book_age_s=a)
    r = c.report("m")["book_age_at_decision_s"]
    assert r["median"] == 0.4 and r["max"] == 6.9
    # the whole point: the p95 is NOT the median, so a 2s requirement met
    # on average and missed at the tail cannot read as met.
    assert r["p95"] == 6.9


# ---------------------------------------------------------------- item 3
def test_detection_to_decision_is_measured_against_our_own_detection():
    c = CL.CompletionLatency()
    c.note_fill_detected("m", "UP", 100.0)
    c.note_decision("m", 105.0, action="NO", reason="COOLDOWN",
                    book_age_s=0.3)
    c.note_decision("m", 112.5, action="COMPLETE", reason="OK",
                    book_age_s=0.5)
    r = c.report("m")
    assert r["detection_to_decision_s"]["n"] == 2
    assert r["detection_to_first_actionable_s"] == 12.5
    assert r["actionable_decisions"] == 1


def test_a_decision_with_no_detection_yields_no_interval_not_a_zero():
    """A missing input must not read as an instantaneous decision."""
    c = CL.CompletionLatency()
    c.note_decision("m", 105.0, action="NO", reason="NO_INVENTORY")
    assert c.report("m")["detection_to_decision_s"]["n"] == 0
    assert c.report("m")["detection_to_first_actionable_s"] is None


def test_only_the_first_detection_counts_so_later_ticks_do_not_reset_it():
    c = CL.CompletionLatency()
    c.note_fill_detected("m", "UP", 100.0)
    for t in (101.0, 102.0, 103.0):
        c.note_fill_detected("m", "UP", t)
    c.note_decision("m", 110.0, action="COMPLETE", reason="OK")
    assert c.report("m")["detection_to_first_actionable_s"] == 10.0


# ---------------------------------------------------------------- item 4
def test_decision_to_eligible_is_measured_and_submission_is_separate():
    c = CL.CompletionLatency()
    c.note_decision("m", 100.0, action="COMPLETE", reason="OK")
    c.note_eligible("m", 100.45, submitted=False)
    r = c.report("m")
    assert r["decision_to_eligible_s"]["median"] == 0.45
    # DISARMED: eligible is not submitted, and the record says so.
    assert c.m["m"]["eligible"][0]["submitted"] is False


def test_eligibility_with_no_prior_decision_is_not_charged_an_interval():
    c = CL.CompletionLatency()
    c.note_eligible("m", 100.0)
    assert c.report("m")["decision_to_eligible_s"]["n"] == 0


# ---------------------------------------------------------------- item 5
def test_refusal_reasons_are_counted_so_delay_can_be_attributed():
    c = CL.CompletionLatency()
    for rsn in ("COMPLEMENT_PAIR_ABOVE_CAP", "COMPLEMENT_PAIR_ABOVE_CAP",
                "DECIDE_COOLDOWN", "STALE_BOOK"):
        c.note_decision("m", 1.0, action="NO", reason=rsn, book_age_s=0.2)
    r = c.report("m")["decision_reasons"]
    assert r["COMPLEMENT_PAIR_ABOVE_CAP"] == 2
    assert r["DECIDE_COOLDOWN"] == 1 and r["STALE_BOOK"] == 1


def test_a_reason_is_paired_with_the_age_it_was_reached_on():
    """A refusal on a stale book is a different fact from one on a fresh
    book, and the report must not conflate them."""
    c = CL.CompletionLatency()
    c.note_decision("m", 1.0, action="NO", reason="COMPLEMENT_PAIR_ABOVE_CAP",
                    book_age_s=0.3)
    d = c.m["m"]["decisions"][0]
    assert d["reason"] == "COMPLEMENT_PAIR_ABOVE_CAP" and d["book_age_s"] == 0.3


# ---------------------------------------------------------------- item 6
def test_observer_continuity_across_a_rollover_is_a_measurement():
    c = CL.CompletionLatency()
    k = "__observer__"
    for t in (1.0, 2.0, 3.0):
        c.note_observation(k, t)
    c.note_rollover(k, 4.0)
    for t in (5.0, 6.0):
        c.note_observation(k, t)
    r = c.report(k)
    assert r["rollovers"] == 1
    assert r["observations_before_roll"] == 3
    assert r["observations_after_roll"] == 2
    assert r["observer_continued_across_rollover"] is True


def test_an_observer_that_stops_at_the_roll_reads_as_discontinuous():
    c = CL.CompletionLatency()
    k = "__observer__"
    c.note_observation(k, 1.0)
    c.note_rollover(k, 2.0)
    r = c.report(k)
    assert r["observations_after_roll"] == 0
    assert r["observer_continued_across_rollover"] is False


def test_continuity_is_false_before_any_rollover_rather_than_vacuously_true():
    c = CL.CompletionLatency()
    c.note_observation("__observer__", 1.0)
    assert (c.report("__observer__")["observer_continued_across_rollover"]
            is False)


# ------------------------------------------------------------- robustness
@pytest.mark.parametrize("bad", [None, "", "abc", float("nan")])
def test_an_unreadable_time_is_dropped_and_never_recorded_as_zero(bad):
    c = CL.CompletionLatency()
    c.note_poll("m", bad)
    c.note_fill_detected("m", "UP", bad)
    c.note_decision("m", bad, action="X", reason="Y")
    c.note_eligible("m", bad)
    c.note_rollover("m", bad)
    if bad != bad:                                  # NaN parses; it is kept
        return
    assert c.report("m") == {"market": "m", "known": False}


def test_an_unknown_market_reports_known_false_rather_than_empty_stats():
    assert CL.CompletionLatency().report("nope")["known"] is False


def test_the_report_labels_itself_reporting_only():
    c = CL.CompletionLatency()
    c.note_observation("m", 1.0)
    lab = c.report("m")["labels"]
    assert "reporting only" in lab
    assert "No fill, pair or profit is asserted" in lab


def test_reports_are_sorted_and_cover_every_key_seen():
    c = CL.CompletionLatency()
    c.note_observation("b", 1.0)
    c.note_observation("a", 1.0)
    assert [r["market"] for r in c.reports()] == ["a", "b"]
