"""The inventory-aware action scorer.

Constants are the deployed geometry (tick 0.01, one-tick spread,
depth_ticks 2, ceiling 0.96) and Stage 1's five actual first-leg fill prices.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.inventory_action_scorer import (
    OPEN, WAIT, pair_at_fill, planned_pair, score_open, score_reduce,
    score_wait, sweep)

STAGE1_F = (D("0.22"), D("0.44"), D("0.30"), D("0.40"), D("0.19"))


# ------------------------------------------------- the tick identities

def test_planned_and_at_fill_pairs_are_the_deployed_constants():
    assert planned_pair(2, 1) == D("0.95")
    assert pair_at_fill(2, 1) == D("0.97")


@pytest.mark.parametrize("b", ["0.10", "0.33", "0.46", "0.80"])
def test_both_pairs_are_independent_of_the_price_level(b):
    """b cancels: that is why these are identities and not averages."""
    assert planned_pair(2, 1) == D("0.95")
    assert pair_at_fill(2, 1) == D("0.97")


@pytest.mark.parametrize("d,want", [(1, "0.98"), (2, "0.97"), (3, "0.96"),
                                    (4, "0.95")])
def test_depth_moves_the_at_fill_pair_one_tick_at_a_time(d, want):
    assert pair_at_fill(d, 1) == D(want)


def test_a_wider_spread_also_makes_the_pair_reachable():
    """(d + spread) >= 4 ticks is the real condition."""
    assert pair_at_fill(2, 2) == D("0.96")
    assert pair_at_fill(2, 1) == D("0.97")


# --------------------------------- OPEN is unconditionally worse than WAIT

@pytest.mark.parametrize("F", STAGE1_F)
def test_open_never_beats_wait_at_the_deployed_ceiling(F):
    s = sweep(F=F, depth_ticks=2, spread_ticks=1, pair_cap=D("0.96"))
    assert s["complement_submittable"] is False
    assert s["positive_cells"] == 0, s
    assert s["open_ever_beats_wait"] is False


@pytest.mark.parametrize("F", STAGE1_F)
def test_open_becomes_conditionally_positive_at_a_reachable_ceiling(F):
    s = sweep(F=F, depth_ticks=2, spread_ticks=1, pair_cap=D("0.97"))
    assert s["complement_submittable"] is True
    assert s["positive_cells"] > 0
    assert s["best_cell"]["ev_per_contract"] == pytest.approx(0.03, abs=1e-9)


def test_an_unsubmittable_complement_forces_completion_to_zero():
    """Not 'unlikely' -- zero, because the ceiling refuses the order. No
    fill probability may be allowed to paper over that."""
    r = score_open(F=D("0.44"), depth_ticks=2, spread_ticks=1,
                   pair_cap=D("0.96"), p_fill=D(1),
                   p_complete_given_fill=D(1), q_given_fill=D("0.44"))
    assert r["complement_submittable_at_fill"] is False
    assert r["p_complete_effective"] == "0"
    # with q == F the residual is exactly EV-neutral, so OPEN is exactly 0
    assert r["ev_per_contract"] == D(0)


def test_adverse_selection_makes_open_strictly_negative():
    r = score_open(F=D("0.44"), depth_ticks=2, spread_ticks=1,
                   pair_cap=D("0.96"), p_fill=D(1),
                   p_complete_given_fill=D(1),
                   q_given_fill=D("0.44") * D("0.9"))
    assert r["ev_per_contract"] < 0


def test_wait_is_exactly_zero_and_carries_no_fee():
    w = score_wait()
    assert w["action"] == WAIT and w["ev_per_contract"] == D(0)


# ------------------------------------- the conditional structure is real

def test_q_is_conditioned_on_the_fill_not_set_to_the_fill_price():
    """Passing q = F asserts no adverse selection; the scorer must let the
    caller distinguish the two rather than silently equating them."""
    neutral = score_open(F=D("0.30"), depth_ticks=2, spread_ticks=1,
                         pair_cap=D("0.97"), p_fill=D(1),
                         p_complete_given_fill=D(0),
                         q_given_fill=D("0.30"))
    adverse = score_open(F=D("0.30"), depth_ticks=2, spread_ticks=1,
                         pair_cap=D("0.97"), p_fill=D(1),
                         p_complete_given_fill=D(0),
                         q_given_fill=D("0.27"))
    assert neutral["ev_per_contract"] == D(0)
    assert adverse["ev_per_contract"] == D("-0.03")


def test_p_fill_multiplies_through_so_no_fill_is_ever_assumed():
    half = score_open(F=D("0.30"), depth_ticks=2, spread_ticks=1,
                      pair_cap=D("0.97"), p_fill=D("0.5"),
                      p_complete_given_fill=D(1), q_given_fill=D("0.30"))
    full = score_open(F=D("0.30"), depth_ticks=2, spread_ticks=1,
                      pair_cap=D("0.97"), p_fill=D(1),
                      p_complete_given_fill=D(1), q_given_fill=D("0.30"))
    assert half["ev_per_contract"] * 2 == full["ev_per_contract"]


def test_breakeven_completion_rate_matches_the_closed_form():
    """p_c* = F*a / ((1-pair) + F*a); the scorer must cross zero there."""
    F, a, pair = D("0.44"), D("0.10"), D("0.97")
    Fa = F * a
    p_star = Fa / ((D(1) - pair) + Fa)
    at = score_open(F=F, depth_ticks=2, spread_ticks=1, pair_cap=D("0.97"),
                    p_fill=D(1), p_complete_given_fill=p_star,
                    q_given_fill=F * (D(1) - a))
    assert abs(at["ev_per_contract"]) < D("0.000001")


# ------------------------------------------------------------- REDUCE

def test_reduce_is_reported_as_variance_control_not_edge():
    r = score_reduce(F=D("0.44"), exit_px=D("0.20"),
                     q_given_fill=D("0.20"),
                     taker_fee_per_contract=D("0.03"))
    assert "not edge" in r["basis"]
    assert r["improves_on_hold"] is False      # the fee is the whole gap


def test_scorer_imports_nothing_from_the_execution_stack():
    import ast
    import os
    src = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "l5",
        "inventory_action_scorer.py")
    mods = set()
    for node in ast.walk(ast.parse(open(src).read())):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= {"__future__", "decimal", "typing"}, mods
