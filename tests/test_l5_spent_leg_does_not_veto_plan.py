"""A spent leg's dead price must not veto the live complement.

THE 2215z DEFECT, from the owner-authorized campaign of 2026-11-02.

  22:16:25  DOWN 1.92 submitted at own 0.3900 -- wire 0.61, an OFFER
  22:17:09  FILLED (venue order DFWP527012WE)
            the book then ran to 0.8800/0.9000
  thereafter  plan_unexecutable: LEG_WOULD_CROSS:DOWN
              retirement_blocked_by: LEG_HOLDS_UNMATCHED:DOWN
              and NO complement order was ever submitted

`plan_executable` judged BOTH legs for crossing and returned on the first
one that did. That was DOWN -- the leg already FILLED, whose old offer at
wire 0.61 sits far below a 0.88 bid and therefore "crosses". Its price was
history and would never be sent again. The UP complement, frozen at own
0.5600, did NOT cross that book.

So a dead leg's stale price vetoed a live one, and the record blamed the
wrong leg. Three of the campaign's four windows submitted no complement at
all.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

# the real 2215z plan and the real book it moved to
PLAN = {"pair_cost": "0.9500",
        "legs": [{"side": "DOWN", "price": "0.3900", "qty": "1.92",
                  "mode": "MAKER"},
                 {"side": "UP", "price": "0.5600", "qty": "1.92",
                  "mode": "MAKER"}]}
MOVED = dict(bid=D("0.88"), ask=D("0.90"), tick=D("0.01"), depth_ticks=2)
FRESH = dict(bid=D("0.60"), ask=D("0.61"), tick=D("0.01"), depth_ticks=2)


def test_the_spent_leg_really_does_cross_the_moved_book():
    """Establishing the premise, not assuming it."""
    assert TS.leg_crosses("DOWN", D("0.3900"), bid=D("0.88"), ask=D("0.90"))


def test_the_live_complement_really_does_NOT_cross_it():
    """So it was placeable, and the engine never judged it."""
    assert not TS.leg_crosses("UP", D("0.5600"), bid=D("0.88"), ask=D("0.90"))


def test_the_defect_reproduces_with_the_default_behaviour():
    """Judging both legs blames the SPENT one."""
    assert TS.plan_executable(PLAN, **MOVED) == "LEG_WOULD_CROSS:DOWN"


def test_excluding_the_spent_leg_stops_it_vetoing_the_live_one():
    """The verdict must no longer name DOWN. It may still refuse -- the
    complement's own frozen price is 30 ticks adrift of this book -- but it
    must refuse for the COMPLEMENT's reason, which is the one a caller can
    act on by re-pricing."""
    why = TS.plan_executable(PLAN, spent_sides=["DOWN"], **MOVED)
    assert why != "LEG_WOULD_CROSS:DOWN"
    assert "DOWN" not in str(why)
    assert str(why).startswith("PRICE_LEFT_BEHIND:UP")


def test_an_unspent_leg_that_crosses_is_still_refused():
    """The fix must not become a way to submit a crossing order."""
    plan = {"pair_cost": "0.9500",
            "legs": [{"side": "UP", "price": "0.8700", "qty": "1"},
                     {"side": "DOWN", "price": "0.0800", "qty": "1"}]}
    why = TS.plan_executable(plan, spent_sides=["UP"], bid=D("0.05"),
                             ask=D("0.07"), tick=D("0.01"), depth_ticks=2)
    assert why is not None
    assert "UP" not in str(why)          # the spent leg is not blamed
    assert "DOWN" in str(why)            # the live leg is


def test_both_legs_spent_is_not_an_unexecutable_plan():
    """Nothing left to place is not the same as unable to place."""
    assert TS.plan_executable(PLAN, spent_sides=["DOWN", "UP"],
                              **MOVED) is None


@pytest.mark.parametrize("spent", [None, (), []])
def test_the_default_judges_both_legs_byte_for_byte_as_before(spent):
    """No caller change and no spent list means identical behaviour."""
    assert TS.plan_executable(PLAN, spent_sides=spent, **MOVED) == \
        TS.plan_executable(PLAN, **MOVED)


def test_a_fresh_plan_on_its_own_book_is_still_executable(spent=None):
    assert TS.plan_executable(PLAN, **FRESH) is None
    assert TS.plan_executable(PLAN, spent_sides=["DOWN"], **FRESH) is None


@pytest.mark.parametrize("side", ["down", "Down", " DOWN "])
def test_the_spent_side_is_matched_case_and_space_insensitively(side):
    """A caller passing the store's own casing must not silently fail to
    exclude the leg -- that would reintroduce the defect quietly."""
    assert TS.plan_executable(PLAN, spent_sides=[side], **MOVED) != \
        "LEG_WOULD_CROSS:DOWN"


def test_an_unreadable_price_still_fails_closed_even_when_spent():
    """Reading every leg's price before excluding any is deliberate."""
    plan = {"pair_cost": "0.95",
            "legs": [{"side": "DOWN", "price": None, "qty": "1"},
                     {"side": "UP", "price": "0.56", "qty": "1"}]}
    assert TS.plan_executable(plan, spent_sides=["DOWN"],
                              **MOVED) == "LEG_PRICE_UNREADABLE"


def test_a_plan_that_is_not_two_legs_is_still_refused():
    assert TS.plan_executable({"legs": [{"side": "UP", "price": "0.5"}]},
                              spent_sides=["UP"], **MOVED) == \
        "PLAN_NOT_TWO_LEGS"
