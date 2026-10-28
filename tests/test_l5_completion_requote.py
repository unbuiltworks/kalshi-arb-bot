"""P2: repricing a completion that is not filling.

THE EVIDENCE, from the 284 preserved live book samples of 2026-10-28,
measured over each residual's whole life rather than at the one instant the
engine happened to evaluate:

  window  samples  touch ok by     touch ok by        resting quote,
                   opening cap     completion ceiling ticks behind touch
  1215z        57        29/57              56/57     median 12, max 50
  1245z        88         5/88              78/88     median 18, max 35

Both failures are real. The quote was placed `depth_ticks` behind and then
FROZEN while the market ran, ending 12-18 ticks away; and the opening cap
refused a touch-priced completion in 83 of 1245z's 88 sampled books, so the
engine could not have moved there anyway.

1215z is the instructive case: its resting level WAS reached in 8 of the 11
samples of the order's life and still did not fill, because reaching a
level is not a fill -- we were behind the size already resting there. The
requote buys a price that CAN trade, never a trade.

These fixtures are the two live cases at their recorded books, plus the
partial-fill, cancellation-race, no-fill and adverse-selection paths.
"""
from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

WS = 1_793_189_700.0                       # 1215z window start, 12:15:00Z
WE = WS + TS.WINDOW_S

#: 1215z at 12:23:53: held UP 4.21 for $1.5547, DOWN 1.92 for $0.7488.
#: Book bid 0.4900 / ask 0.5100, so the DOWN touch is own 0.4900 and the
#: engine rested at own 0.4700.
L1215 = dict(side="DOWN", resting_own_px="0.47", resting_qty="2.29",
             resting_filled_qty="0", bid="0.4900", ask="0.5100",
             tick="0.01", min_qty="0.01",
             held_qty="4.21", held_cost_usd="1.5547",
             deficient_qty="1.92", deficient_cost_usd="0.7488",
             residual_budget_usd="3.00",
             now_s=WS + 533.0, window_start_s=WS, window_end_s=WE,
             book_age_s="0.05")

#: 1245z at 12:52:25: held UP 2.27 for $0.7264, nothing on the other side.
#: Book bid 0.3400 / ask 0.3500 puts the DOWN touch at own 0.6500 for a
#: 0.97 pair -- refused by the opening cap, permitted as a completion.
L1245 = dict(L1215, resting_own_px="0.63", resting_qty="2.27",
             bid="0.3400", ask="0.3500",
             held_qty="2.27", held_cost_usd="0.7264",
             deficient_qty="0", deficient_cost_usd="0")


def rq(case, **over):
    kw = dict(case)
    kw.update(over)
    return TS.completion_requote(**kw)


# ----------------------------------------------- the two live fixtures

def test_1215z_requotes_from_0_47_to_the_0_49_touch():
    r = rq(L1215)
    assert r["act"] is True, r
    assert D(r["from_price"]) == D("0.47")
    assert D(r["to_price"]) == D("0.49")
    assert r["ticks_gained"] == "2"
    assert r["ceiling_binds"] is False       # 0.49 is far under the ceiling
    assert D(r["qty"]) <= D("2.29")


def test_1245z_requotes_to_a_touch_the_opening_cap_would_refuse():
    r = rq(L1245)
    assert r["act"] is True, r
    assert D(r["to_price"]) == D("0.65")
    # the pair this implies is above the opening ceiling, and permitted
    # only because completing beats stranding inside the owner's budget
    assert D("0.3200") + D(r["to_price"]) > TS.max_pair_cost()
    assert r["completion_ceiling"] == "0.95"


def test_the_requote_never_crosses_on_either_fixture():
    for case in (L1215, L1245):
        r = rq(case)
        assert r["act"] is True
        assert TS.leg_crosses("DOWN", D(r["to_price"]),
                              bid=D(case["bid"]), ask=D(case["ask"])) is False


# -------------------------------------------------- it must not act

def test_an_uncertain_order_state_refuses_before_anything_else():
    r = rq(L1215, orders_uncertain="UNVERIFIED_SUBMIT")
    assert r["act"] is False and r["reason"] == "ORDERS_UNCERTAIN"


def test_a_stale_book_refuses():
    assert rq(L1215, book_age_s="9.0")["reason"] == "BOOK_STALE"
    assert rq(L1215, book_age_s=None)["reason"] == "BOOK_STALE"


def test_a_suspected_gap_refuses():
    assert rq(L1215, gap_suspected=True)["reason"] == "GAP_SUSPECTED"


def test_a_crossed_book_refuses():
    assert rq(L1215, bid="0.5200", ask="0.5100")["reason"] \
        == "BOOK_CROSSED_OR_LOCKED"


def test_past_the_completion_cutoff_it_refuses():
    r = rq(L1215, now_s=WS + TS.COMPLETE_CUTOFF_S + 1.0)
    assert r["act"] is False
    assert r["reason"] in ("PAST_COMPLETE_CUTOFF", "PAST_TERMINAL")


def test_too_little_runway_to_cancel_and_resubmit_refuses():
    """The sequence is cancel, confirm, submit. Started too late it leaves
    the residual with no quote at all, which is worse than a stale one."""
    r = rq(L1215, now_s=TS.cancel_cutoff(WE) - 5.0)
    assert r["act"] is False
    assert r["reason"] == "INSUFFICIENT_REQUOTE_RUNWAY"


def test_a_quote_already_at_the_touch_is_left_alone():
    r = rq(L1215, resting_own_px="0.49")
    assert r["act"] is False and r["reason"] == "ALREADY_NEAR_TOUCH"


def test_a_quote_the_market_came_TO_is_left_alone():
    """Negative ticks-behind: the order is marketable where it rests, so
    moving it would only surrender its place in that queue."""
    r = rq(L1215, resting_own_px="0.52")
    assert r["act"] is False and r["reason"] == "ALREADY_NEAR_TOUCH"
    assert D(r["ticks_behind"]) < 0


def test_a_one_tick_move_is_not_worth_the_queue_priority():
    r = rq(L1215, resting_own_px="0.48")
    assert r["act"] is False and r["reason"] == "ALREADY_NEAR_TOUCH"


def test_a_matched_book_has_nothing_to_complete():
    r = rq(L1215, held_qty="1.92", deficient_qty="1.92")
    assert r["act"] is False and r["reason"] == "NOTHING_TO_COMPLETE"


def test_an_unreadable_budget_refuses_rather_than_repricing():
    r = rq(L1215, residual_budget_usd=None)
    assert r["act"] is False and r["reason"] == "NO_COMPLETION_CEILING"


# ------------------------------------- partial fills and races

def test_a_partially_filled_order_requotes_only_its_working_remainder():
    """1.00 of the 2.29 already filled: the replacement carries 1.29, and
    the residual it reduces is smaller too."""
    r = rq(L1215, resting_filled_qty="1.00",
           held_qty="4.21", deficient_qty="2.92")
    assert r["act"] is True, r
    assert D(r["qty"]) <= D("1.29")


def test_a_fully_filled_order_is_not_requoted():
    """The cancellation race: it filled between our read and our decision.
    Nothing is still working, so there is nothing to move."""
    r = rq(L1215, resting_filled_qty="2.29")
    assert r["act"] is False and r["reason"] == "NOTHING_STILL_WORKING"


def test_the_requote_can_never_exceed_the_unmatched_remainder():
    """Even with a large order still working, the replacement is clipped to
    what is genuinely one-sided -- it can never reverse the book."""
    r = rq(L1215, resting_qty="99", held_qty="4.21", deficient_qty="3.00")
    assert r["act"] is True, r
    assert D(r["qty"]) <= D("1.21")


def test_a_quantity_below_the_venue_grid_refuses():
    r = rq(L1215, resting_qty="0.005", min_qty="0.01")
    assert r["act"] is False
    assert r["reason"] in ("NOTHING_STILL_WORKING",
                           "NOTHING_TO_REQUOTE_ON_GRID")


# ------------------------------------------------ adverse selection

def test_adverse_selection_clamps_the_target_to_the_ceiling():
    """The touch has run past what completing can justify, so the move goes
    to the ceiling rather than chasing the market to it."""
    r = rq(L1245, residual_budget_usd="0", bid="0.2000", ask="0.2100")
    if r["act"]:
        assert D(r["to_price"]) <= D(r["completion_ceiling"])
        assert r["ceiling_binds"] is True


def test_adverse_selection_refuses_when_the_ceiling_is_below_us():
    """Nothing dearer than where we already rest is permissible, so the
    residual is carried rather than chased."""
    r = rq(L1245, resting_own_px="0.70", residual_budget_usd="0",
           bid="0.2000", ask="0.2100")
    assert r["act"] is False
    assert r["reason"] in ("NO_IMPROVEMENT", "ALREADY_NEAR_TOUCH")


def test_the_target_is_always_dearer_than_where_we_rest():
    """The one invariant that makes this safe to run repeatedly: it only
    ever moves TOWARD the touch, so it cannot walk a quote away."""
    for px in ("0.20", "0.35", "0.47"):
        r = rq(L1215, resting_own_px=px)
        if r["act"]:
            assert D(r["to_price"]) > D(px)


def test_the_queue_cost_of_the_move_is_reported():
    r = rq(L1215, queue_ahead="105.29")
    assert r["act"] is True
    assert r["queue_ahead_at_target"] == "105.29"


def test_nothing_in_the_result_claims_a_fill():
    r = rq(L1215)
    assert "fill" not in str(r.get("labels", "")).lower() or \
        "not a trade" in str(r["labels"]).lower()
    assert set(r) >= {"act", "from_price", "to_price", "qty"}
