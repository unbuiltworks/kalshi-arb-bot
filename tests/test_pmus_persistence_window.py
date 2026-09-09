"""Tests for the 30-60s persistence-window observation.

The same three silent-failure shapes are guarded here: a truncated window
must not read as a result, the inputs must actually reach the computation,
and the output must not be a degenerate function of one input.
"""

from decimal import Decimal as D

import pytest

from src.pmus_persistence_window import (BUCKETS, WINDOW_S, bucket_of, cohort,
                                         complement_price_now, observe,
                                         open_window, summarise,
                                         touch_reached)


def book(bid="0.40", ask="0.42"):
    return {"bid": D(bid), "ask": D(ask)}


def win(px="0.58", side="NO"):
    return open_window(0.0, D(px), side, "m")


# ------------------------------ buckets ------------------------------

def test_buckets_partition_the_window_without_overlap():
    assert bucket_of(0.0) == "a_0_30s"
    assert bucket_of(29.9) == "a_0_30s"
    assert bucket_of(30.0) == "b_30_60s"      # boundary belongs to the NEXT
    assert bucket_of(59.9) == "b_30_60s"
    assert bucket_of(60.0) == "c_60_120s"
    assert bucket_of(119.9) == "c_60_120s"
    assert bucket_of(120.0) is None


def test_the_window_closes_and_stops_recording():
    w = observe(win(), book(), WINDOW_S + 1, True, D(5))
    assert w["closed"] is True
    assert sum(b["observations"] for b in w["buckets"].values()) == 0


# --- an opportunity needs BOTH the touch and a print ---

def test_touch_alone_is_not_an_opportunity():
    """No assumed fills: a price nobody traded at is not an opportunity."""
    w = observe(win("0.58", "NO"), book("0.42", "0.60"), 10.0,
                printed=False, shares=D(0))
    assert w["buckets"]["a_0_30s"]["opportunities"] == 0


def test_print_alone_is_not_an_opportunity():
    """A print that never came to our price would not have hit us."""
    w = observe(win("0.58", "NO"), book("0.30", "0.35"), 10.0,
                printed=True, shares=D(5))
    assert w["buckets"]["a_0_30s"]["prints"] == 1
    assert w["buckets"]["a_0_30s"]["opportunities"] == 0


def test_touch_and_print_together_make_one_opportunity():
    w = observe(win("0.58", "NO"), book("0.45", "0.60"), 10.0,
                printed=True, shares=D(5))
    assert w["buckets"]["a_0_30s"]["opportunities"] == 1


def test_touch_reached_uses_the_complement_side_not_always_one_minus_ask():
    """The bug already shipped once in the shadow: assuming the complement is
    always NO. A NO complement is reachable at 1-bid; a YES complement at the
    ask."""
    b = book("0.45", "0.60")
    assert touch_reached(b, "NO", D("0.58")) is True     # 1-0.45 = 0.55 <= 0.58
    assert touch_reached(b, "YES", D("0.58")) is False   # ask 0.60 > 0.58
    assert touch_reached(b, "YES", D("0.61")) is True


def test_complement_price_follows_the_side():
    assert complement_price_now(book("0.40", "0.42"), "NO") == D("0.58")
    assert complement_price_now(book("0.40", "0.42"), "YES") == D("0.40")


# --- GUARD: the answer must depend on WHEN things happened ---

def test_opportunities_land_in_the_bucket_they_occurred_in():
    """Goes red if elapsed time stops reaching the bucketing -- the failure
    that made a 4-interval window look like a complete result."""
    w = win("0.58", "NO")
    for t in (5.0, 40.0, 45.0, 90.0):
        w = observe(w, book("0.45", "0.60"), t, True, D(1))
    s = summarise(w)
    assert s["buckets"]["a_0_30s"]["opportunities"] == 1
    assert s["buckets"]["b_30_60s"]["opportunities"] == 2
    assert s["buckets"]["c_60_120s"]["opportunities"] == 1
    assert s["opportunities_inside_our_old_deadline"] == 1
    assert s["opportunities_we_never_waited_for"] == 3
    assert s["share_beyond_30s"] == 0.75


def test_a_window_with_no_opportunity_reports_none_not_zero_share():
    s = summarise(win())
    assert s["share_beyond_30s"] is None


# --- deterioration is signed and measured from entry ---

def test_a_cheaper_complement_reads_as_negative_deterioration():
    w = observe(win("0.58", "NO"), book("0.50", "0.55"), 10.0, True, D(1))
    b = summarise(w)["buckets"]["a_0_30s"]
    assert b["best_complement_px"] == "0.45"          # 1 - 0.55
    assert b["deterioration_vs_entry"] == "-0.13"


def test_best_complement_price_keeps_the_best_seen_not_the_last():
    w = win("0.58", "NO")
    w = observe(w, book("0.50", "0.55"), 5.0, True, D(1))    # 0.45
    w = observe(w, book("0.30", "0.70"), 9.0, True, D(1))    # 0.30, better
    w = observe(w, book("0.50", "0.60"), 12.0, True, D(1))   # 0.40, worse
    assert summarise(w)["buckets"]["a_0_30s"]["best_complement_px"] == "0.30"


# --- GUARD: observation must not be degenerate with a single input ---

def test_two_windows_with_identical_books_but_different_timing_differ():
    """If the output collapsed to a function of the book alone, these would
    be identical -- the shape that made pair cost exactly 1 - spread."""
    early, late = win("0.58", "NO"), win("0.58", "NO")
    for t in (5.0, 10.0):
        early = observe(early, book("0.45", "0.60"), t, True, D(1))
    for t in (40.0, 70.0):
        late = observe(late, book("0.45", "0.60"), t, True, D(1))
    a, b = summarise(early), summarise(late)
    assert a["opportunities_we_never_waited_for"] == 0
    assert b["opportunities_we_never_waited_for"] == 2
    assert a["share_beyond_30s"] != b["share_beyond_30s"]


def test_flow_is_accumulated_per_bucket():
    w = win()
    w = observe(w, book(), 5.0, True, D("3"))
    w = observe(w, book(), 45.0, True, D("7"))
    s = summarise(w)
    assert s["buckets"]["a_0_30s"]["shares"] == "3"
    assert s["buckets"]["b_30_60s"]["shares"] == "7"


# ------------------------------ cohort ------------------------------

def test_cohort_totals_the_comparison_the_owner_asked_for():
    ws = []
    for _ in range(3):
        w = win("0.58", "NO")
        w = observe(w, book("0.45", "0.60"), 10.0, True, D(1))
        w = observe(w, book("0.45", "0.60"), 45.0, True, D(1))
        ws.append(summarise(w))
    c = cohort(ws)
    assert c["windows"] == 3
    assert c["opportunities_inside_our_old_deadline"] == 3
    assert c["opportunities_we_never_waited_for"] == 3
    assert c["share_beyond_30s"] == 0.5
    assert c["our_historical_max_rest_s"] == 32.2
    assert c["l5_median_complement_gap_s"] == 47.0


def test_an_empty_cohort_does_not_invent_a_share():
    assert cohort([])["share_beyond_30s"] is None
