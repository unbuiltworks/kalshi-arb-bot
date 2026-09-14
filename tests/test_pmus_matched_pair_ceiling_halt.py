"""LIVE HALT 2026-09-14T13:26:52Z: the matched-pair-ceiling REFUSAL raised
instead of refusing.

    status  HALT
    reason  UnboundLocalError: cannot access local variable 'dec'
            where it is not associated with a value
    stop    matched_pair_ceiling:1.000326530612244897959183674>1.00
    slug    aec-wta-ararus-samste-2026-09-14

THE REFUSAL WAS CORRECT AND MUST STAY EXACTLY FAIL-CLOSED. A pair above 1.00
has to stop. The defect was solely that the refusal path annotated `dec`
before binding it: `dec` is assigned only inside OTHER conditional branches
(the off-distribution and residual-cap paths), so a pass that reached the
ceiling refusal first read an unbound local and the exception escaped as a
HALT that stopped the whole campaign.
"""
import ast
import inspect
from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as S

# the exact average matched-pair cost the live breach computed
LIVE_BREACH = D("1.000326530612244897959183674")


def _inv(up, cost_up, dn, cost_dn):
    inv = S.Inventory()
    inv.up, inv.cost_up = D(up), D(cost_up)
    inv.dn, inv.cost_dn = D(dn), D(cost_dn)
    return inv


def test_live_breach_value_is_reproduced_exactly():
    """Drive the real number from the halt, not an approximation of it."""
    inv = _inv("3.9", "1.9016", "4.9", "2.4")
    after = S.matched_pair_cost_after(inv, True, D("0.60"), D("1.0"))
    assert after == LIVE_BREACH
    assert after > S.completion_pair_ceiling()


def test_live_breach_refuses_and_does_not_raise():
    """The whole point: a safe refusal, not an exception."""
    inv = _inv("3.9", "1.9016", "4.9", "2.4")
    assert S.pair_invariant_ok(inv, True, D("0.60"), D("1.0")) is False


@pytest.mark.parametrize("cost_up,expect_ok,label", [
    ("1.8999", True, "0.99997959... just BELOW 1.00"),
    ("1.9", True, "exactly 1.00"),
    ("1.9016", False, "1.000326530612... just ABOVE 1.00 (the LIVE value)"),
])
def test_boundary_below_at_and_above_the_ceiling(cost_up, expect_ok, label):
    """<= 1.00 passes, > 1.00 refuses. The boundary is INCLUSIVE at par."""
    inv = _inv("3.9", cost_up, "4.9", "2.4")
    after = S.matched_pair_cost_after(inv, True, D("0.60"), D("1.0"))
    ok = S.pair_invariant_ok(inv, True, D("0.60"), D("1.0"))
    assert ok is expect_ok, f"{label}: after={after}"
    if expect_ok:
        assert after <= S.completion_pair_ceiling(), label
    else:
        assert after > S.completion_pair_ceiling(), label


def test_exactly_at_par_is_allowed_not_refused():
    """A pair of exactly 1.00 is not a breach; only strictly above is."""
    inv = _inv("3.9", "1.9", "4.9", "2.4")
    assert S.matched_pair_cost_after(inv, True, D("0.60"), D("1.0")) == D("1.00")
    assert S.pair_invariant_ok(inv, True, D("0.60"), D("1.0")) is True


def test_no_matched_inventory_means_no_ceiling_to_breach():
    """One empty side -> None -> allowed, unchanged behaviour."""
    inv = _inv("0", "0", "4.9", "2.4")
    assert S.matched_pair_cost_after(inv, False, D("0.99"), D("1.0")) is None
    assert S.pair_invariant_ok(inv, False, D("0.99"), D("1.0")) is True


def _ceiling_branch() -> ast.If:
    """The `if not pair_invariant_ok(...)` block inside the session loop."""
    tree = ast.parse(inspect.getsource(S))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.UnaryOp):
            continue
        if not isinstance(node.test.op, ast.Not):
            continue
        call = node.test.operand
        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == \
                "pair_invariant_ok":
            return node
    raise AssertionError("ceiling refusal branch not found")


def test_ceiling_branch_binds_dec_before_it_annotates_it():
    """THE REGRESSION. Walk the refusal branch in order: the first statement
    that touches `dec` must be the assignment, never a subscript write."""
    branch = _ceiling_branch()
    seen_assign = False
    for node in ast.walk(branch):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "dec":
                    seen_assign = True
    assert seen_assign, "ceiling branch never binds `dec` -- the live HALT"

    # order matters, not just presence: find the line numbers
    assign_lines = [n.lineno for n in ast.walk(branch)
                    if isinstance(n, ast.Assign)
                    for t in n.targets
                    if isinstance(t, ast.Name) and t.id == "dec"]
    use_lines = [n.lineno for n in ast.walk(branch)
                 if isinstance(n, ast.Subscript)
                 and isinstance(n.value, ast.Name) and n.value.id == "dec"]
    assert assign_lines, "no binding of dec in the refusal branch"
    assert min(assign_lines) < min(use_lines), (
        f"dec used at line {min(use_lines)} before it is bound at "
        f"{min(assign_lines)} -- this is the UnboundLocalError")


def test_refusal_branch_still_stops_and_annotates_the_same_way():
    """The fix must not have softened the refusal: it still sets
    stop_reason, marks the decision a no-op, and counts the action."""
    src = ast.get_source_segment(inspect.getsource(S), _ceiling_branch()) or ""
    assert "matched_pair_ceiling" in src
    assert "mark_noop" in src
    assert "cap_binding" in src
    assert "continue" in src
    # and it never submits anything from this branch
    for forbidden in ("submit", "place", "_order("):
        assert forbidden not in src, forbidden


def test_ceiling_and_risk_envelope_are_untouched():
    """No threshold moved as part of this fix."""
    assert S.completion_pair_ceiling() == D("1.00")
    # 3 is the module default; production runs 6 via its own env. Pinned so a
    # change to EITHER shows up as a test failure rather than silently.
    assert S.max_residual_units() == 3
