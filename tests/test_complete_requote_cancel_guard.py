"""A COMPLETE is never cancelled for repricing without a legal replacement.

OWNER: "A complement must never be cancelled for repricing unless its
replacement passes all applicable preflight checks."

WHY THIS ASYMMETRY IS CORRECT, and why OPEN/EXTEND deliberately keep the
blanket behaviour. OPEN and EXTEND cancel on a moved touch and re-derive a
price on the next poll; if no legal price exists they simply do not open and
nothing is at risk. A COMPLETE is the opposite case -- it is the only order
working an inventory we have ALREADY PAID FOR, so cancelling it without a
replacement leaves the window with no complement resting at all. That is
strictly worse than the static order it replaced, and it is the defect these
tests close.

THE GUARD IS CURRENTLY DEAD CODE, ON PURPOSE. requote_relations() excludes
"complete" until an owner arms PMUS_SPRING_COMPLETE_REQUOTE. The fix lands
first so that arming the flag is a safe act rather than a hopeful one.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as ses

_SRC = open(ses.__file__).read()
TICK = D("0.01")


def _guard() -> str:
    """The guard block, from the requote condition to the plain fallback."""
    start = _SRC.index("if relation_ in requote_relations() and touch != px_:")
    end = _SRC.index('return f"touch_moved:{touch}"', start)
    return _SRC[start:end]


# ------------------------------------------------- the guard is where it must be
def test_the_guard_sits_inside_the_requote_condition():
    g = _guard()
    assert 'if relation_ == "complete":' in g
    assert "complement_plan(" in g


def test_the_guard_prices_the_replacement_BEFORE_returning_a_cancel():
    g = _guard()
    priced = g.index("complement_plan(")
    cancelled = g.index('return f"touch_moved:{touch}|replacement:')
    assert priced < cancelled, "the cancel must not precede the pricing"


def test_no_legal_replacement_keeps_the_order_resting():
    g = _guard()
    block = g[g.index("if new_px_ is None:"):]
    head = block[:block.index("\n", block.index("return"))]
    assert "return None" in head, head


def test_a_replacement_that_is_not_better_keeps_the_order_resting():
    g = _guard()
    i = g.index("if D(str(new_px_)) <= px_:")
    assert "return None" in g[i:i + 240]


def test_a_pricing_error_fails_CLOSED_which_means_keep_resting():
    """An exception while pricing is not a reason to destroy the order."""
    g = _guard()
    i = g.index("except Exception as exc:")
    tail = g[i:g.index("if new_px_ is None:", i)]
    assert "return None" in tail
    assert "touch_moved" not in tail


def test_the_cancel_reason_names_the_replacement_it_holds():
    """So the tape shows a cancel was only ever issued with a price in hand."""
    assert 'return f"touch_moved:{touch}|replacement:{new_px_}"' in _SRC


# --------------------------------------------- open/extend are byte-unchanged
def test_open_and_extend_still_cancel_on_a_bare_moved_touch():
    assert _SRC.count('return f"touch_moved:{touch}"') == 1
    g = _guard()
    # the guard is scoped to complete only; nothing in it mentions the others
    assert '"open"' not in g and '"extend"' not in g


def test_the_crossing_guard_still_precedes_everything():
    cross = _SRC.index('return f"would_cross:{opp_ask}"')
    assert cross < _SRC.index("if relation_ in requote_relations()")


# ------------------------------------------- the preflight it actually reuses
def test_complement_plan_is_the_same_preflight_the_executor_uses():
    """Not a second copy of the ceiling logic that could drift from it."""
    g = _guard()
    for forbidden in ("SPRING_PAIR_TARGET", "pair_cost_max(",
                      "completion_pair_ceiling(", "0.98", "0.96"):
        assert forbidden not in g, forbidden


@pytest.mark.parametrize("avg,expect_none", [
    (D("0.30"), False), (D("0.44"), False), (D("0.60"), False),
    (D("0.90"), True), (D("0.97"), True), (D("0.98"), True)])
def test_no_legal_replacement_is_a_REACHABLE_state(avg, expect_none):
    """The guard's first branch is not hypothetical: a cost basis that leaves
    no fee-safe room returns None, and that is exactly when cancelling would
    have stranded us."""
    plan = ses.complement_plan(True, avg, {"bid": D("0.50"), "ask": D("0.51")},
                               TICK)
    assert (plan["px"] is None) is expect_none, plan


def test_the_fee_safe_price_is_book_responsive_so_branch_b_is_live():
    """If the legal price never moved, the 'not better' branch would be the
    only one that ever fired. It does move, through the post-only trim."""
    lo = ses.complement_plan(True, D("0.30"),
                             {"bid": D("0.50"), "ask": D("0.51")}, TICK)
    hi = ses.complement_plan(True, D("0.30"),
                             {"bid": D("0.30"), "ask": D("0.31")}, TICK)
    assert lo["px"] != hi["px"], (lo, hi)


# ----------------------------------------------------- still dark by default
def test_the_flag_remains_off_and_the_guard_is_not_live_yet(monkeypatch):
    monkeypatch.delenv(ses.COMPLETE_REQUOTE_ENV, raising=False)
    assert ses.complete_requote_enabled() is False
    assert "complete" not in ses.requote_relations()
