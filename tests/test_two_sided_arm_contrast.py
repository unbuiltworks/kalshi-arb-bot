"""The arms must be GENUINELY different policies.

Batch 1 produced bit-identical results for all three arms. These tests exist so
that failure mode can never recur silently: each one fails if an arm collapses
into another.
"""
from decimal import Decimal as D

import pytest

from src.l5_two_sided import (ARM_ADAPTED, ARM_CONTROL, ARM_REPLICATION,
                              COMPLETE_NOW, EXIT_NOW, WAIT, TwoSided,
                              best_action)

TOLS = (D("0"), D("0.05"), D("0.50"))


def cmp_(comp, exit_):
    return {COMPLETE_NOW: {"value": comp}, EXIT_NOW: {"value": exit_}}


# ------------------------------------------------ B: REPLICATION vs ADAPTED

def test_grid_contains_reachable_states_where_the_two_arms_differ():
    """The Batch-1 defect: ADAPTED's extra branch was subsumed, so the two arms
    were the same function. A sweep must now find divergence."""
    vals = [None] + [D(x) / D(100) for x in range(-200, 201, 7)]
    diff = 0
    for c in vals:
        for e in vals:
            for t in TOLS:
                if best_action(cmp_(c, e), arm=ARM_REPLICATION,
                               residual_tolerance=t) != \
                   best_action(cmp_(c, e), arm=ARM_ADAPTED,
                               residual_tolerance=t):
                    diff += 1
    assert diff > 0, "REPLICATION and ADAPTED are the same function again"


def test_adapted_prefers_a_better_exit_over_a_positive_completion():
    """The preregistered distinction: ADAPTED takes the economically superior
    executable action. REPLICATION is patient and just completes."""
    c = cmp_(D("0.02"), D("0.10"))
    assert best_action(c, arm=ARM_REPLICATION,
                       residual_tolerance=D("0.50")) == COMPLETE_NOW
    assert best_action(c, arm=ARM_ADAPTED,
                       residual_tolerance=D("0.50")) == EXIT_NOW


def test_adapted_takes_a_positive_exit_where_replication_merely_waits():
    """Completion is negative, exit is positive, tolerance NOT breached."""
    c = cmp_(D("-0.05"), D("0.03"))
    assert best_action(c, arm=ARM_REPLICATION,
                       residual_tolerance=D("0.50")) == WAIT
    assert best_action(c, arm=ARM_ADAPTED,
                       residual_tolerance=D("0.50")) == EXIT_NOW


def test_neither_arm_exits_for_a_negative_exit_without_a_tolerance_breach():
    """ADAPTED is 'economically superior', not 'trigger-happy': a loss-making
    exit still needs the forced-flatten condition."""
    c = cmp_(D("-0.05"), D("-0.03"))
    for arm in (ARM_REPLICATION, ARM_ADAPTED):
        assert best_action(c, arm=arm, residual_tolerance=D("0.50")) == WAIT


def test_forced_flatten_still_applies_to_both_arms():
    c = cmp_(D("-0.05"), D("-0.60"))
    for arm in (ARM_REPLICATION, ARM_ADAPTED):
        assert best_action(c, arm=arm,
                           residual_tolerance=D("0.50")) == EXIT_NOW


def test_control_uses_the_patient_replication_comparator():
    """CONTROL isolates STRUCTURE, so its completion comparator must match
    REPLICATION exactly -- otherwise it confounds two differences at once."""
    vals = [None] + [D(x) / D(100) for x in range(-200, 201, 11)]
    for c in vals:
        for e in vals:
            for t in TOLS:
                assert best_action(cmp_(c, e), arm=ARM_CONTROL,
                                   residual_tolerance=t) == \
                       best_action(cmp_(c, e), arm=ARM_REPLICATION,
                                   residual_tolerance=t)


# ------------------------------------------------------- A: CONTROL is real

def _feed(c, *, bid, ask, t, bid_qty="50", ask_qty="50"):
    c.on_book({"bid": bid, "ask": ask, "bid_qty": bid_qty,
               "ask_qty": ask_qty, "status": "OK",
               "_ladder": {"bids": {D(bid): D(bid_qty)},
                           "offers": {D(ask): D(ask_qty)}}}, t)


def test_control_rests_only_one_side_while_flat():
    c = TwoSided(arm=ARM_CONTROL, clip=D(1))
    c.window_end = 10_000.0
    _feed(c, bid="0.40", ask="0.60", t=1000.0)
    resting = [s for s in ("up", "down") if c.legs[s] is not None]
    assert len(resting) == 1, f"CONTROL rested {resting}"
    assert c.blocked.get("control_single_first_leg", 0) >= 1


def test_replication_rests_both_sides_while_flat_on_the_same_state():
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1))
    c.window_end = 10_000.0
    _feed(c, bid="0.40", ask="0.60", t=1000.0)
    assert [s for s in ("up", "down") if c.legs[s] is not None] == \
        ["up", "down"], "REPLICATION must quote BOTH sides when flat"


def test_control_and_replication_differ_behaviorally_on_one_shared_state():
    """The two arms, same tape, must not produce the same working exposure."""
    ctl, rep = (TwoSided(arm=a, clip=D(1))
                for a in (ARM_CONTROL, ARM_REPLICATION))
    for c in (ctl, rep):
        c.window_end = 10_000.0
        _feed(c, bid="0.40", ask="0.60", t=1000.0)
    assert ctl.places != rep.places


def test_control_first_leg_side_is_the_cheaper_touch_and_deterministic():
    c = TwoSided(arm=ARM_CONTROL, clip=D(1))
    c.window_end = 10_000.0
    _feed(c, bid="0.30", ask="0.95", t=1000.0)   # up touch 0.30, down 0.05
    assert c.first_leg_side() == "down"
    assert c.legs["down"] is not None and c.legs["up"] is None


def test_cycles_started_increments_on_the_first_fill_of_a_cycle():
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1))
    assert c.cycles_started == 0
    c._book_fill("up", D(1), D("0.40"), maker=True, evidence="t")
    assert c.cycles_started == 1
    c._book_fill("up", D(1), D("0.41"), maker=True, evidence="t")
    assert c.cycles_started == 1, "same cycle must not re-count"


def test_a_banked_pair_opens_a_new_countable_cycle():
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1))
    c._book_fill("up", D(1), D("0.40"), maker=True, evidence="t")
    c._book_fill("down", D(1), D("0.50"), maker=True, evidence="t")
    assert (c.cycles_started, c.cycles_completed) == (1, 1)
    c._book_fill("up", D(1), D("0.40"), maker=True, evidence="t")
    assert c.cycles_started == 2


def test_max_cycles_one_prevents_a_second_cycle():
    """The Batch-1 defect: the gate was dead because cycles_started never
    moved, so CONTROL completed 14 cycles."""
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1), max_cycles=1)
    c.window_end = 10_000.0
    c.now = 1000.0
    c._book_fill("up", D(1), D("0.40"), maker=True, evidence="t")
    c._book_fill("down", D(1), D("0.50"), maker=True, evidence="t")
    assert c.cycles_completed == 1 and c.residual() == 0
    _feed(c, bid="0.40", ask="0.60", t=1001.0)
    assert c.legs["up"] is None and c.legs["down"] is None
    assert c.blocked.get("max_cycles", 0) >= 1


def test_control_binds_max_cycles_to_one_and_forbids_extend():
    c = TwoSided(arm=ARM_CONTROL, clip=D(1), max_cycles=50,
                 allow_extend=True)
    assert c.max_cycles == 1 and c.allow_extend is False


def test_no_inert_behavioural_marker_remains():
    c = TwoSided(arm=ARM_CONTROL, clip=D(1))
    assert not hasattr(c, "one_leg_only"), \
        "a reporting flag must not imply behaviour it does not enforce"


def test_unknown_arm_is_refused():
    with pytest.raises(ValueError):
        TwoSided(arm="SOMETHING_ELSE", clip=D(1))


# ------------------------------------------------------ C: accounting labels

def test_no_total_containing_the_shadow_flatten_is_called_settled():
    """Batch 1 reported a -12.67 'settled_net' that was dominated by a
    hypothetical flatten. No key may imply settlement for such a quantity."""
    c = TwoSided(arm=ARM_ADAPTED, clip=D(1))
    c.flatten_pnl = D("-5.00")
    r = c.report(winner="down")
    for key, v in r.items():
        if v is None or key in ("accounting_note", "rebate_note"):
            continue
        if "settle" in key and "shadow" not in key:
            assert D(str(v)) != c.flatten_pnl and \
                D(str(v)) == D(str(v)), key
    assert "settled_net" not in r
    assert D(r["shadow_total_with_flatten"]) == D("-5.00")
    assert D(r["source_settled_total"]) == D("0")


def test_the_two_totals_differ_by_exactly_the_shadow_flatten():
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1))
    c.qty["up"] = D(1)
    c.cost["up"] = D("0.40")
    c.flatten_pnl = D("-0.07")
    s = c.settle("up")
    assert D(s["shadow_total_with_flatten"]) - \
        D(s["source_settled_total"]) == D("-0.07")


def test_source_settled_identity_holds_and_excludes_modeled_rebates():
    from src.l5_two_sided import Pair
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1))
    c.pairs.append(Pair(D(2), D("0.35"), D("0.57"), up_maker=True,
                        down_maker=False, cycle=0, t=0.0))
    c.qty["up"] = D(3)
    c.cost["up"] = D("1.05")
    c.qty["down"] = D(2)
    c.cost["down"] = D("1.14")
    c.flatten_pnl = D("-0.11")
    s = c.settle("up")
    lhs = (D(s["matched_gross_edge"]) - D(s["taker_fees_cashflow"])
           + D(s["residual_settlement_pnl"]))
    assert lhs == D(s["source_settled_total"])
    assert lhs + D(s["flatten_pnl_shadow"]) == \
        D(s["shadow_total_with_flatten"])
    # the modeled rebate is reported but is NOT inside either total
    assert "maker_rebates_modeled_cashflow" in s
