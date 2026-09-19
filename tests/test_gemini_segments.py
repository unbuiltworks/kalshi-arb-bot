"""Segmentation: the census is the deliverable, the rate is the refusal.

Four fills exist. Nine dimensions were requested. These tests are mostly about
what this module declines to say about that ratio.
"""
import pytest

from src.gemini_execution import segments as SG


def _cycle(utc, *, tte=200.0, symbol="S", readable=True, yes_spread="0.20",
           yes_bid="0.30", yes_ask="0.50", best_qty="50", prints=3,
           actions=(("OPEN", "YES"),), touch_ours=False, why=None):
    return {
        "kind": "cycle", "utc": utc, "symbol": symbol, "tte_s": tte,
        "l5_action": [{"action": a, "leg": l, "price": "0.30", "qty": "0.01"}
                      for a, l in actions],
        "habitat": {
            "utc": utc, "tte_s": tte, "book_readable": readable,
            "book_why": why,
            "yes_bid": yes_bid, "yes_ask": yes_ask, "yes_spread": yes_spread,
            "no_bid": "0.50", "no_ask": "0.70",
            "ladder": {"readable": True,
                       "bids": {"best": yes_bid, "best_qty": best_qty,
                                "n_levels": 3, "touch_is_ours": touch_ours}},
            "flow": {"readable": True, "n_prints": prints},
        },
    }


# ============================================================ the buckets

@pytest.mark.parametrize("x,want", [
    ("0.01", "<=0.05"), ("0.05", "<=0.05"), ("0.07", "0.06-0.10"),
    ("0.20", "0.11-0.20"), ("0.33", ">0.30"), (None, SG.UNREADABLE),
    ("not a number", SG.UNREADABLE)])
def test_spread_buckets(x, want):
    assert SG.spread_bucket(x) == want


@pytest.mark.parametrize("x,want", [
    ("0.31", "0.26-0.50"), ("0.05", "0.00-0.10"), ("0.95", "0.91-1.00"),
    ("0.90", "0.76-0.90")])
def test_probability_buckets(x, want):
    assert SG.probability_bucket(x) == want


@pytest.mark.parametrize("x,want", [
    (271.0, ">240s"), (200.0, "180-240s"), (59.0, "<=60s"), (None,
                                                             SG.UNREADABLE)])
def test_tte_buckets(x, want):
    assert SG.tte_bucket(x) == want


# ======================================================== refused books

def test_a_refused_book_lands_in_NO_price_derived_segment():
    """THE RULE THIS MODULE EXISTS FOR. A crossed book read YES bid 0.95 /
    ask 0.65 during soak #1 -- a -0.30 'spread'. Bucketing that as `<=0.05`
    would report the venue as the tightest it has ever been using exactly the
    reading the controller threw away."""
    seg = SG.cycle_segments(_cycle("2026-09-19T04:00:00+00:00",
                                   readable=False, yes_spread="-0.30",
                                   why="crossed book"))
    assert seg["spread"] == SG.UNREADABLE
    assert seg["probability_band"] == SG.UNREADABLE
    assert seg["touch_depth"] == SG.UNREADABLE
    # but the cycle is still counted, and its reason is kept
    assert seg["book_readable"] is False and seg["book_why"] == "crossed book"


def test_refused_cycles_are_counted_on_their_own_line():
    rows = [_cycle("2026-09-19T04:00:0%d+00:00" % i,
                   readable=(i % 2 == 0), why=None if i % 2 == 0 else "crossed")
            for i in range(4)]
    c = SG.census(rows)
    assert c["cycles"] == 4 and c["book_refused_cycles"] == 2
    assert c["book_refused_share"] == 0.5
    assert c["refusal_reasons"] == ["crossed"]


# ============================================================== the rate

def test_NO_SEGMENT_STATES_A_RATE_ON_FOUR_FILLS():
    """The whole point. Nine dimensions over four fills would print a table of
    percentages that reads as knowledge and is noise."""
    rows = [_cycle("2026-09-19T04:00:0%d+00:00" % i) for i in range(9)]
    c = SG.census(rows, fill_segments=[{"spread": "0.11-0.20",
                                        "tte": ">240s"}] * 4)
    assert c["rates_stated"] is False
    assert c["fills_total"] == 4
    for dim in c["dimensions"].values():
        for cell in dim.values():
            assert cell["rate"] is None
            assert "NO CONVERSION RATE IS STATED" in cell["rate_why"]
            assert "4 fills exist" in cell["rate_why"]


def test_the_fill_column_is_still_reported_as_counts():
    rows = [_cycle("2026-09-19T04:00:0%d+00:00" % i) for i in range(3)]
    c = SG.census(rows, fill_segments=[{"spread": "0.11-0.20"}])
    assert c["dimensions"]["spread"]["0.11-0.20"]["fills"] == 1
    assert c["dimensions"]["spread"]["0.11-0.20"]["cycles"] == 3


def test_a_fill_whose_decision_state_was_never_recorded_is_still_shown():
    """C4 and C6 filled before book-state capture existed. Dropping those
    fills would make the record look like the only fills we have are the ones
    we happen to have books for."""
    rows = [_cycle("2026-09-19T04:00:00+00:00")]
    c = SG.census(rows, fill_segments=[{"spread": SG.NOT_PRESENTED}])
    assert c["dimensions"]["spread"][SG.NOT_PRESENTED]["fills"] == 1
    assert c["dimensions"]["spread"][SG.NOT_PRESENTED]["cycles"] == 0


# =============================================================== the flow

def test_flow_is_measured_between_two_reads_not_assumed_from_a_duration():
    a = _cycle("2026-09-19T04:00:00+00:00", prints=3)
    b = _cycle("2026-09-19T04:00:30+00:00", prints=6)
    # 3 new prints in 30 s == 6/min
    assert SG.flow_rates([a, b]) == [None, "6.000"]


def test_the_first_cycle_of_a_symbol_has_no_rate_and_that_is_not_zero():
    a = _cycle("2026-09-19T04:00:00+00:00", symbol="A", prints=3)
    b = _cycle("2026-09-19T04:00:30+00:00", symbol="B", prints=9)
    assert SG.flow_rates([a, b]) == [None, None]


def test_a_truncated_tape_is_unreadable_not_a_rate():
    a = _cycle("2026-09-19T04:00:00+00:00", prints=SG.FLOW_TAPE_LIMIT)
    b = _cycle("2026-09-19T04:00:30+00:00", prints=SG.FLOW_TAPE_LIMIT)
    assert SG.flow_rates([a, b]) == [None, None]


def test_a_print_count_going_backwards_is_refused():
    a = _cycle("2026-09-19T04:00:00+00:00", prints=9)
    b = _cycle("2026-09-19T04:00:30+00:00", prints=3)
    assert SG.flow_rates([a, b])[1] is None


# ============================================================== the rungs

_MUT = [
    {"kind": "PLACE", "utc": "2026-09-19T01:35:30.757+00:00",
     "target": {"leg": "YES", "price": "0.31", "action": "OPEN"}},
    {"kind": "PLACE", "utc": "2026-09-19T01:35:30.771+00:00",
     "target": {"leg": "NO", "price": "0.42", "action": "OPEN"}},
    {"kind": "CANCEL", "utc": "2026-09-19T01:35:43.084+00:00",
     "target": {"leg": "NO", "price": "0.42"}},
    {"kind": "PLACE", "utc": "2026-09-19T01:35:44.181+00:00",
     "target": {"leg": "NO", "price": "0.57", "action": "OPEN"}},
    {"kind": "CANCEL", "utc": "2026-09-19T01:35:50.596+00:00",
     "target": {"leg": "NO", "price": "0.57"}},
]


def test_resting_seconds_come_from_PLACE_to_CANCEL():
    rr = SG.rung_intervals(_MUT, window_end="2026-09-19T01:36:39.447+00:00",
                           filled_prices=["0.31"])
    by = {r["price"]: r for r in rr}
    assert by["0.42"]["resting_s"] == 12.313
    assert by["0.57"]["resting_s"] == 6.415
    assert by["0.42"]["ended"] == "CANCEL"


def test_a_rung_still_live_at_the_stop_is_a_FLOOR_not_a_measurement():
    rr = SG.rung_intervals(_MUT, window_end="2026-09-19T01:36:39.447+00:00",
                           filled_prices=["0.31"])
    yes = [r for r in rr if r["leg"] == "YES"][0]
    assert yes["ended"] == "AT_STOP"
    assert yes["filled"] is True


def test_reprices_are_counted_per_leg():
    rr = SG.rung_intervals(_MUT, window_end="2026-09-19T01:36:39.447+00:00")
    by = {r["price"]: r for r in rr}
    # the NO leg was placed twice: 0.42 then 0.57 -- one reprice
    assert by["0.42"]["reprices_on_this_leg"] == 1
    assert by["0.42"]["places_on_this_leg"] == 2
    assert by["0.31"]["reprices_on_this_leg"] == 0


def test_a_TERMINAL_CANCEL_IS_A_WITHDRAWAL_NOT_A_REPRICE():
    """C7's YES rung was placed once at 0.01, left alone for 72 s and pulled
    at the end of the window. Counting that final cancel as a reprice made the
    one rung that most clearly WAS left alone read as repriced, and put the
    only counterexample to 'leave it alone and it fills' on the wrong side of
    the table."""
    muts = [
        {"kind": "PLACE", "utc": "2026-09-18T23:27:57.267+00:00",
         "target": {"leg": "YES", "price": "0.01", "action": "OPEN"}},
        {"kind": "CANCEL", "utc": "2026-09-18T23:29:09.378+00:00",
         "target": {"leg": "YES", "price": "0.01"}},
    ]
    rr = SG.rung_intervals(muts, window_end="2026-09-18T23:29:40+00:00")
    assert rr[0]["reprices_on_this_leg"] == 0
    assert rr[0]["resting_s"] == 72.111
    assert rr[0]["filled"] is False        # 72 s resting, and still no fill


def test_the_repricing_finding_carries_its_own_confound():
    """It is the most decision-relevant association in the record and the one
    most likely to be misread as a tuning instruction."""
    rr = SG.rung_intervals(_MUT, window_end="2026-09-19T01:36:39.447+00:00",
                           filled_prices=["0.31"])
    c = SG.census([_cycle("2026-09-19T04:00:00+00:00")], rungs=rr)
    rep = c["repricing"]
    assert rep["fills_never_repriced"] == 1
    assert rep["fills_on_a_repriced_leg"] == 0
    assert "ASSOCIATION, NOT CAUSE" in rep["caution"]


# ===================================================== adverse movement

def test_adverse_movement_is_marked_not_realized():
    later = [_cycle("2026-09-19T01:36:40+00:00", yes_bid="0.20",
                    yes_ask="0.30")]
    got = SG.adverse_movement(leg="YES", fill_price="0.31", later=later)
    assert got["readable"] is True
    assert got["samples"][0]["mid"] == "0.25"
    assert got["samples"][0]["vs_fill"] == "-0.06"
    assert "Not realized" in got["labels"]


def test_a_refused_book_after_a_fill_is_not_an_unchanged_mid():
    later = [_cycle("2026-09-19T01:36:40+00:00", readable=False)]
    got = SG.adverse_movement(leg="YES", fill_price="0.31", later=later)
    assert got["readable"] is False
    assert got["samples"][0]["mid"] is None
    assert "no cycle after the fill" in got["why"]


# ================================================= our own size, flagged

def test_our_own_touch_is_flagged_and_never_netted_away():
    """In C8B the bid at our price read 0.01 for seven cycles -- our own order,
    alone. Reporting that as market depth, or silently removing it, are two
    different ways of being wrong."""
    rows = [_cycle("2026-09-19T04:00:00+00:00", best_qty="0.01",
                   touch_ours=True)]
    c = SG.census(rows)
    assert c["touch_was_ours_cycles"] == 1
    assert c["dimensions"]["touch_depth"]["<=1"]["cycles"] == 1


def test_l5_state_segments_by_the_action_the_policy_chose():
    rows = [_cycle("2026-09-19T04:00:00+00:00",
                   actions=(("EXTEND", "YES"), ("COMPLETE", "NO")))]
    c = SG.census(rows)
    assert "COMPLETE+EXTEND" in c["dimensions"]["l5_state"]


def test_a_cycle_with_no_action_is_NO_ACTION_not_missing():
    rows = [_cycle("2026-09-19T04:00:00+00:00", actions=())]
    c = SG.census(rows)
    assert c["dimensions"]["l5_state"]["NO_ACTION"]["cycles"] == 1


def test_a_rung_that_FILLED_stops_resting_at_the_FILL():
    """Both fills on record were still live at the window's stop, so measuring
    them to the stop overstated the only two resting times that have a fill
    attached. C8B's rung rested 57.8 s to its fill, not 68.7 s to the stop."""
    muts = [{"kind": "PLACE", "utc": "2026-09-19T01:35:30.748+00:00",
             "target": {"leg": "YES", "price": "0.31", "action": "OPEN"}}]
    rr = SG.rung_intervals(muts, window_end="2026-09-19T01:36:39.447+00:00",
                           filled_prices=["0.31"],
                           filled_utc={"0.31": "2026-09-19T01:36:28.553Z"})
    assert rr[0]["ended"] == "FILL"
    assert rr[0]["resting_s"] == 57.805
    assert rr[0]["resting_bucket"] == ">60s" or rr[0]["resting_s"] < 60
