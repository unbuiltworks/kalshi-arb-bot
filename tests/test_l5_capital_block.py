"""Pricing what a capital or inventory limit refused.

The engine already records THAT a submission was refused. These pin what it
COST, because a limit cannot be argued about without that: one that refuses
nothing profitable is free, and one that refuses matched surplus is paying
for safety with throughput.

NOTHING HERE IS A PROBABILITY OR A REALISED FIGURE, and the tests assert
that the labels say so.
"""
from decimal import Decimal as D

import pytest

from src.l5_capital_block import BLOCKED_BY, capital_block_record, classify

#: 1245z, held UP 2.27 for $0.7264, a DOWN completion refused at own 0.64
CASE = dict(side="DOWN", price="0.64", qty="2.29", bid="0.3300", ask="0.3400",
            held_qty="2.29", held_cost_usd="0.7264",
            residual_budget_usd="3.00")


@pytest.mark.parametrize("reason,want", [
    ("CASH_COMMITMENT:3.1>3.0", "CASH_ENVELOPE"),
    ("CASH_COMMITMENT", "CASH_ENVELOPE"),
    ("RESIDUAL_CAP", "RESIDUAL_LIMIT"),
    ("RESIDUAL_ABOVE_CAP", "RESIDUAL_LIMIT"),
    ("MAX_LIVE_ORDERS", "ORDER_COUNT"),
    ("ACCOUNT_EXPOSURE_REFUSED", "EXPOSURE_CAP"),
    ("EXPOSURE_UNREADABLE:x:y", "EXPOSURE_CAP"),
])
def test_the_engines_own_reasons_map_to_the_owners_limits(reason, want):
    assert classify(reason) == want


@pytest.mark.parametrize("reason", [
    "BOOK_STALE", "COMPLEMENT_PAIR_ABOVE_CAP", "PAST_ENTRY_CUTOFF",
    "NOT_ARMED", "", None, "CASH_COMMITMENTX"])
def test_a_refusal_that_is_not_a_LIMIT_is_not_counted_as_one(reason):
    """An unclassified refusal is not evidence about a limit. Returning a
    guess would let economic refusals be counted as capital starvation."""
    assert classify(reason) is None
    assert capital_block_record(reason=reason, **CASE) is None


def test_it_covers_all_four_limits_the_owner_asked_about():
    assert set(BLOCKED_BY.values()) == {
        "CASH_ENVELOPE", "RESIDUAL_LIMIT", "ORDER_COUNT", "EXPOSURE_CAP"}


# ------------------------------------------------- the four numbers

def test_a_BUY_SHORT_leg_is_charged_a_dollar_a_share_not_its_price():
    """The cash gate's convention, which is why a dearer DOWN quote costs
    the envelope nothing extra."""
    r = capital_block_record(reason="CASH_COMMITMENT", **CASE)
    assert r["is_buy_short"] is True
    assert D(r["additional_obligation_usd"]) == D("2.29")      # q, not q*px


def test_an_UP_leg_is_charged_its_price():
    r = capital_block_record(reason="CASH_COMMITMENT",
                             **dict(CASE, side="UP", price="0.31"))
    assert r["is_buy_short"] is False
    assert D(r["additional_obligation_usd"]) == D("2.29") * D("0.31")


def test_the_downside_if_stranded_is_the_cash_this_leg_would_lose():
    r = capital_block_record(reason="MAX_LIVE_ORDERS", **CASE)
    assert D(r["downside_if_stranded_usd"]) == D("2.29") * D("0.64")


def test_the_matched_surplus_is_the_pair_and_is_labelled_conditional():
    r = capital_block_record(reason="MAX_LIVE_ORDERS", **CASE)
    # complement touch for an UP leg is the bid, 0.33, so the pair is 0.97
    assert r["complement_touch_own_px"] == "0.3300"
    assert D(r["pair_cost_if_both_fill"]) == D("0.97")
    assert D(r["expected_matched_surplus_usd"]) > 0
    lab = r["labels"]
    assert "conditional on BOTH" in lab
    assert "not a forecast" in lab and "probability" in lab


def test_a_pair_above_a_dollar_reports_a_NEGATIVE_surplus():
    """It must not clamp at zero: a limit that refused a LOSING pair did the
    right thing, and the record has to be able to say so."""
    r = capital_block_record(reason="MAX_LIVE_ORDERS",
                             **dict(CASE, price="0.90"))
    assert D(r["pair_cost_if_both_fill"]) > D(1)
    assert D(r["expected_matched_surplus_usd"]) < 0


def test_the_completion_distance_sign_is_documented_and_right():
    """Negative is headroom under the ceiling; positive means no permissible
    completion is reachable at the touch."""
    near = capital_block_record(reason="MAX_LIVE_ORDERS", **CASE)
    assert D(near["completion_distance_ticks"]) < 0        # 0.33 under 0.95
    assert "POSITIVE completion_distance_ticks" in near["labels"]
    # the complement here is UP, whose touch is the BID, so a high bid is
    # what puts the touch above a tight ceiling
    far = capital_block_record(
        reason="MAX_LIVE_ORDERS",
        **dict(CASE, bid="0.9500", ask="0.9600", residual_budget_usd="0"))
    assert D(far["completion_distance_ticks"]) > 0


# --------------------------------------- unreadable stays unreadable

def test_an_unreadable_book_leaves_the_surplus_None_rather_than_guessing():
    r = capital_block_record(reason="CASH_COMMITMENT",
                             **dict(CASE, bid=None, ask=None))
    assert r is not None
    assert r["expected_matched_surplus_usd"] is None
    assert r["pair_cost_if_both_fill"] is None
    # the obligation and the downside do not need a book, so they survive
    assert r["additional_obligation_usd"] is not None


def test_an_unreadable_quantity_leaves_both_money_fields_None():
    r = capital_block_record(reason="CASH_COMMITMENT",
                             **dict(CASE, qty=None))
    assert r["additional_obligation_usd"] is None
    assert r["downside_if_stranded_usd"] is None


def test_no_held_inventory_means_no_completion_ceiling():
    r = capital_block_record(reason="CASH_COMMITMENT",
                             **dict(CASE, held_qty="0"))
    assert r["completion_ceiling"] is None
    assert r["completion_distance_ticks"] is None


def test_the_wire_price_is_carried_so_maker_side_is_checkable():
    r = capital_block_record(reason="MAX_LIVE_ORDERS", **CASE)
    assert D(r["wire_price"]) == D(1) - D("0.64")
