"""Phase 5's scenario matrix, against the ONE controller.

Nine conditions the mandate names, each asked of `decide_inventory` so the
unified controller -- not a stub -- answers them: partial fills, a missing
second leg, cancellation races, stale books, rollover, multiple rounds,
adverse price movement, settlement arithmetic, and restart recovery.

THE INVARIANT RUNNING THROUGH ALL NINE: the controller may refuse to ADD
exposure for any reason it likes, but it must never refuse to MANAGE
exposure already standing. Every scenario below that holds inventory asserts
an action from the disposition family; every scenario that is flat asserts
the opening family, where a refusal is safe.

The 2026-11-07 12:30Z window is the permanent fixture. Its venue orders:

    12:30:27  DOWN 0.5000 FILLED    l5f-b195e7360f86e0e4b6c9363a
    12:30:49  UP   0.4500 FILLED    l5f-83557f64dd82a6809828ef7d
    12:32:54+ UP   0.3500 CANCELED  l5f-2c1c0f64c13ec71c5cc7ab5e
    12:33:57  UP   0.4600 FILLED    l5f-fc3f9809135cd723b717ab4f
    12:34:58  DOWN 0.4900 CANCELED  l5f-19329b90120cb4523271ea9f

UP 2 @ basis 0.9100, DOWN 1 @ 0.5000, `yes_value 0.0000` -- settled
-$0.4100. PR #207 pins the same arithmetic independently.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_unmatched_disposition as UD

SLUG = "cpc-btc-updown-15m-2026-11-07-1230z"
#: the book round 1 was opened into: one tick wide, as 217 of 220 samples
BOOK = {"bid": D("0.4800"), "ask": D("0.4900")}
WINDOW_S = 900.0


def disp(**over):
    """The held-DOWN state at 12:30:44, before the UP completion filled."""
    out = dict(held_cost_usd=D("0.5000"),
               deficient_qty=D("0"), deficient_cost_usd=D("0"),
               bid=BOOK["bid"], ask=BOOK["ask"], tick=D("0.01"),
               min_qty=D("0.01"), now_s=44.0, window_start_s=0.0,
               window_end_s=WINDOW_S, taker_enabled=False,
               # a live stream age; `None` reads as stale and fails closed
               book_age_s=D("0.05"))
    out.update(over)
    return out


def reop(**over):
    out = dict(worst_leg_own_px=D("0.4900"), qty=D("1"),
               bid=BOOK["bid"], ask=BOOK["ask"], tick=D("0.01"),
               depth_ticks=2, now_s=0.0, window_end_s=WINDOW_S,
               exposure_headroom_usd=D("1.00"))
    out.update(over)
    return out


def held(qty, side="DOWN", **over):
    return UD.decide_inventory(market=SLUG, held_side=side,
                               held_qty=D(str(qty)),
                               disposition_kwargs=disp(**over),
                               reopen_kwargs=reop())


# ------------------------------------------------------------ 1. PARTIAL FILLS

@pytest.mark.parametrize("qty", ["0.01", "0.03", "0.5", "1", "1.92"])
def test_a_partial_fill_is_inventory_and_must_be_disposed_of(qty):
    """2015z settled with a 0.03-contract residual. Size is not the test."""
    r = held(qty)
    assert r["inventory_family"] == "DISPOSITION"
    assert r["selected"] in UD.ACTIONS
    assert r["selected"] is not None, "a partial fill is never ignored"


def test_a_partly_filled_resting_completion_still_counts_as_resting():
    r = held("1", resting_own_px=D("0.4500"), resting_qty=D("1"),
             resting_filled_qty=D("0.4"))
    assert r["inventory_family"] == "DISPOSITION"
    assert r["selected"] in UD.ACTIONS


# ------------------------------------------------------- 2. MISSING SECOND LEG

def test_the_1230z_held_leg_with_its_completion_resting_is_preserved():
    """The live controller chose PRESERVE at 12:30:44 and it filled in 5s."""
    r = held("1", resting_own_px=D("0.4500"), resting_qty=D("1"))
    assert r["selected"] == UD.PRESERVE, r.get("selected_reason")


def test_a_held_leg_with_nothing_resting_still_gets_a_decision():
    """0100z emitted 83 records reading N/A. Never again."""
    r = held("1")
    assert r["selected"] is not None
    assert r["selected_reason"]


# ----------------------------------------------------- 3. CANCELLATION RACES

def test_uncertain_orders_cannot_authorise_a_crossing():
    """An ambiguous POST must not be resolved by crossing the book."""
    r = held("1", orders_uncertain=True, taker_enabled=True)
    assert r["selected"] not in UD.CROSSING


def test_a_suspected_gap_cannot_authorise_a_crossing():
    r = held("1", gap_suspected=True, taker_enabled=True)
    assert r["selected"] not in UD.CROSSING


def test_crossing_is_unreachable_while_the_route_is_closed():
    """Owner order: taker disabled. It may be PRICED, never selected."""
    r = held("1", taker_enabled=False)
    assert r["selected"] not in UD.CROSSING
    priced = {a["action"] for a in (r.get("actions") or [])}
    assert UD.COMPLETE_TAKER in priced, "the counterfactual is still stated"


# ------------------------------------------------------------ 4. STALE BOOKS

def test_a_stale_book_cannot_authorise_a_crossing():
    r = held("1", book_age_s=D("30"), taker_enabled=True)
    assert r["selected"] not in UD.CROSSING


def test_a_stale_book_does_not_stop_the_decision_being_recorded():
    r = held("1", book_age_s=D("30"))
    assert r["selected_reason"]


@pytest.mark.parametrize("kw", [{"bid": None}, {"ask": None},
                                {"bid": D("0.50"), "ask": D("0.49")}])
def test_a_one_sided_book_refuses_to_OPEN(kw):
    r = UD.decide_inventory(market=SLUG, held_qty=D("0"), rounds_used=0,
                            reopen_kwargs=reop(**kw))
    assert r["selected"] is None
    assert "BOOK_NOT_TWO_SIDED" in r["selected_reason"]


# --------------------------------------------------------------- 5. ROLLOVER

def test_no_round_opens_without_runway_to_complete_it():
    r = UD.decide_inventory(
        market=SLUG, held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reop(single_outcome_usd=D("-0.03"), now_s=800.0))
    assert r["selected"] is None
    assert "INSUFFICIENT_RUNWAY_FOR_A_ROUND" in r["selected_reason"]


def test_inventory_held_at_rollover_is_still_disposed_of():
    """Refusing to open late is right; refusing to manage is not."""
    r = held("1", now_s=880.0)
    assert r["inventory_family"] == "DISPOSITION"
    assert r["selected"] is not None


# --------------------------------------------------------- 6. MULTIPLE ROUNDS

def test_the_first_round_opens_and_so_does_the_second():
    """REPEATED ROUNDS ARE THE NORMAL CASE. L5 trades a median of 32 times
    per BTC15m window and p90 of 135; a controller that stopped after one
    pair would reproduce none of that."""
    first = UD.decide_inventory(market=SLUG, held_qty=D("0"), rounds_used=0,
                                reopen_kwargs=reop())
    assert first["selected"] == UD.OPEN
    second = UD.decide_inventory(market=SLUG, held_qty=D("0"), rounds_used=1,
                                 reopen_kwargs=reop())
    assert second["selected"] == UD.REPLENISH


def test_a_round_is_still_refused_when_it_cannot_be_AFFORDED():
    """Capacity, not expectancy, is what bounds throughput."""
    r = UD.decide_inventory(
        market=SLUG, held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reop(exposure_headroom_usd=D("0.10")))
    assert r["selected"] is None
    assert "EXPOSURE_HEADROOM_BELOW_WORST_CASE" in r["selected_reason"]


@pytest.mark.parametrize("used", [1, 2, 5])
def test_repeated_rounds_remain_available_where_justified(used):
    """NOT a one-pair cap: the gate is arithmetic, not a counter."""
    r = UD.decide_inventory(
        market=SLUG, held_qty=D("0"), rounds_used=used,
        reopen_kwargs=reop(single_outcome_usd=D("-0.03")))
    assert r["selected"] == UD.REPLENISH


def test_each_round_is_judged_against_the_state_the_last_one_left():
    """Outstanding exposure narrows the next round's headroom."""
    clean = UD.decide_inventory(
        market=SLUG, held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reop(single_outcome_usd=D("-0.03")))
    dirty = UD.decide_inventory(
        market=SLUG, held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reop(single_outcome_usd=D("-0.03"),
                           outstanding_worst_case_usd=D("0.80")))
    assert clean["selected"] == UD.REPLENISH
    assert dirty["selected"] is None
    assert "EXPOSURE_HEADROOM_BELOW_WORST_CASE" in dirty["selected_reason"]


# ------------------------------------------------- 7. ADVERSE PRICE MOVEMENT

def test_the_book_running_away_does_not_strand_the_held_leg():
    """0330z froze a plan against a 0.42/0.56 touch; the book went to 0.91."""
    r = held("1", bid=D("0.9100"), ask=D("0.9200"))
    assert r["inventory_family"] == "DISPOSITION"
    assert r["selected"] is not None


def test_an_adverse_book_is_bounded_by_its_WORST_CASE_not_its_level():
    """An 0.89 leg cannot fit a $1 budget once anything else is standing.

    The price LEVEL carries no expectancy signal -- the pair identity
    depends on the spread, not on where the book sits -- so what refuses a
    round into an extreme book is the capacity test.
    """
    r = UD.decide_inventory(
        market=SLUG, held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reop(bid=D("0.9100"), ask=D("0.9200"),
                           worst_leg_own_px=D("0.8900"),
                           outstanding_worst_case_usd=D("0.50")))
    assert r["selected"] is None
    assert "EXPOSURE_HEADROOM_BELOW_WORST_CASE" in r["selected_reason"]


def test_a_genuinely_wider_spread_does_raise_the_edge():
    r = UD.decide_inventory(
        market=SLUG, held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reop(bid=D("0.30"), ask=D("0.70")))
    assert r["selected"] == UD.REPLENISH
    assert D(r["reopen"]["pair_edge_usd"]) == D("0.4400")


# ------------------------------------------------------------- 8. SETTLEMENT

def test_the_matched_pair_redeems_exactly_one_dollar():
    """DOWN 0.5000 + UP 0.4500 = 0.9500 against a 1.0000 redemption.

    Read off the UNEQUAL call, because the balanced figure is what an
    unequal round has to beat: an equal round is refused by that function
    as NOT_AN_UNEQUAL_ROUND and never reaches the pricing at all.
    """
    w = UD.weighted_inventory_decision(
        market=SLUG, favoured_side="UP", n_favoured=D("2"), n_other=D("1"),
        up_own_px=D("0.4500"), down_own_px=D("0.5000"),
        bid=BOOK["bid"], ask=BOOK["ask"],
        exposure_headroom_usd=D("2.00"))
    assert D(w["balanced_alternative_usd"]) == D("0.0500")


def test_the_1230z_settlement_is_reproduced_exactly():
    """UP 2 @ 0.9100 and DOWN 1 @ 0.5000 settle -$0.4100 when DOWN wins."""
    w = UD.weighted_inventory_decision(
        market=SLUG, favoured_side="UP", n_favoured=D("2"), n_other=D("1"),
        up_own_px=D("0.4550"), down_own_px=D("0.5000"),
        bid=BOOK["bid"], ask=BOOK["ask"],
        exposure_headroom_usd=D("2.00"))
    assert D(w["spend_usd"]) == D("1.4100")
    assert D(w["if_down_wins_usd"]) == D("-0.4100")
    assert D(w["if_up_wins_usd"]) == D("0.5900")


def test_no_uncredited_rebate_appears_in_any_settlement_figure():
    """A zero commission is not a rebate and $0.0152 was never credited."""
    w = UD.weighted_inventory_decision(
        market=SLUG, favoured_side="UP", n_favoured=D("2"), n_other=D("1"),
        up_own_px=D("0.4550"), down_own_px=D("0.5000"),
        bid=BOOK["bid"], ask=BOOK["ask"],
        exposure_headroom_usd=D("2.00"))
    for k, v in w.items():
        if isinstance(v, str) and k.endswith("_usd"):
            assert "0.0152" not in v


# ------------------------------------------------------ 9. RESTART RECOVERY

def test_inventory_found_after_a_restart_is_disposed_of_not_reopened():
    """A restart that re-reads held inventory must manage it, not add to it."""
    r = held("1")
    assert r["inventory_family"] == "DISPOSITION"
    assert r["selected"] not in UD.OPENING_ACTIONS


def test_an_unreadable_inventory_read_takes_no_action_at_all():
    r = UD.decide_inventory(market=SLUG, held_qty="junk")
    assert r["selected"] is None
    assert r["selected_reason"] == "HELD_QTY_UNREADABLE"


def test_a_restart_with_no_disposition_inputs_does_not_guess():
    r = UD.decide_inventory(market=SLUG, held_side="DOWN", held_qty=D("1"))
    assert r["selected"] is None
    assert r["selected_reason"] == "DISPOSITION_INPUTS_MISSING"


# ------------------------------------------------- the invariant, stated once

@pytest.mark.parametrize("kw", [
    {}, {"orders_uncertain": True}, {"gap_suspected": True},
    {"book_age_s": D("30")}, {"now_s": 880.0},
    {"bid": D("0.9100"), "ask": D("0.9200")},
    {"resting_own_px": D("0.4500"), "resting_qty": D("1")},
])
def test_held_inventory_always_gets_an_action_whatever_the_condition(kw):
    """THE INVARIANT. Refuse to ADD exposure freely; never refuse to MANAGE."""
    r = held("1", **kw)
    assert r["inventory_family"] == "DISPOSITION"
    assert r["selected"] in UD.ACTIONS, (kw, r.get("selected_reason"))
