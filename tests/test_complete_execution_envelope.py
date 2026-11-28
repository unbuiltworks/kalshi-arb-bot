"""P0-B regressions: the COMPLETE execution-envelope forensics.

The headline result -- "zero of 137 attempts had a legal AND sufficient one-tick
improvement" -- is only trustworthy if the tick size and the censoring are
right. Both were wrong in my first pass, and both are pinned here.
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1]
       / "analysis/l5/p0b/complete_execution_envelope.py")
spec = importlib.util.spec_from_file_location("cee", SRC)
M = importlib.util.module_from_spec(spec)
sys.modules["cee"] = M
spec.loader.exec_module(M)


def test_the_venue_tick_is_half_a_cent_not_a_cent():
    """orderPriceMinTickSize reports 0.01, but 98 of 137 live COMPLETE prices
    are half-cent and the venue range is 0.005..0.995."""
    assert M.TICK == D("0.005")


def test_a_one_tick_gap_is_never_reported_as_no_gap():
    """THE BUG THIS EXISTS FOR. With TICK=0.01 a $0.005 gap divides to 0.5 and
    int()-truncates to ZERO, which reported seven orders as 'the book reached
    us and we still did not fill' -- a queue/latency finding -- when the book
    had in fact stopped exactly one tick short every time."""
    assert M._ticks(D("0.5600"), D("0.555")) == 1
    assert M._ticks(D("0.5250"), D("0.520")) == 1
    assert M._ticks(D("0.555"), D("0.555")) == 0


def test_sub_tick_gaps_round_away_from_zero_not_toward_it():
    assert M._ticks(D("0.5570"), D("0.555")) >= 1     # 0.002 is still a gap
    assert M._ticks(D("0.5530"), D("0.555")) <= -1    # and so is -0.002


def test_book_reached_us_requires_a_gap_of_zero_or_less():
    assert M.bucket({"book_gap_ticks": 0}) == "BOOK_REACHED_US"
    assert M.bucket({"book_gap_ticks": 1}) == "1_TICK_SHORT"
    assert M.bucket({"book_gap_ticks": 2}) == "2_TICKS_SHORT"
    assert M.bucket({"book_gap_ticks": 3}) == "3+_TICKS_SHORT"
    assert M.bucket({"book_gap_ticks": None}) == "unknown"


def _row(px, ask, avg, min_opp):
    return {"px": D(px), "fresh_opp_ask": D(ask), "yes_ask": D(ask),
            "held_avg_cost": D(avg), "min_opp_ask": D(min_opp),
            "book_gap_ticks": M._ticks(D(min_opp), D(px))}


def test_one_more_tick_that_would_cross_is_not_post_only():
    """The finding that decided P0-B: in every near-miss our price was ALREADY
    at L5's touch and the ask sat exactly one tick above, so +1 tick IS the ask
    -- marketable, not a resting maker order. The maker envelope had no room
    left to give."""
    cf = M.one_tick_counterfactual(_row("0.555", "0.560", "0.4077", "0.5600"))
    assert cf["book_would_have_reached"] is True
    assert cf["pair_within_ceiling"] is True
    assert cf["fee_safe"] is True
    assert cf["still_post_only"] is False, (
        "+1 tick lands exactly on the ask; calling that post-only would "
        "manufacture a maker fill that the venue would have rejected or crossed")


def test_a_genuine_maker_improvement_is_recognised():
    """The test must not be rigged to always say no: with a wider spread, one
    more tick is legal, sufficient and still resting."""
    cf = M.one_tick_counterfactual(_row("0.500", "0.560", "0.4000", "0.5050"))
    assert cf["still_post_only"] is True
    assert cf["book_would_have_reached"] is True
    assert cf["pair_within_ceiling"] is True


def test_pair_ceiling_blocks_an_otherwise_available_tick():
    cf = M.one_tick_counterfactual(_row("0.520", "0.600", "0.4950", "0.5250"))
    assert cf["book_would_have_reached"] is True
    assert cf["still_post_only"] is True
    assert cf["pair_within_ceiling"] is False        # 0.495 + 0.525 = 1.020


def test_censoring_excludes_orders_that_never_rested_to_the_horizon():
    """An order cancelled at 2 s says nothing about 60 s. Counting it as a
    failure would understate fill probability and make the execution policy
    look worse than the tape supports."""
    rows = [{"filled": False, "rest_s": 2.0}, {"filled": True, "rest_s": 1.0}]
    p = M.fill_probabilities(rows, horizons=(60.0,))
    assert p["p_fill_within_60s"]["at_risk"] == 1
    assert p["p_fill_within_60s"]["censored_before_horizon"] == 1


def test_p_fill_before_cancel_uses_every_attempt():
    rows = [{"filled": False, "rest_s": 2.0}] * 9 + [{"filled": True, "rest_s": 1.0}]
    p = M.fill_probabilities(rows)["p_fill_before_cancel"]
    assert p["attempts"] == 10 and p["filled"] == 1 and p["p"] == 0.1
