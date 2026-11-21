"""No order may leave the MATCHED inventory costing more than $1.00 a pair.

OWNER 2026-09-10: "Neither COMPLETE nor EXTEND is allowed to make the total
matched inventory cost more than $1 per pair. That protects the whole
position, not merely the newest trade."

Found by simulation, not by a live loss: the <=1.00 ceiling on the complement
gates the MARGINAL pair, and a_up moves underneath already-matched inventory
when an EXTEND lands after a cheap COMPLETE.
"""
from __future__ import annotations

import random
from decimal import Decimal as D

from src import pmus_spring_l5_session as ses


def _inv(up, dn, cost_up, cost_dn):
    i = ses.Inventory()
    i.up, i.dn = D(up), D(dn)
    i.cost_up, i.cost_dn = D(cost_up), D(cost_dn)
    return i


def test_the_exact_counterexample_is_refused_at_the_extend():
    """OPEN 10@0.10 -> COMPLETE 10@0.85 (marginal 0.95, allowed) ->
    EXTEND 10@0.95 would take the average to 1.375. Paid $19.00 for
    +1.00 / -9.00."""
    inv = _inv("10", "10", "1.00", "8.50")
    assert ses.matched_pair_cost_after(inv, True, D("0.95"), D("10")) == D("1.375")
    assert ses.pair_invariant_ok(inv, True, D("0.95"), D("10")) is False


def test_a_cheap_extend_on_the_same_position_is_allowed():
    """Not a ban on EXTEND -- L5's persistence survives, bounded."""
    inv = _inv("10", "10", "1.00", "8.50")
    assert ses.pair_invariant_ok(inv, True, D("0.14"), D("10")) is True


def test_the_first_leg_is_unconstrained_because_nothing_is_matched_yet():
    flat = _inv("0", "0", "0", "0")
    assert ses.matched_pair_cost_after(flat, True, D("0.95"), D("5")) is None
    assert ses.pair_invariant_ok(flat, True, D("0.95"), D("5")) is True


def test_it_binds_on_complete_too_not_only_extend():
    inv = _inv("10", "2", "8.00", "0.10")          # a_up 0.80
    assert ses.pair_invariant_ok(inv, False, D("0.90"), D("8")) is False


def test_exactly_one_dollar_is_allowed_and_a_hair_over_is_not():
    inv = _inv("10", "10", "4.00", "5.00")          # a_up .40 a_dn .50 = .90
    assert ses.matched_pair_cost_after(inv, True, D("0.60"), D("10")) == D("1.00")
    assert ses.pair_invariant_ok(inv, True, D("0.60"), D("10")) is True
    assert ses.pair_invariant_ok(inv, True, D("0.61"), D("10")) is False


# ----------------------------------------------------- the property, restored

def test_the_worst_case_bound_now_holds_across_random_sequences():
    """THE POINT OF THE WHOLE RULE, and the check that failed before it:

        worst settlement branch  >=  -(cash paid for the UNMATCHED contracts)

    That is what makes per-market worst case bounded by first-leg spend, which
    is what makes breadth safe. It holds iff the AVERAGE pair stays <= 1.00.
    Before this invariant the same simulation breached it by -6.99.
    """
    random.seed(7)
    worst_gap = None
    for _ in range(20000):
        inv = _inv("0", "0", "0", "0")
        for _ in range(random.randint(1, 8)):
            side_up = random.random() < 0.5 or inv.up == 0
            px = D(str(round(random.uniform(0.01, 0.99), 3)))
            qty = D(str(round(random.uniform(0.1, 8), 2)))
            if not ses.pair_invariant_ok(inv, side_up, px, qty):
                continue                      # the gate refuses it, as live
            if side_up:
                inv.up += qty; inv.cost_up += px * qty
            else:
                inv.dn += qty; inv.cost_dn += px * qty
        S = inv.cost_up + inv.cost_dn
        worst = min(inv.up, inv.dn) - S
        if inv.up >= inv.dn:
            unmatched = ((inv.cost_up / inv.up) * (inv.up - inv.dn)
                         if inv.up else D("0"))
        else:
            unmatched = ((inv.cost_dn / inv.dn) * (inv.dn - inv.up)
                         if inv.dn else D("0"))
        gap = worst + unmatched
        if worst_gap is None or gap < worst_gap:
            worst_gap = gap
    assert worst_gap >= D("-0.000001"), \
        f"worst case escaped the unmatched-cash bound by {worst_gap}"


def test_the_gate_is_wired_into_the_open_extend_path_not_merely_defined():
    import ast
    tree = ast.parse(open(ses.__file__).read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_session_body")
    src = ast.unparse(fn)
    assert "pair_invariant_ok(inv, side_up, px, qty)" in src
    assert "matched_pair_ceiling" in src
