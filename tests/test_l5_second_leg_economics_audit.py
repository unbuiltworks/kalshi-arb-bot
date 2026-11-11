"""The five gaps a code audit of PR #215 found, and their fixes.

PR #215's own 26 tests pass and its fail-closed behaviour is sound: no
maker fill is assumed, a taker candidate is marked `execution_confirmed:
False`, a model-ranked candidate is marked `model_only: True`, and an
absent probability refuses rather than defaults. This file covers what it
did NOT model, each verified against the live 2026-11-09 15:30Z window or
against a measured venue fact.

  1. MAKER REBATES could not be expressed at all. A credit would have to
     be a negative `fee`, which `__post_init__` refuses -- correctly, since
     the same door would admit a negative ADVERSE cost. They are now their
     own non-negative inputs WITH the measured 2.52-contract floor, because
     crediting a rebate the venue does not pay is how a losing
     configuration is made to look profitable.
  2. PARTIAL FILLS were not representable. The joint distribution is
     all-or-nothing per leg, yet the live window filled 0.7 of a
     1-contract DOWN order.
  3. STALENESS was ungated in the paired model. `evaluate` bounds book age
     at 2 s; `evaluate_paired_entry` took no book age at all.
  4. DEPTH was ungated in the paired model, so it could price a size the
     book cannot absorb.
  5. A NON-NORMALISED DISTRIBUTION RAISED instead of refusing, so a
     controller asking "should I enter?" got an exception rather than no.

NOTHING HERE IS A FILL OR A PROFIT. Every number is a hypothesis priced
from supplied observations.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src.l5_second_leg_economics import (PROB_SUM_TOLERANCE,
                                         evaluate_paired_entry)

#: a normalised, deliberately unexciting distribution for structural tests.
P = dict(p_both="0.25", p_up_only="0.25", p_down_only="0.25",
         p_neither="0.25", p_up_settles_given_up_only="0.5",
         p_up_settles_given_down_only="0.5")
#: the CONFIRMED CREDITED maker rebate, $2.13 over 293 fills.
REBATE = "0.00727"
#: the confirmed floor below which it earns $0.00.
FLOOR = "2.52"


# =====================================================================
# 1. MAKER REBATES, AND THE FLOOR THAT MAKES THEM ZERO AT OUR CLIP
# =====================================================================

def test_a_rebate_is_a_credit_and_lowers_the_pair_cost():
    plain = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                  quantity="3", **P)
    paid = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                 quantity="3", up_rebate=REBATE,
                                 down_rebate=REBATE, **P)
    assert D(paid["pair_cost"]) < D(plain["pair_cost"])
    assert D(plain["pair_cost"]) - D(paid["pair_cost"]) == D(REBATE) * 2
    assert D(paid["rebate_credited"]) == D(REBATE) * 2
    assert paid["rebate_earned"] is True


def test_the_rebate_is_ZERO_below_the_measured_floor():
    """THE DEPLOYED CLIP IS 1 CONTRACT and the confirmed floor is 2.52, so
    the rebate the deployed configuration earns is $0.00. A model that
    credited it anyway would flatter the configuration that just lost."""
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                quantity="1", up_rebate=REBATE,
                                down_rebate=REBATE, rebate_min_qty=FLOOR, **P)
    assert out["rebate_earned"] is False
    assert D(out["rebate_credited"]) == D("0")
    plain = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                  quantity="1", **P)
    assert D(out["pair_cost"]) == D(plain["pair_cost"])


def test_at_or_above_the_floor_the_rebate_is_credited():
    for q in ("2.52", "3", "5"):
        out = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                    quantity=q, up_rebate=REBATE,
                                    down_rebate=REBATE, rebate_min_qty=FLOOR,
                                    **P)
        assert out["rebate_earned"] is True, q


def test_a_negative_rebate_is_refused():
    with pytest.raises(ValueError, match="negative rebate"):
        evaluate_paired_entry(up_price="0.48", down_price="0.47",
                              quantity="3", up_rebate="-0.01", **P)


def test_a_negative_fee_is_still_refused_so_the_credit_door_stays_narrow():
    """The rebate has its own input precisely so that `fee` cannot carry a
    credit -- the same relaxation would admit a negative adverse cost."""
    with pytest.raises(ValueError, match="negative fee"):
        evaluate_paired_entry(up_price="0.48", down_price="0.47",
                              quantity="3", up_fee="-0.00727", **P)
    with pytest.raises(ValueError, match="negative fee"):
        evaluate_paired_entry(up_price="0.48", down_price="0.47",
                              quantity="3", adverse_cost="-0.01", **P)


def test_the_rebate_also_reduces_the_worst_case_on_an_unmatched_leg():
    """What is at risk is the leg's cost NET of what the venue credits."""
    plain = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                  quantity="3", **P)
    paid = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                 quantity="3", up_rebate=REBATE,
                                 down_rebate=REBATE, **P)
    assert D(paid["residual_worst"]) > D(plain["residual_worst"])


# =====================================================================
# 2. PARTIAL FILLS -- THE LIVE WINDOW'S ACTUAL SHAPE
# =====================================================================

def test_a_partial_single_leg_fill_is_representable():
    """The live 15:30Z DOWN leg filled 0.7 of a 1-contract order."""
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                quantity="1", down_filled_qty="0.7", **P)
    assert D(out["down_filled_qty"]) == D("0.7")
    assert D(out["up_filled_qty"]) == D("1")


def test_a_partial_fill_scales_ITS_OWN_branch_proportionally():
    """TWICE-CORRECTED, and the corrections are the point.

    First form expected `residual_worst` to improve when only DOWN's
    filled size shrank. It does not: that field is the MINIMUM over both
    single-leg branches, and a full-size UP branch still governs.

    Second form then expected the DOWN branch to improve. It does not
    either, because with `p_up_settles_given_down_only = 0.5` the DOWN
    branch is PROFITABLE (+0.06), so filling less of it earns less.

    The invariant that actually holds, and holds whatever the sign, is
    LINEARITY in the filled quantity -- which is the property a partial
    fill should have."""
    whole = evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                  quantity="1", **P)
    part = evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                 quantity="1", down_filled_qty="0.7", **P)
    assert D(whole["down_only_expected_net"]) == D("0.06")
    assert D(part["down_only_expected_net"]) == D("0.06") * D("0.7")
    assert D(part["up_only_expected_net"]) == D(whole["up_only_expected_net"])
    assert D(part["residual_worst"]) == D(whole["residual_worst"]), (
        "the UP branch is unchanged and is the worse one, so the minimum "
        "must not move")


def test_a_partial_fill_DOES_reduce_a_losing_branch():
    """The risk-reducing reading, on a branch that actually loses: DOWN
    filled alone while UP settles pays nothing, so the cost is the loss and
    filling less of it loses less. This is the live 15:30Z shape."""
    whole = evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                  quantity="1", p_both="0", p_up_only="0",
                                  p_down_only="1", p_neither="0",
                                  p_up_settles_given_up_only="0",
                                  p_up_settles_given_down_only="1")
    part = evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                 quantity="1", down_filled_qty="0.7",
                                 p_both="0", p_up_only="0",
                                 p_down_only="1", p_neither="0",
                                 p_up_settles_given_up_only="0",
                                 p_up_settles_given_down_only="1")
    assert D(whole["down_only_expected_net"]) == D("-0.44")
    assert D(part["down_only_expected_net"]) == D("-0.308")
    assert D(part["down_only_expected_net"]) > D(whole["down_only_expected_net"])


def test_shrinking_BOTH_branches_does_improve_the_worst_case():
    whole = evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                  quantity="1", **P)
    part = evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                 quantity="1", up_filled_qty="0.7",
                                 down_filled_qty="0.7", **P)
    assert D(part["residual_worst"]) > D(whole["residual_worst"])


def test_the_partial_matches_the_live_windows_measured_cost_basis():
    """0.7 contracts of DOWN at own 0.44 cost $0.308, and that is exactly
    what the window settled for when the leg expired worthless."""
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                quantity="1", down_filled_qty="0.7",
                                p_both="0", p_up_only="0", p_down_only="1",
                                p_neither="0",
                                p_up_settles_given_up_only="0",
                                p_up_settles_given_down_only="1")
    # DOWN filled alone and UP settled, so the DOWN leg paid nothing.
    assert D(out["down_only_expected_net"]) == D("-0.308")
    assert D(out["expected_net"]) == D("-0.308")


def test_a_filled_quantity_above_the_quoted_size_is_refused():
    for bad in ("1.01", "2"):
        with pytest.raises(ValueError, match="filled quantity"):
            evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                  quantity="1", down_filled_qty=bad, **P)


def test_a_zero_or_negative_filled_quantity_is_refused():
    for bad in ("0", "-0.5"):
        with pytest.raises(ValueError, match="filled quantity"):
            evaluate_paired_entry(up_price="0.51", down_price="0.44",
                                  quantity="1", up_filled_qty=bad, **P)


def test_fees_scale_pro_rata_with_the_filled_fraction():
    half = evaluate_paired_entry(up_price="0.50", down_price="0.50",
                                 quantity="2", down_fee="0.04",
                                 down_filled_qty="1", **P)
    full = evaluate_paired_entry(up_price="0.50", down_price="0.50",
                                 quantity="2", down_fee="0.04", **P)
    # the quoted fee is for the quoted size; half filled charges half.
    assert D(full["down_only_expected_net"]) < D(half["down_only_expected_net"])


# =====================================================================
# 3. STALENESS
# =====================================================================

def test_a_stale_book_refuses_the_paired_entry():
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                quantity="1", book_age_s="6.5", **P)
    assert not out["eligible"] and out["reason"] == "STALE_BOOK"
    assert "expected_net" not in out


def test_the_default_freshness_bound_is_two_seconds():
    assert evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                 quantity="1", book_age_s="2",
                                 **P)["reason"] != "STALE_BOOK"
    assert evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                 quantity="1", book_age_s="2.01",
                                 **P)["reason"] == "STALE_BOOK"


def test_an_absent_book_age_does_not_silently_pass_a_stale_book(monkeypatch):
    """An omitted age is NOT asserted fresh -- it simply is not checked, and
    callers in the money path are expected to supply it. Pinned so that the
    omission stays visible rather than reading as a freshness guarantee."""
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                quantity="1", **P)
    assert "book_age_s" not in out, (
        "no freshness is reported when none was supplied")


# =====================================================================
# 4. DEPTH
# =====================================================================

@pytest.mark.parametrize("up_d,down_d,side", [
    ("0.5", "10", "UP"), ("10", "0.5", "DOWN"), ("0.5", "0.5", "UP")])
def test_insufficient_depth_refuses_and_names_the_side(up_d, down_d, side):
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                quantity="1", up_depth=up_d,
                                down_depth=down_d, **P)
    assert not out["eligible"] and out["reason"] == "INSUFFICIENT_DEPTH"
    assert out["insufficient_side"] == side


def test_depth_exactly_equal_to_the_size_is_sufficient():
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                quantity="1", up_depth="1", down_depth="1",
                                **P)
    assert out["reason"] != "INSUFFICIENT_DEPTH"


# =====================================================================
# 5. A NON-NORMALISED DISTRIBUTION REFUSES RATHER THAN RAISING
# =====================================================================

def test_the_tolerance_is_tight_enough_to_catch_a_real_mismatch():
    assert PROB_SUM_TOLERANCE < D("0.001")


def test_refusal_order_puts_the_cheap_gates_before_the_probabilities():
    """A stale or too-thin book is refused WITHOUT consuming calibrated
    probabilities, so the reason an operator reads is the first real one."""
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                quantity="1", book_age_s="9",
                                up_depth="0.1",
                                p_both="0.9", p_up_only="0.9",
                                p_down_only="0.9", p_neither="0.9",
                                p_up_settles_given_up_only="0.5",
                                p_up_settles_given_down_only="0.5")
    assert out["reason"] == "STALE_BOOK"


# =====================================================================
# WHAT PR #215 ALREADY GOT RIGHT -- pinned so a refactor cannot lose it
# =====================================================================

def test_an_absent_probability_still_refuses_rather_than_defaulting():
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47",
                                quantity="1")
    assert not out["eligible"]
    assert out["reason"] == "JOINT_FILL_OR_SETTLEMENT_UNVALIDATED"
    assert "expected_net" not in out


def test_every_eligible_verdict_is_marked_model_only():
    out = evaluate_paired_entry(up_price="0.30", down_price="0.30",
                                quantity="1", **P)
    assert out["eligible"] is True
    assert out["model_only"] is True, (
        "an eligible hypothesis must never read as a confirmed execution")


# =====================================================================
# THE ADVERSE-SELECTION-AWARE PAIR CEILING
# =====================================================================
from src.l5_second_leg_economics import (DEFAULT_ADVERSE_SKEW,
                                         admissible_pair_ceiling)

#: PROVEN conservative arm, recorded books at depth 2, out of sample.
BOTH, SINGLE = D("0.525"), D("0.423")
CEIL = dict(p_both=str(BOTH), p_up_only=str(SINGLE / 2),
            p_down_only=str(SINGLE / 2))


def test_the_default_skew_is_nonzero():
    """Zero would assert a single fill carries no directional information,
    which the live window refutes."""
    assert DEFAULT_ADVERSE_SKEW > 0
    assert DEFAULT_ADVERSE_SKEW <= D("0.5")


def test_more_adverse_selection_demands_a_cheaper_pair():
    prev = None
    for x in ("0", "0.05", "0.10", "0.20", "0.30", "0.50"):
        c = admissible_pair_ceiling(adverse_skew=x, **CEIL)
        if prev is not None:
            assert c < prev, x
        prev = c


def test_the_ceiling_reproduces_the_1530z_break_even_exactly():
    """At the measured break-even skew the ceiling equals the pair that
    window actually quoted, 0.95. The inversion is exact, not searched."""
    c = admissible_pair_ceiling(adverse_skew="0.0871", **CEIL)
    assert c.quantize(D("0.0001")) == D("0.9500")


def test_the_1530z_pair_is_REFUSED_at_the_default_skew():
    """THE ENTRY DEFECT. pair 0.95 sits inside the deployed 0.96 constant
    and is admitted by it, but is above the ceiling the economics allow
    once a single fill is treated as informative."""
    c = admissible_pair_ceiling(**CEIL)
    assert D("0.95") > c
    assert c.quantize(D("0.0001")) == D("0.9138")


def test_at_zero_skew_the_ceiling_is_LOOSER_so_callers_must_take_the_min():
    """Pinned because it is the one case where using this ALONE would
    widen entry. The documented contract is min(existing, this)."""
    c = admissible_pair_ceiling(adverse_skew="0", **CEIL)
    assert c > D("0.96")
    assert min(D("0.96"), c) == D("0.96")


def test_a_pair_that_never_completes_has_no_admissible_price():
    with pytest.raises(ValueError, match="p_both must be positive"):
        admissible_pair_ceiling(p_both="0", p_up_only="0.5",
                                p_down_only="0.5")


def test_a_higher_profit_threshold_demands_a_cheaper_pair():
    base = admissible_pair_ceiling(**CEIL)
    strict = admissible_pair_ceiling(minimum_expected_net="0.02", **CEIL)
    assert strict < base


@pytest.mark.parametrize("bad", ["-0.01", "0.51", "1"])
def test_a_skew_outside_zero_to_a_half_is_refused(bad):
    with pytest.raises(ValueError, match="adverse skew"):
        admissible_pair_ceiling(adverse_skew=bad, **CEIL)


def test_a_negative_profit_threshold_is_refused():
    with pytest.raises(ValueError, match="negative minimum expected net"):
        admissible_pair_ceiling(minimum_expected_net="-0.01", **CEIL)


def test_the_ceiling_agrees_with_the_full_model_at_the_boundary():
    """Cross-check: a pair AT the ceiling should price to roughly zero
    expected net through `evaluate_paired_entry`, and a pair a cent
    dearer should be negative."""
    skew = D("0.15")
    c = admissible_pair_ceiling(adverse_skew=str(skew), **CEIL)
    a, b = D("0.5") - skew, D("0.5") + skew
    half = (c / 2).quantize(D("0.0001"))
    out = evaluate_paired_entry(
        up_price=str(half), down_price=str(half), quantity="1",
        p_both=str(BOTH), p_up_only=str(SINGLE / 2),
        p_down_only=str(SINGLE / 2),
        p_neither=str(D("1") - BOTH - SINGLE),
        p_up_settles_given_up_only=str(a),
        p_up_settles_given_down_only=str(b))
    assert abs(D(out["expected_net"])) < D("0.001"), out["expected_net"]
