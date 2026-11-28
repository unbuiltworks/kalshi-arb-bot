"""The guard that would have prevented the 2230z investigation entirely.

`src/l5_book_tape_coverage` exists because I priced a completion at a named
second from a sampler that observes the book about twice a minute. These
tests pin the two refusals that stop that, and -- more importantly -- pin
WHY the obvious guard is not sufficient.

The obvious guard is observation age, and it FAILS here. At the 2230z fill
instant the nearest tape observation was 0.27s old, inside any sane staleness
bound, and it was still wrong by twelve cents, because the REST endpoint
serves a lagged book. No measure of how old a row is can detect a row that
was stale when it was served. What does detect it is SAMPLING RESOLUTION: a
tape dark for 92.6% of its window has no business answering "what was
tradable at 22:32:10", whatever the one row nearest to that second looks
like. So `require_second_level` is the binding refusal and it defaults on.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src.l5_book_tape_coverage import (
    DARK_FRACTION_UNUSABLE, MAX_OBSERVATION_AGE_S, NOT_SECOND_LEVEL, NO_OBS,
    STALE, UNREADABLE, complement_own_px, coverage, frozen_runs,
    observation_at, price_completion_from_tape)


def row(t, bid, ask, *, bsh="100", ash="100", traded="1.0"):
    return {"t": t, "kind": "bbo", "bid": bid, "ask": ask,
            "bid_shares": bsh, "ask_shares": ash, "shares_traded": traded}


#: the real shape: two reads ~2s apart, then ~29s of silence
def sampler_tape(start=1000.0, cycles=10):
    out = []
    for i in range(cycles):
        base = start + i * 31.0
        px = "0.5000"
        out.append(row(base, px, "0.5100", traded=str(100 * i)))
        out.append(row(base + 2.0, px, "0.5100", traded=str(100 * i)))
    return out


#: what a book that can answer a second-level question looks like
def dense_tape(start=1000.0, n=60, step=1.0):
    return [row(start + i * step, "0.5000", "0.5100", traded=str(i))
            for i in range(n)]


# ----------------------------------------------------------- coverage

def test_coverage_reports_the_sampler_as_unusable_for_a_named_second():
    cov = coverage(sampler_tape())
    assert cov["observations"] == 20
    assert cov["dark_fraction"] > DARK_FRACTION_UNUSABLE
    assert cov["usable_for_second_level"] is False


def test_the_median_gap_understates_a_paired_sampler_and_dark_time_does_not():
    """Why `dark_fraction` is the binding measure and the median is not.

    This tape reads twice 2s apart and then sleeps 29s. Of its 19 gaps, 10
    are the 2s intra-pair gap, so the MEDIAN gap is 2.0s -- it looks like a
    two-second book. It is dark 93% of the time. I pin this because the
    median is the statistic I would naturally have quoted, and on the real
    2230z tape it happened to read 16.6s and so looked alarming; on a
    regularly paired sampler it would have read 2.0s and reassured me while
    the same 29-second holes were there.
    """
    cov = coverage(sampler_tape())
    assert cov["median_gap_s"] == 2.0            # looks fine
    assert cov["max_gap_s"] == 29.0              # is not
    assert cov["dark_fraction"] > 0.9
    assert cov["usable_for_second_level"] is False


def test_coverage_accepts_a_dense_tape():
    cov = coverage(dense_tape())
    assert cov["dark_s"] == 0.0
    assert cov["usable_for_second_level"] is True


def test_dark_time_counts_only_gaps_beyond_the_threshold():
    """Two 1s gaps and one 20s gap: only the 20s one is darkness."""
    rows = [row(0, "0.50", "0.51"), row(1, "0.50", "0.51"),
            row(2, "0.50", "0.51"), row(22, "0.50", "0.51")]
    cov = coverage(rows, dark_threshold_s=5.0)
    assert cov["dark_s"] == 20.0
    assert cov["max_gap_s"] == 20.0


def test_coverage_refuses_to_characterise_fewer_than_two_observations():
    cov = coverage([row(0, "0.50", "0.51")])
    assert cov["usable_for_second_level"] is False
    assert cov["median_gap_s"] is None
    assert "fewer than two" in cov["why"]


def test_coverage_tolerates_iso_timestamps_as_well_as_epoch():
    rows = [{"t": "2026-11-02T22:32:10.345771+00:00", "bid": "0.52", "ask": "0.53"},
            {"t": "2026-11-02T22:32:12.419564+00:00", "bid": "0.52", "ask": "0.53"}]
    cov = coverage(rows)
    assert cov["observations"] == 2
    assert 2.0 < cov["span_s"] < 2.2


# ------------------------------------------------------- frozen snapshots

def test_frozen_runs_detects_the_cached_endpoint_signature():
    fr = frozen_runs(sampler_tape())
    assert fr["runs"] == 10
    assert fr["longest_run"] == 2
    assert fr["fraction_in_runs"] == 1.0


def test_frozen_runs_is_quiet_on_a_moving_book():
    rows = [row(i, "0.%02d" % (40 + i), "0.%02d" % (41 + i)) for i in range(8)]
    fr = frozen_runs(rows)
    assert fr["runs"] == 0 and fr["rows_in_runs"] == 0


def test_a_repeated_row_is_flagged_on_the_observation_itself():
    rows = sampler_tape(cycles=1)
    hit = observation_at(rows, rows[1]["t"], max_age_s=5.0)
    assert hit["ok"] is True
    assert hit["frozen_duplicate"] is True
    first = observation_at(rows, rows[0]["t"], max_age_s=5.0)
    assert first["frozen_duplicate"] is False


# -------------------------------------------------- staleness as refusal

def test_a_stale_observation_is_a_named_refusal_not_a_price():
    rows = sampler_tape(cycles=1)
    hit = observation_at(rows, rows[1]["t"] + 20.0)
    assert hit["ok"] is False
    assert hit["reason"] == STALE
    assert hit["age_s"] > MAX_OBSERVATION_AGE_S


def test_it_never_reaches_forward_to_an_observation_after_the_instant():
    """An observation after the instant is not evidence about the instant."""
    rows = [row(100.0, "0.50", "0.51")]
    hit = observation_at(rows, 50.0)
    assert hit["ok"] is False
    assert hit["reason"] == NO_OBS


def test_an_unreadable_instant_is_refused():
    assert observation_at(dense_tape(), None)["reason"] == UNREADABLE


# ------------------------------------------- the binding second-level gate

def test_a_sampler_tape_is_refused_even_when_the_nearest_row_is_fresh():
    """THE LESSON OF 2230z, in one test.

    The instant asked about is 0.1s after an observation -- as fresh as it
    gets -- and the call still refuses, because the tape's resolution cannot
    support the question.
    """
    rows = sampler_tape()
    res = price_completion_from_tape(rows=rows, at_t=rows[0]["t"] + 0.1,
                                     held_side="UP", first_leg_own_px="0.47")
    assert res["ok"] is False
    assert res["reason"] == NOT_SECOND_LEVEL
    assert res["pair_own_cost"] is None
    assert res["observation"]["ok"] is True          # the row WAS fresh
    assert res["observation"]["age_s"] < 0.5


def test_the_second_level_gate_defaults_on():
    import inspect
    sig = inspect.signature(price_completion_from_tape)
    assert sig.parameters["require_second_level"].default is True


def test_the_window_level_question_can_still_be_asked_explicitly():
    rows = sampler_tape()
    res = price_completion_from_tape(rows=rows, at_t=rows[0]["t"] + 0.1,
                                     held_side="UP", first_leg_own_px="0.47",
                                     require_second_level=False)
    assert res["ok"] is True
    assert res["pair_own_cost"] == "0.9600"          # 0.47 + (1 - 0.51)


def test_a_dense_tape_prices_the_completion_normally():
    rows = dense_tape()
    res = price_completion_from_tape(rows=rows, at_t=rows[10]["t"] + 0.2,
                                     held_side="UP", first_leg_own_px="0.47")
    assert res["ok"] is True
    assert res["coverage"]["usable_for_second_level"] is True
    assert res["pair_own_cost"] == "0.9600"


def test_a_dense_but_stale_instant_still_refuses_on_age():
    rows = dense_tape(n=5)
    res = price_completion_from_tape(rows=rows, at_t=rows[-1]["t"] + 60.0,
                                     held_side="UP", first_leg_own_px="0.47")
    assert res["ok"] is False
    assert res["reason"] == STALE


# ---------------------------------------- the inversion, named not inferred

def test_the_complement_inversion_is_right_for_both_held_sides():
    r = row(0, "0.4000", "0.4200")
    # holding UP, the complement is DOWN: join the UP ask, cross the UP bid
    assert complement_own_px(r, "UP", mode="maker") == D("0.5800")
    assert complement_own_px(r, "UP", mode="taker") == D("0.6000")
    # holding DOWN, the complement is UP: join the bid, cross the ask
    assert complement_own_px(r, "DOWN", mode="maker") == D("0.4000")
    assert complement_own_px(r, "DOWN", mode="taker") == D("0.4200")


def test_the_maker_complement_is_never_dearer_than_the_taker_one():
    r = row(0, "0.3600", "0.3700")
    for held in ("UP", "DOWN"):
        assert (complement_own_px(r, held, mode="maker")
                <= complement_own_px(r, held, mode="taker"))


def test_the_real_2230z_numbers_reproduce_from_the_authenticated_book():
    """UP 0.4100/0.4200 at t+131.85 gave DOWN 0.5800 own and a 1.05 pair."""
    r = row(0, "0.4100", "0.4200")
    comp = complement_own_px(r, "UP", mode="maker")
    assert comp == D("0.5800")
    assert D("0.4700") + comp == D("1.0500")


def test_the_tape_reading_that_misled_me_reproduces_too():
    """And it is 0.94 -- so the arithmetic was never the problem."""
    r = row(0, "0.5200", "0.5300")
    assert D("0.4700") + complement_own_px(r, "UP", mode="maker") == D("0.9400")
