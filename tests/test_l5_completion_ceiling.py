"""B5: the maximum permissible completion price.

A POLICY STATED AS A NUMBER, so it can be authorized before it is used.
`completion_decision` answers "cross at the price on the book right now?".
This answers the prior question: how dear a completion is permissible at all,
given what we ACTUALLY paid, the fee we would ACTUALLY owe, the inventory we
ACTUALLY hold and the residual risk the owner has approved.

Nothing here enables crossing.
"""
from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

#: the stranded leg the 0515z/0530z book would have produced: DOWN 0.79 x 4,
#: which is a $3.21 worst case all by itself
STRANDED = dict(held_side="DOWN", held_qty="4.00", held_cost_usd="3.2100")


def ceil(**kw):
    base = dict(STRANDED, complete_qty="4.00", residual_budget_usd="7.00")
    base.update(kw)
    return TS.max_completion_price(**base)


# ---------------------------------------------------------- the four ceilings

def test_the_four_ceilings_are_computed_and_each_is_a_DIFFERENT_question():
    out = ceil()
    assert out["ok"] is True
    c = out["ceilings"]
    assert set(c) == {"positive_both", "improves", "within_budget",
                      "pair_cap"}
    # holding the stranded leg is a $3.21 worst case
    assert D(out["worst_outcome_if_held_usd"]) == D("-3.21")
    assert D(out["avg_cost_px"]) == D("0.8025")

    # positive_both is the STRONGEST: the completed book must pay on both
    # outcomes, so it is never dearer than the others
    pb = D(c["positive_both"])
    for other in ("improves", "within_budget"):
        assert pb <= D(c[other]), (other, c)
    # and the reported maximum is the minimum of ALL four
    assert D(out["max_permissible_price"]) == min(D(v) for v in c.values())
    assert out["binding_ceiling"] in c


def test_positive_both_is_consistent_with_the_pair_arithmetic():
    """A pair costing `c + q` pays min(a,b) on both outcomes, so the ceiling
    must leave the TOTAL under the matched quantity, fee included."""
    out = ceil()
    q = D(out["ceilings"]["positive_both"])
    sunk = D(out["held_cost_usd"])
    d = D(out["complete_qty"])
    cost = sunk + q * d + TS.fee(TS.TAKER_THETA, d, q)
    assert cost < min(D("4.00"), d)          # pays on both outcomes
    # one tick dearer does NOT
    q2 = q + D("0.01")
    cost2 = sunk + q2 * d + TS.fee(TS.TAKER_THETA, d, q2)
    assert cost2 >= min(D("4.00"), d)


def test_improves_can_permit_a_price_that_positive_both_forbids():
    """THE CASE THAT MATTERS FOR A STRANDED LEG. Bought at 0.79, the worst
    case is already -$3.21. A completion too dear to turn a profit can still
    leave a far smaller loss, and the policy must be able to say so -- while
    still never calling it a profit."""
    out = ceil()
    c = out["ceilings"]
    assert D(c["improves"]) > D(c["positive_both"])
    # at the `improves` ceiling the book is still a LOSS, just a smaller one
    q = D(c["improves"])
    d = D(out["complete_qty"])
    cost = D(out["held_cost_usd"]) + q * d + TS.fee(TS.TAKER_THETA, d, q)
    worst = min(D("4.00") - cost, d - cost)
    assert worst > D(out["worst_outcome_if_held_usd"])
    assert worst < 0


def test_a_tighter_residual_budget_lowers_the_budget_ceiling():
    loose = ceil(residual_budget_usd="7.00")["ceilings"]["within_budget"]
    tight = ceil(residual_budget_usd="0.50")["ceilings"]["within_budget"]
    assert D(tight) < D(loose)


def test_a_zero_budget_demands_a_non_losing_completion():
    out = ceil(residual_budget_usd="0")
    wb = D(out["ceilings"]["within_budget"])
    pb = D(out["ceilings"]["positive_both"])
    # worst >= -0 is worst >= 0, one hair weaker than worst > 0
    assert wb >= pb and wb - pb <= D("0.01")


def test_the_pair_cap_applies_to_a_CROSSING_completion_too():
    out = ceil()
    q = D(out["ceilings"]["pair_cap"])
    d = D(out["complete_qty"])
    cost = D(out["held_cost_usd"]) + q * d + TS.fee(TS.TAKER_THETA, d, q)
    eff = cost / min(D("4.00"), d)
    assert eff <= D(out["pair_cap"]) == TS.MAX_PAIR_COST


def test_a_DEARER_first_leg_lowers_every_ceiling():
    cheap = ceil(held_cost_usd="1.6000")["ceilings"]
    dear = ceil(held_cost_usd="3.2100")["ceilings"]
    for k in cheap:
        if cheap[k] is not None and dear[k] is not None:
            assert D(dear[k]) <= D(cheap[k]), k


def test_a_PARTIAL_first_fill_is_priced_on_what_actually_filled():
    """Exchange-confirmed quantity only. A 1.00 fill of a 4.00 leg needs a
    1.00 completion, and the ceiling is computed from the 1.00 cost."""
    out = ceil(held_qty="1.00", held_cost_usd="0.8025", complete_qty="1.00")
    assert out["ok"] is True
    assert D(out["avg_cost_px"]) == D("0.8025")
    assert D(out["complete_qty"]) == D("1.00")


def test_a_PARTIAL_completion_cannot_pay_on_both_outcomes_and_says_so():
    """THE POLICY DECISION, LAID OUT. Completing only part of a stranded leg
    lifts `min(a, b)` by less than the sunk cost already exceeds it, so NO
    price on the grid makes the book pay on both outcomes -- and under the
    strictest reading the whole thing is therefore refused.

    `improves` is still reachable: a partial completion genuinely reduces the
    worst case. So this is exactly the case where the answer depends on which
    ceilings the owner puts in force, which is why `ok` is the conservative
    AND of all four and the ceilings are reported individually rather than
    collapsed into one verdict.
    """
    full = ceil(complete_qty="4.00")
    part = ceil(complete_qty="2.00")
    assert full["ok"] is True
    assert part["ok"] is False
    assert part["reason"] == "NO_PERMISSIBLE_PRICE"
    # no price pays on both outcomes with only half the deficit covered
    assert part["ceilings"]["positive_both"] is None
    # but completing DOES reduce the worst case, and that ceiling is reported
    assert part["ceilings"]["improves"] is not None
    q = D(part["ceilings"]["improves"])
    d = D(part["complete_qty"])
    cost = D(part["held_cost_usd"]) + q * d + TS.fee(TS.TAKER_THETA, d, q)
    worst = min(d - cost, D("4.00") - cost)
    assert worst > D(part["worst_outcome_if_held_usd"])


# -------------------------------------------------------------- fails closed

@pytest.mark.parametrize("kw,reason", [
    ({"held_side": "SIDEWAYS"}, "HELD_SIDE_UNKNOWN"),
    ({"held_qty": "0"}, "NO_CONFIRMED_INVENTORY"),
    ({"held_qty": None}, "NO_CONFIRMED_INVENTORY"),
    ({"held_cost_usd": None}, "HELD_COST_UNKNOWN"),
    ({"complete_qty": "0"}, "NOTHING_TO_COMPLETE"),
    ({"complete_qty": None}, "NOTHING_TO_COMPLETE"),
    ({"residual_budget_usd": None}, "RESIDUAL_BUDGET_UNKNOWN"),
    ({"tick": "0"}, "TICK_UNKNOWN"),
])
def test_every_missing_input_refuses_rather_than_assuming(kw, reason):
    out = ceil(**kw)
    assert out["ok"] is False and out["reason"] == reason


def test_an_UNREADABLE_budget_is_not_an_unlimited_one():
    """The whole point of a budget is that it binds. A None must refuse."""
    assert ceil(residual_budget_usd=None)["reason"] == (
        "RESIDUAL_BUDGET_UNKNOWN")
    assert ceil(residual_budget_usd="not-a-number")["reason"] == (
        "RESIDUAL_BUDGET_UNKNOWN")


def test_a_book_so_dear_that_NO_price_is_permissible_says_so():
    """Held at a cost no completion can rescue inside the budget."""
    out = ceil(held_cost_usd="11.00", residual_budget_usd="0.10")
    assert out["ok"] is False
    assert out["reason"] == "NO_PERMISSIBLE_PRICE"
    assert out["ceilings"]["within_budget"] is None


def test_an_UP_held_leg_is_symmetric():
    out = TS.max_completion_price(
        held_side="UP", held_qty="4.00", held_cost_usd="0.6800",
        complete_qty="4.00", residual_budget_usd="7.00")
    assert out["ok"] is True
    assert out["deficient_side"] == "DOWN"
    # a cheap held leg leaves plenty of room
    assert D(out["ceilings"]["positive_both"]) > D("0.80")
