"""The 120-second completion tail: what it buys, and what it must not cost.

THE DEFECT (owner 2026-11-03). `may_complete` is bounded by
`min(complete_cutoff_s(), cancel_cutoff(t_end))`. With the override
`L5_TS_CANCEL_MARGIN_S=300` the terminal landed on T+600 -- exactly
`ENTRY_CUTOFF_S` -- so `COMPLETE_CUTOFF_S = 720` was dead configuration and
the engine stopped REDUCING risk at the same instant it stopped TAKING it.
The live tape showed the signature: PAST_TERMINAL from T+601 to T+714, and
PAST_COMPLETE_CUTOFF only from T+725.

THE CORRECTION is to stop widening the margin, so the code floor of 180
governs. The terminal becomes T+720, the configured completion cutoff is
reached, and completion outlives opening by 120s.

WHAT IT DOES NOT CHANGE. Terminal safety is carried by `ORDER_TTL_S = 120`,
which bounds every order's rest per-order, so the oldest an order can get is
120s against the watchdog's 300s overdue threshold. The margin was never what
kept orders young once the TTL existed; it was only truncating the window.

WHAT IS NOT CLAIMED. On the four venue-confirmed fills of 2026-11-02 the tail
recovers NOTHING: those fills landed at T+21..T+130 and the two reachable
completions were at T+132 and T+220, both already inside T+600. The tail
matters for a LATE fill, a state that campaign never produced.
"""
from __future__ import annotations

import pytest

from src import l5_two_sided_probe as TS

WINDOW = 900.0
WS = 1_792_468_800.0
WE = WS + WINDOW


@pytest.fixture(autouse=True)
def code_floor_margin(monkeypatch):
    """The corrected configuration: no override, so the floor of 180 governs."""
    monkeypatch.delenv(TS.CANCEL_MARGIN_ENV, raising=False)
    yield


# ------------------------------------------------- the tail exists at all

def test_the_terminal_is_T_plus_720_and_the_tail_is_120s():
    assert TS.cancel_margin_s() == 180.0
    assert TS.cancel_cutoff(WE) == WS + 720.0
    assert TS.entry_cutoff_s() == 600.0
    assert TS.complete_cutoff_s() == 720.0
    d = TS.binding_completion_deadline_s(WINDOW)
    assert d["binding_completion_deadline_s"] == 720.0
    assert d["collapsed_onto_entry_cutoff"] is False
    assert d["completion_outlives_opening_s"] == 120.0


def test_completion_is_allowed_through_the_tail_where_opening_is_not():
    for t in (601.0, 650.0, 700.0, 719.0):
        assert TS.may_complete(WS + t, WS, WE) is None, t
        assert TS.may_enter(WS + t, WS, WE) == "PAST_ENTRY_CUTOFF", t


# ------------------------------------------------- the late fill, the point

def test_a_LATE_fill_still_has_a_completion_window():
    """A leg filling at T+550 had 50s under the collapse and has 170s now."""
    fill = 550.0
    old_terminal, new_terminal = 600.0, 720.0
    assert TS.may_complete(WS + fill, WS, WE) is None
    assert new_terminal - fill == 170.0
    assert (new_terminal - fill) - (old_terminal - fill) == 120.0


def test_a_fill_inside_the_tail_can_still_be_completed():
    """Filled at T+610 -- past entry, inside the tail."""
    assert TS.may_enter(WS + 610.0, WS, WE) == "PAST_ENTRY_CUTOFF"
    assert TS.may_complete(WS + 610.0, WS, WE) is None
    assert TS.may_complete(WS + 719.9, WS, WE) is None


# ------------------------------------------------- the settlement boundary

def test_nothing_completes_at_or_after_the_cutoff():
    assert TS.may_complete(WS + 720.0, WS, WE) == "PAST_COMPLETE_CUTOFF"
    assert TS.may_complete(WS + 721.0, WS, WE) == "PAST_COMPLETE_CUTOFF"
    assert TS.may_complete(WS + 899.0, WS, WE) == "PAST_COMPLETE_CUTOFF"


def test_the_tail_still_ends_180s_before_settlement():
    """The margin is what settlement safety is bought with; 180s of it."""
    assert WE - TS.cancel_cutoff(WE) == 180.0


def test_an_early_VENUE_close_pulls_the_whole_tail_earlier():
    """`t_end` is the venue's close, not start+WINDOW_S. The tail is relative
    to the real close, so an early close shortens it rather than overrunning."""
    early = WS + 500.0
    assert TS.cancel_cutoff(early) == WS + 320.0
    assert TS.may_complete(WS + 319.0, WS, early) is None
    assert TS.may_complete(WS + 321.0, WS, early) == "PAST_TERMINAL"


# ------------------------------------------------- resting orders and cancels

def test_no_order_may_rest_past_the_terminal_whatever_its_ttl():
    """`order_due` is min(submit + ttl, terminal): the terminal is a hard
    second bound, so a late order is due even with ttl left."""
    submitted = WS + 650.0                    # inside the tail
    assert TS.order_due(WS + 700.0, submitted, WS, WE) is False
    assert TS.order_due(WS + 720.0, submitted, WS, WE) is True
    assert TS.order_due(WS + 721.0, submitted, WS, WE) is True


def test_the_per_order_ttl_still_binds_inside_the_tail():
    submitted = WS + 300.0
    assert TS.order_ttl_s() == 120.0
    assert TS.order_due(WS + 419.0, submitted, WS, WE) is False
    assert TS.order_due(WS + 421.0, submitted, WS, WE) is True


def test_the_oldest_reachable_order_age_stays_under_the_watchdog_threshold():
    """THE SAFETY ARGUMENT, as arithmetic. The watchdog calls an order older
    than 300s overdue and ASSERTS HALT BEFORE cancelling. The per-order TTL
    caps age at 120s, so the margin is not what prevents that -- which is
    why narrowing it to the floor does not expose the watchdog."""
    watchdog_overdue_s = 300.0
    assert TS.order_ttl_s() < watchdog_overdue_s
    assert watchdog_overdue_s - TS.order_ttl_s() == 180.0


def test_a_pending_cancellation_is_not_an_assumed_cancellation():
    """`order_due` says an order SHOULD be withdrawn. It is not evidence that
    the venue has withdrawn it, and nothing here reports a terminal state."""
    submitted = WS + 650.0
    due = TS.order_due(WS + 725.0, submitted, WS, WE)
    assert due is True
    assert isinstance(due, bool)              # a verdict, not a venue state
    # and the gate keeps refusing new completions regardless of the cancel
    assert TS.may_complete(WS + 725.0, WS, WE) == "PAST_COMPLETE_CUTOFF"


# ------------------------------------------------- partial fills

def test_a_partial_fill_inside_the_tail_leaves_the_remainder_completable():
    """The deadline is a function of the CLOCK, not of how much filled, so a
    partially filled leg is governed by the same tail as a whole one."""
    for t in (605.0, 660.0, 719.0):
        assert TS.may_complete(WS + t, WS, WE) is None, t
    assert TS.may_complete(WS + 720.0, WS, WE) == "PAST_COMPLETE_CUTOFF"


# ------------------------------------------------- the regression direction

def test_widening_the_margin_back_to_300_re_collapses_the_tail(monkeypatch):
    """The env can still WIDEN (`max(CANCEL_MARGIN_S, env)`), and widening is
    precisely what caused the defect. Pinned so it cannot return unnoticed."""
    monkeypatch.setenv(TS.CANCEL_MARGIN_ENV, "300")
    assert TS.cancel_margin_s() == 300.0
    d = TS.binding_completion_deadline_s(WINDOW)
    assert d["binding_completion_deadline_s"] == 600.0
    assert d["collapsed_onto_entry_cutoff"] is True
    assert d["completion_outlives_opening_s"] == 0.0
    assert TS.may_complete(WS + 601.0, WS, WE) == "PAST_TERMINAL"


def test_narrowing_below_the_floor_is_still_refused(monkeypatch):
    """Safety is the floor's job, and it is not configurable away."""
    for v in ("0", "10", "60", "179", "not-a-number", ""):
        monkeypatch.setenv(TS.CANCEL_MARGIN_ENV, v)
        assert TS.cancel_margin_s() == 180.0, v
