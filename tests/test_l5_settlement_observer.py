"""The read-only settlement observation, and what it refuses to conclude.

THE GAP IT FILLS, measured rather than asserted. The paired-entry
economics need P(UP settles | one leg filled alone) -- the adverse
selection that decided the 2026-11-09 15:30Z loss -- and the entry turns
positive-to-negative at a skew of 0.0871, so the estimate must resolve to
about +/-0.09. Of ten market windows in the replay sources the settled side
is determinable in SIX, because four tapes end between 31% and 59% of the
way through the window. Six observations put a 50% estimate at 0.19..0.81.

So the module records one line per window and, deliberately, computes NO
probability from it. These tests pin both halves: the observation is
correct, and the refusal to over-read it is explicit.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_settlement_observer as SO

WS = "2026-11-09T15:30:00+00:00"


def at(offset_s, bid, ask, **kw):
    import datetime as dt
    start = dt.datetime.fromisoformat(WS)
    obs = start + dt.timedelta(seconds=offset_s)
    return SO.observe(market="cpc-btc-updown-15m-2026-11-09-1530z",
                      window_start_utc=WS, observed_utc=obs.isoformat(),
                      bid=bid, ask=ask, **kw)


# =====================================================================
# WHAT IT DETERMINES
# =====================================================================

def test_a_late_extreme_book_at_the_top_settles_UP():
    """The actual 15:30Z window: the bid ran to 0.98 and UP settled."""
    r = at(808, "0.98", "0.99")
    assert r["settled_side"] == "UP"
    assert r["reason"] is None
    assert r["implied_other_side"] == "0.02"


def test_a_late_extreme_book_at_the_bottom_settles_DOWN():
    r = at(846, "0.01", "0.02")
    assert r["settled_side"] == "DOWN"
    assert r["implied_other_side"] == "0.02"


def test_the_residual_error_of_the_proxy_is_always_reported():
    """A terminal book is a probability, not an outcome, and the leftover
    is carried on the record so a calibration can weight or exclude it."""
    for off, bid, ask, want in ((808, "0.97", "0.98", "0.03"),
                                (808, "0.99", "1.00", "0.01"),
                                (808, "0.00", "0.01", "0.01"),
                                (808, "0.02", "0.03", "0.03")):
        r = at(off, bid, ask)
        assert r["settled_side"] in ("UP", "DOWN"), (bid, ask)
        assert r["implied_other_side"] == want, (bid, ask, r)


def test_coverage_is_reported_so_an_early_tape_is_visible():
    assert at(278, "0.14", "0.15")["coverage"] == pytest.approx(0.3089, abs=1e-4)
    assert at(846, "0.01", "0.02")["coverage"] == pytest.approx(0.94, abs=1e-4)


# =====================================================================
# WHAT IT REFUSES, AND WHY EACH REFUSAL MATTERS
# =====================================================================

@pytest.mark.parametrize("off", [0, 100, 278, 441, 510, 533, 699])
def test_a_tape_that_ENDS_EARLY_determines_nothing(off):
    """THE COMMON CASE and the reason the module exists. At 59% of the
    window the price still has time to cross, so the book states a
    probability and not an outcome."""
    r = at(off, "0.01", "0.02")
    assert r["settled_side"] == "UNDETERMINED"
    assert r["reason"] == "TAPE_ENDS_EARLY"
    assert r["implied_other_side"] is None


def test_a_late_but_MIDDLING_book_determines_nothing():
    r = at(808, "0.54", "0.55")
    assert r["settled_side"] == "UNDETERMINED"
    assert r["reason"] == "TERMINAL_BOOK_NOT_EXTREME"


@pytest.mark.parametrize("bid,ask,reason", [
    ("x", "0.55", "UNREADABLE_BOOK"),
    (None, "0.55", "UNREADABLE_BOOK"),
    ("0.55", "nan", "UNREADABLE_BOOK"),
    ("1.5", "1.6", "BOOK_NOT_SANE"),
    ("-0.1", "0.2", "BOOK_NOT_SANE"),
    ("0.60", "0.50", "BOOK_NOT_SANE")])
def test_an_unreadable_or_crossed_book_refuses(bid, ask, reason):
    r = at(808, bid, ask)
    assert r["settled_side"] == "UNDETERMINED"
    assert r["reason"] == reason


def test_a_missing_market_refuses():
    r = SO.observe(market="", window_start_utc=WS, observed_utc=WS,
                   bid="0.5", ask="0.5")
    assert r["reason"] == "NO_MARKET"


@pytest.mark.parametrize("ws,ob", [
    ("not-a-time", WS), (WS, "not-a-time"),
    ("2026-11-09T15:30:00", WS), (WS, "2026-11-09T15:43:00")])
def test_unreadable_or_naive_times_refuse(ws, ob):
    r = SO.observe(market="m", window_start_utc=ws, observed_utc=ob,
                   bid="0.98", ask="0.99")
    assert r["settled_side"] == "UNDETERMINED"
    assert r["reason"] in ("UNREADABLE_TIMES", "NAIVE_TIME")


def test_an_observation_BEFORE_the_window_refuses():
    r = at(-60, "0.98", "0.99")
    assert r["reason"] == "OBSERVED_BEFORE_WINDOW"


def test_it_never_raises_on_any_input():
    """It runs inside close_window, so raising would interfere with
    closing a window."""
    for kw in ({}, {"market": None}, {"market": 7, "bid": object()},
               {"market": "m", "window_start_utc": [], "observed_utc": {}}):
        base = dict(market="m", window_start_utc=WS, observed_utc=WS,
                    bid="0.5", ask="0.5")
        base.update(kw)
        r = SO.observe(**base)
        assert r["settled_side"] == "UNDETERMINED" or r["reason"] is None


# =====================================================================
# IT COMPUTES NO PROBABILITY, AND THAT IS THE POINT
# =====================================================================

def test_the_summary_counts_and_does_NOT_divide():
    recs = [at(808, "0.98", "0.99"), at(846, "0.01", "0.02"),
            at(278, "0.14", "0.15"), at(808, "0.54", "0.55")]
    out = SO.summarise(recs)
    assert out == {**out, "observed": 4, "determined": 2, "up": 1, "down": 1}
    assert out["undetermined_by_reason"] == {"TAPE_ENDS_EARLY": 1,
                                             "TERMINAL_BOOK_NOT_EXTREME": 1}
    for key in out:
        assert "probability" not in key and "rate" not in key, key


def test_six_observations_are_reported_INSUFFICIENT():
    """The number we actually have. It must not read as calibrated."""
    recs = [at(808, "0.98", "0.99") for _ in range(6)]
    assert SO.summarise(recs)["determined"] == 6
    assert SO.summarise(recs)["sufficient_for_entry_calibration"] is False


def test_the_sufficiency_bound_is_the_one_the_entry_threshold_implies():
    """~400 observations resolve a proportion to about +/-0.049; 100 give
    +/-0.096, which still cannot separate 'enter' from 'do not enter' at a
    break-even skew of 0.0871."""
    assert SO.summarise([at(808, "0.98", "0.99")
                         for _ in range(399)])[
        "sufficient_for_entry_calibration"] is False
    assert SO.summarise([at(808, "0.98", "0.99")
                         for _ in range(400)])[
        "sufficient_for_entry_calibration"] is True


def test_a_non_mapping_record_is_ignored_rather_than_counted():
    out = SO.summarise([at(808, "0.98", "0.99"), None, "junk", 7])
    assert out["observed"] == 1 and out["determined"] == 1
