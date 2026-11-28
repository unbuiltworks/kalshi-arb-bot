"""The completion depth default, and the live window that justified it.

WHY THIS FILE EXISTS. `adaptive_depth_enabled()` was DEFAULT OFF, and its
own docstring already recorded the measurement that said OFF completes
nothing: a completion fixed at the touch completes ZERO pairs in 652
replayed entries, because a touch pair is `1 - spread` and the fee-safe
ceiling is 0.96.

The owner-authorized live window `cpc-btc-updown-15m-2026-11-09-1530z`
then ran with it OFF and reproduced that shape exactly -- one filled first
leg, zero completions, -$0.3080 settled. These tests pin the flipped
default so the validated shape cannot be left behind an unset variable
again, and they pin the window's own arithmetic so the reason survives.

WHAT IS NOT CLAIMED. Flipping this does not make the strategy profitable
and does not prove the 1530z window would have paired -- a complement must
still FILL, and in a rising book a complement resting behind the touch may
not. It removes one specific impossibility: at depth 0 there is no
admissible price at all.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC

ADAPT = BC.ADAPTIVE_ENV
DEPTH = BC.COMPLETION_DEPTH_TICKS_ENV
TICK = D("0.01")
CAP = D("0.96")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv(ADAPT, raising=False)
    monkeypatch.setenv(DEPTH, "0")
    yield


# =====================================================================
# THE DEFAULT
# =====================================================================

def test_the_walk_is_ON_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv(ADAPT, raising=False)
    assert BC.adaptive_depth_enabled() is True


def test_an_unset_variable_walks_outward_rather_than_pinning_depth_zero():
    assert BC.adaptive_depth_candidates() == [0, 1, 2, 3, 4, 5]


@pytest.mark.parametrize("raw", ["0", "no", "false", "off", "OFF", "False",
                                 " off ", "NO"])
def test_only_an_explicit_false_spelling_turns_it_off(monkeypatch, raw):
    monkeypatch.setenv(ADAPT, raw)
    assert BC.adaptive_depth_enabled() is False
    assert BC.adaptive_depth_candidates() == [0]


@pytest.mark.parametrize("raw", ["1", "yes", "true", "on", "ON", "True"])
def test_the_true_spellings_still_work(monkeypatch, raw):
    monkeypatch.setenv(ADAPT, raw)
    assert BC.adaptive_depth_enabled() is True


@pytest.mark.parametrize("raw", ["", "   ", "typo", "maybe", "2", "yes!"])
def test_an_UNREADABLE_value_keeps_the_validated_shape(monkeypatch, raw):
    """A typo must not silently restore the configuration that completes
    nothing. Off is an explicit act, not the result of a slip."""
    monkeypatch.setenv(ADAPT, raw)
    assert BC.adaptive_depth_enabled() is True
    assert len(BC.adaptive_depth_candidates()) > 1


def test_the_walk_is_bounded_and_stops_at_five_ticks():
    assert BC.ADAPTIVE_MAX_DEPTH_TICKS == 5
    assert max(BC.adaptive_depth_candidates()) == 5


def test_the_configured_depth_is_tried_FIRST_so_nothing_is_skipped(monkeypatch):
    """The walk starts where the operator pinned it and only then widens,
    so enabling this never makes a shallower choice unavailable."""
    monkeypatch.setenv(DEPTH, "2")
    assert BC.adaptive_depth_candidates()[0] == 2


def test_an_unreadable_depth_falls_back_to_the_code_default_not_the_entry(
        monkeypatch):
    monkeypatch.setenv(DEPTH, "not-a-number")
    assert BC.completion_depth_ticks() == BC.COMPLETION_DEPTH_TICKS
    monkeypatch.setenv(DEPTH, "-3")
    assert BC.completion_depth_ticks() == BC.COMPLETION_DEPTH_TICKS


# =====================================================================
# THE 1530z ARITHMETIC, FROM THAT WINDOW'S OWN DECISION LINES
# =====================================================================
#: DOWN filled at own 0.44 (ask 0.56), cost basis $0.308 on 0.7 contracts.
DOWN_OWN = D("0.44")
#: every bid observed in that window AFTER the fill, from the decision
#: lines and the final `book_now`.
BIDS_AFTER_FILL = [D("0.53"), D("0.56"), D("0.53"), D("0.54"),
                   D("0.58"), D("0.61"), D("0.58"), D("0.60")]


def pair_at(bid: D, depth: int) -> D:
    """A maker complement rests `depth` ticks behind the bid."""
    return DOWN_OWN + (bid - D(depth) * TICK)


def test_at_depth_zero_no_observed_bid_admits_a_pair_inside_the_cap():
    """THE FINDING. This is why the live window completed nothing: at the
    touch the cheapest admissible pair the book ever offered was 0.97."""
    best = min(pair_at(b, 0) for b in BIDS_AFTER_FILL)
    assert best == D("0.97")
    assert best > CAP
    assert not any(pair_at(b, 0) <= CAP for b in BIDS_AFTER_FILL)


def test_ONE_tick_behind_the_touch_already_admits_a_pair():
    """A CORRECTION of this file's first version, which asserted depth 2.
    Depth 1 clears: own 0.52 against DOWN 0.44 is exactly 0.96, the cap
    inclusive. So the walk the live window needed was ONE tick, not two --
    which makes the depth-0 pin a narrower miss and a worse one."""
    assert pair_at(D("0.53"), 1) == D("0.96") == CAP
    assert pair_at(D("0.53"), 1) <= CAP
    assert pair_at(D("0.53"), 0) == D("0.97") > CAP


def test_depth_two_also_admits_and_does_so_with_margin():
    assert pair_at(D("0.53"), 2) == D("0.95") < CAP


def test_the_walk_reaches_those_depths_from_the_deployed_configuration():
    """The service had L5_COMPLETION_DEPTH_TICKS=0 and no adaptive
    variable. With the flipped default that configuration reaches both."""
    cands = BC.adaptive_depth_candidates()
    assert 1 in cands and 2 in cands


def test_the_walk_stops_at_the_FIRST_admissible_depth_not_the_deepest():
    """Walking further than necessary would give away price for nothing."""
    first = next(d for d in BC.adaptive_depth_candidates()
                 if any(pair_at(b, d) <= CAP for b in BIDS_AFTER_FILL))
    assert first == 1, first
    assert pair_at(D("0.53"), first) <= CAP
    assert pair_at(D("0.53"), first - 1) > CAP, (
        "depth 0 must still be refused, or 1 is not the first")


def test_the_cap_itself_is_not_moved_by_any_of_this():
    """The whole point: this chooses WHERE to rest, never WHETHER the
    economics permit it."""
    assert BC.BREAKEVEN_PAIR == D("0.9532")
    assert BC.REDEMPTION_PAIR == D("1.00")


def test_a_pair_outside_the_cap_is_refused_at_every_depth_in_the_walk():
    """When the book has run far enough, the walk finds nothing and the
    leg stays stranded -- which is correct, not a failure to try."""
    far_bid = D("0.80")
    assert not any(pair_at(far_bid, d) <= CAP
                   for d in BC.adaptive_depth_candidates())
