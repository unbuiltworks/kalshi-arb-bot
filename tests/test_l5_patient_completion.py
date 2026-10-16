"""Patient opposite-side completion: ECONOMICS decide, never the deadline.

The bar this file exists to hold: `completion_decision` must never return ok
True because time is short. Every assertion is about the worst of the two
settlement outcomes, the cash and ceiling already in force, or a fail-closed
input.
"""
from decimal import Decimal as D

import pytest

from src import l5_faithful_translation as FT
from src import l5_two_sided_probe as TS


def inputs(**over):
    """A one-sided book: 5 UP at 0.59, nothing DOWN. DOWN crosses at 0.40."""
    base = dict(up_qty="5", dn_qty="0", up_cost_px="0.59", dn_cost_px="0",
                taker_price="0.40", available_depth="500",
                tick="0.01", min_qty="0.01",
                cash_headroom="10", worst_case_ceiling="3.60",
                book_age_s="0.05", gap_suspected=False)
    base.update(over)
    return base


# ------------------------------------------------------- the phase gate

def test_the_maker_phase_decides_nothing():
    """Crossing early throws away the edge the maker quote was placed for."""
    start = 1000.0
    assert TS.completion_phase(start + 1, start) == "MAKER"
    assert TS.completion_phase(start + 300, start) == "MAKER"


def test_the_completion_window_is_the_last_two_minutes():
    start = 1000.0
    cut = TS.cancel_cutoff_from_start(start)
    assert TS.completion_phase(cut - TS.COMPLETION_WINDOW_S - 1,
                               start) == "MAKER"
    assert TS.completion_phase(cut - TS.COMPLETION_WINDOW_S, start) == \
        "COMPLETION"
    assert TS.completion_phase(cut - 1, start) == "COMPLETION"
    assert TS.completion_phase(cut, start) == "LATE"
    assert TS.completion_phase(cut + 600, start) == "LATE"


def test_the_cutoff_is_derived_from_the_window_START():
    """The same arithmetic trap the deadline sweep already fell into once."""
    start = 1000.0
    assert TS.completion_phase(start + 700, start) == "COMPLETION"
    assert TS.cancel_cutoff_from_start(start) == start + 900 - 180


# ------------------------------------------------ the decision itself

def test_a_completion_that_improves_the_worst_outcome_is_allowed():
    r = TS.completion_decision(**inputs())
    assert r["ok"] is True and r["cross"] is True
    assert r["side"] == "DOWN"
    assert r["mode"] == FT.TAKER and r["post_only"] is False
    # 5 UP at 0.59 held alone loses 2.95 + fee on the DOWN outcome.
    # Completing 5 DOWN at 0.40 makes the pair cost 4.95 + fees against a
    # 5.00 payout on EITHER outcome.
    assert D(r["worst_after"]) > D(r["worst_before"])
    assert D(r["worst_improvement_usd"]) > 0
    assert D(r["qty"]) == D("5")
    # NOT PROFITABLE, and the test says so rather than hoping. 5 UP at 0.59
    # is 2.95 sunk; completing 5 DOWN at 0.40 makes the pair cost 4.95 plus
    # 0.18 of taker fees against a 5.00 payout, so the book settles at
    # -0.13 on BOTH outcomes. That is damage limitation against a -3.04
    # hold, not an edge, and the labels must not read as one.
    assert D(r["worst_after"]) == D("-0.13")
    assert D(r["worst_before"]) == D("-3.04")
    assert r["both_positive_after"] is False


def test_worst_case_improvement_ALONE_is_nearly_vacuous():
    """MEASURED, because it decides whether the rule means anything. Holding
    a one-sided leg loses its whole cost on the wrong outcome, so every
    completion below a dollar improves the worst case. Without a second bar
    the engine would cross at any price the book showed."""
    for px in ("0.35", "0.45", "0.60", "0.80", "0.90", "0.95"):
        tu = TS.top_up(up_qty="5", dn_qty="0", up_price="0.59", dn_price="0",
                       extra_side="DOWN", extra_qty="5", extra_price=px)
        assert tu["ok"] is True, px


def test_a_cross_that_costs_more_in_fee_than_it_buys_is_refused():
    """THE SECOND BAR. In expectation a completion at the market's own price
    costs exactly the taker fee, so the improvement must exceed it."""
    # a book one hundredth of a share short: completing it lifts min(a, b)
    # by 0.01 of a share, worth half a cent, against a cent of taker fee
    # the venue bills in whole cents.
    r = TS.completion_decision(**inputs(up_qty="1", dn_qty="0.99",
                                        dn_cost_px="0.38",
                                        taker_price="0.50"))
    assert r["ok"] is False and r["cross"] is False
    assert r["reason"] == "IMPROVEMENT_BELOW_ITS_OWN_FEE"
    assert D(r["improvement_usd"]) <= D(r["taker_fee_usd"])


def test_the_improvement_must_exceed_the_fee_that_bought_it():
    r = TS.completion_decision(**inputs())
    assert D(r["worst_improvement_usd"]) > D(r["taker_fee_usd"])


def test_the_deadline_is_never_itself_a_reason():
    """THE CENTRAL PROPERTY. completion_decision takes no clock at all, so
    no amount of elapsed time can turn a refusal into a cross."""
    import inspect
    sig = inspect.signature(TS.completion_decision).parameters
    for forbidden in ("now", "t_start", "t_end", "deadline", "phase",
                      "cutoff", "time"):
        assert forbidden not in sig, forbidden
    bad = TS.completion_decision(**inputs(up_qty="1", dn_qty="0.99",
                                          dn_cost_px="0.38",
                                          taker_price="0.50"))
    assert bad["ok"] is False


def test_an_already_matched_book_has_nothing_to_complete():
    r = TS.completion_decision(**inputs(up_qty="5", dn_qty="5",
                                        dn_cost_px="0.38"))
    assert r["reason"] == "ALREADY_MATCHED"


def test_an_empty_book_has_nothing_to_complete():
    r = TS.completion_decision(**inputs(up_qty="0", dn_qty="0"))
    assert r["reason"] == "NO_INVENTORY_TO_COMPLETE"


def test_the_deficient_side_is_the_SMALLER_leg():
    r = TS.completion_decision(**inputs(up_qty="0", dn_qty="5",
                                        dn_cost_px="0.38",
                                        taker_price="0.61"))
    assert r["side"] == "UP"


# ------------------------------------------------------- partial fills

def test_insufficient_depth_completes_PARTIALLY_and_says_so():
    r = TS.completion_decision(**inputs(available_depth="2"))
    assert r["ok"] is True
    assert D(r["qty"]) == D("2")
    assert r["partial"] is True
    # a partial still lifts min(a, b), so it still improves the worst case
    assert D(r["worst_after"]) > D(r["worst_before"])


def test_a_partial_that_does_not_help_is_still_refused():
    r = TS.completion_decision(**inputs(available_depth="0.01",
                                        taker_price="0.90"))
    assert r["ok"] is False


def test_depth_is_never_assumed_when_absent():
    r = TS.completion_decision(**inputs(available_depth=None))
    assert r["reason"] == "NO_DISPLAYED_DEPTH"


def test_completing_an_existing_partial_leg_blends_the_cost():
    """Both legs already hold inventory; the deficit is the difference."""
    r = TS.completion_decision(**inputs(up_qty="5", dn_qty="3",
                                        dn_cost_px="0.38"))
    assert r["ok"] is True
    assert D(r["deficit"]) == D("2")
    assert D(r["qty"]) == D("2")


# --------------------------------------------------- fail-closed inputs

def test_a_stale_book_is_not_an_executable_price():
    r = TS.completion_decision(**inputs(book_age_s="9"))
    assert r["reason"] == "BOOK_STALE"
    r = TS.completion_decision(**inputs(book_age_s=None))
    assert r["reason"] == "BOOK_STALE"


def test_a_suspected_gap_refuses():
    assert TS.completion_decision(
        **inputs(gap_suspected=True))["reason"] == "GAP_SUSPECTED"


@pytest.mark.parametrize("over,why", [
    ({"taker_price": None}, "COMPLETION_PRICE_UNREADABLE"),
    ({"taker_price": "0"}, "COMPLETION_PRICE_UNREADABLE"),
    ({"taker_price": "1"}, "COMPLETION_PRICE_UNREADABLE"),
    ({"taker_price": "0.02"}, "COMPLETION_PRICE_OUT_OF_BAND"),
    ({"taker_price": "0.405"}, "COMPLETION_PRICE_NOT_ON_TICK"),
    ({"tick": None}, "TICK_UNKNOWN"),
    ({"min_qty": None}, "MIN_QTY_UNKNOWN"),
    ({"cash_headroom": None}, "CASH_HEADROOM_UNKNOWN"),
    ({"worst_case_ceiling": None}, "WORST_CASE_CEILING_UNKNOWN"),
    ({"worst_case_ceiling": "0"}, "WORST_CASE_CEILING_UNKNOWN"),
    ({"up_qty": None}, "INVENTORY_UNREADABLE"),
])
def test_every_missing_input_refuses_rather_than_defaulting(over, why):
    assert TS.completion_decision(**inputs(**over))["reason"] == why


def test_no_cash_headroom_means_no_legal_quantity():
    r = TS.completion_decision(**inputs(cash_headroom="0"))
    assert r["reason"] == "NO_LEGAL_COMPLETION_QUANTITY"


def test_cash_headroom_clips_the_size_including_the_fee():
    r = TS.completion_decision(**inputs(cash_headroom="1"))
    assert r["ok"] is True
    cost = D(r["qty"]) * D(r["price"]) + D(r["taker_fee_usd"])
    assert cost <= D("1"), (r["qty"], r["taker_fee_usd"])


def test_an_improvement_that_breaches_the_ceiling_is_still_refused():
    """An improvement is not permission to breach the authorized bound."""
    r = TS.completion_decision(**inputs(up_qty="40", up_cost_px="0.59",
                                        worst_case_ceiling="0.10",
                                        cash_headroom="100"))
    assert r["ok"] is False
    assert r["reason"] in ("COMPLETION_EXCEEDS_WORST_CASE_CEILING",
                           "TOP_UP_WORSENS_WORST_OUTCOME")


# ---------------------------------------------- both settlement outcomes

def test_both_settlement_outcomes_are_priced_after_completion():
    """A completed pair pays min(a, b) on EITHER outcome, so both sides are
    priced and they are EQUAL once the pair is matched."""
    r = TS.completion_decision(**inputs())
    up_pnl, dn_pnl = D(r["up_pnl_after"]), D(r["dn_pnl_after"])
    assert up_pnl == dn_pnl                     # matched: outcome-independent
    assert min(up_pnl, dn_pnl) == D(r["worst_after"])


def test_a_cheap_enough_completion_IS_positive_on_both_outcomes():
    r = TS.completion_decision(**inputs(taker_price="0.35"))
    assert r["ok"] is True
    assert r["both_positive_after"] is True
    assert D(r["up_pnl_after"]) > 0 and D(r["dn_pnl_after"]) > 0


def test_the_held_alternative_is_what_it_is_compared_against():
    """worst_before is the HOLD case: the one-sided book settling against us.
    Without completion, 5 UP at 0.59 loses its whole cost on DOWN."""
    r = TS.completion_decision(**inputs())
    assert D(r["worst_before"]) < 0
    held = TS.net_settlement(up_qty="5", dn_qty="0", up_price="0.59",
                             dn_price="0")
    assert D(r["worst_before"]) == held["worst_outcome"]


def test_the_fee_charged_is_the_TAKER_fee():
    r = TS.completion_decision(**inputs())
    assert D(r["taker_fee_usd"]) == TS.worst_fee(D(r["qty"]), D(r["price"]))
    assert D(r["taker_fee_usd"]) > 0


# ---------------------------------------------------- purity of the module

def test_the_completion_decision_cannot_submit_or_cancel():
    import inspect
    src = inspect.getsource(TS.completion_decision)
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    for forbidden in ("submit", ".cancel(", "requests", "urllib", "open("):
        assert forbidden not in code, forbidden
