"""The combined-loss gate CRASHED the live campaign. 16 tests missed it.

2026-09-10T04:47:28Z, commit e0fdd13a, market aec-itfwo-hinino-vaiadk:

    session_end  status HALT
    reason "UnboundLocalError:cannot access local variable 'dec' where it
            is not associated with a value"
    campaign_end CAMPAIGN_STOP:HALT
    next "none: this status is an owner decision or a stop, and is never
          auto-restarted"

The campaign stayed dead for 6h43m holding 14.97 unhedged YES at avg 0.4358,
$6.5244 spent. Nothing recovered it, by design -- money-moving sessions fail
closed and never self-relaunch.

WHY THE EXISTING SUITE DID NOT SEE IT. test_combined_loss_budget.py has 16
tests and every one of them calls a HELPER -- guaranteed_settlement_floor,
unsettled_floor_total, combined_loss, combined_budget_tripped,
reduces_risk_only. The arithmetic was right. Not one test executed the
BRANCH that consumes it, and the branch's first statement referenced a name
bound forty lines further down. Correct primitives, unreachable wiring.

So these tests are deliberately not more arithmetic. One drives a real
session through the branch; the rest are a structural guard over the whole
action loop, because `dec` was not special -- any name read at the top of
that loop and assigned only in a lower branch has exactly this shape.

The stale-binding case is worse than the crash and silent: on the second
iteration `dec` survives from the previous pass, so the gate would annotate
the WRONG decision row and the refusal would be attributed to a trade that
was not refused.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from src import pmus_spring_l5_session as s

_SRC = inspect.getsource(s)

# The exact statement whose absence killed the campaign.
_BINDING = '                dec = report["decisions"][-1]\n' \
           '                mark_noop(dec, f"combined_loss_stop:'


# --------------------------------------------------------------- structural
def _action_loop(src: str) -> ast.While:
    tree = ast.parse(src)
    loops = [n for n in ast.walk(tree)
             if isinstance(n, ast.While)
             and "action_budget()" in ast.unparse(n.test)]
    assert len(loops) == 1, "exactly one bounded action loop"
    return loops[0]


def _rows_used_before_binding(src: str) -> list:
    """Every `mark_noop(<bare name>, ...)` in the action loop whose row is
    not bound earlier in its own statement list or an enclosing one.

    Deliberately NOT a general liveness analysis. It answers one question --
    "is the decision row this branch annotates bound on the path that reaches
    it?" -- which is the question the crash asked.
    """
    bad = []

    def _binds(st, name):
        for node in ast.walk(st):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == name:
                        return True
        return False

    def _walk(stmts, bound: frozenset):
        for i, st in enumerate(stmts):
            for call in [n for n in ast.walk(st)
                         if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name)
                         and n.func.id == "mark_noop"
                         and n.args
                         and isinstance(n.args[0], ast.Name)]:
                name = call.args[0].id
                # bound by an enclosing block, or earlier in THIS block, or
                # earlier inside the very statement holding the call
                here = any(_binds(prev, name) for prev in stmts[:i])
                inner = _precedes_within(st, call, name)
                if name not in bound and not here and not inner:
                    bad.append((call.lineno, name))
            nxt = bound | ({t.id for t in _targets(st)} if True else set())
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(st, field, None)
                if isinstance(sub, list):
                    _walk(sub, nxt | frozenset(
                        n for prev in stmts[:i] for n in _names_bound(prev)))
    _walk(_action_loop(src).body, frozenset())
    return bad


def _targets(st) -> list:
    out = []
    if isinstance(st, ast.Assign):
        out += [t for t in st.targets if isinstance(t, ast.Name)]
    return out


def _names_bound(st) -> set:
    return {n.id for node in ast.walk(st)
            if isinstance(node, ast.Assign)
            for n in node.targets if isinstance(n, ast.Name)}


def _precedes_within(stmt, call, name) -> bool:
    """`dec = ...` earlier inside the same compound statement as the call."""
    for node in ast.walk(stmt):
        if isinstance(node, ast.Assign) and node.lineno < call.lineno:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return True
    return False


def test_no_mark_noop_in_the_action_loop_annotates_an_unbound_row():
    """The class-level guard, not just the one line that crashed."""
    assert _rows_used_before_binding(_SRC) == []


def test_the_guard_actually_catches_the_shape_that_crashed():
    """A test that cannot fail on the defect is decoration. Delete the
    binding and the guard must name the very line the venue killed us on."""
    broken = _SRC.replace(_BINDING, '                mark_noop(dec, '
                                    'f"combined_loss_stop:')
    assert broken != _SRC, "the fix's binding line moved; retarget this test"
    found = _rows_used_before_binding(broken)
    assert found, "the guard does not detect an unbound decision row"
    assert all(n == "dec" for _, n in found), found


def test_the_gate_binds_the_row_it_annotates_rather_than_inheriting_one():
    """Pin the fix by value: the binding is INSIDE the gate, immediately
    before the annotation, so iteration N cannot mark iteration N-1's row."""
    i = _SRC.index('if relation in ("open", "extend") and risk_reduction_only:')
    body = _SRC[i:i + 1600]
    bind = body.index('dec = report["decisions"][-1]')
    use = body.index("mark_noop(dec,")
    assert bind < use, "the row must be bound before it is annotated"
    assert "combined_loss_stop" in body[use:use + 200]


# ------------------------------------------------------------- behavioural
def _tripped_session(monkeypatch, tmp_path, relation: str):
    """A real session, armed, holding, with the combined budget tripped.

    combined_budget_tripped is forced rather than reconstructed from a
    ledger: its arithmetic already has 16 tests and is not what broke. What
    broke is that no test had ever ENTERED the branch it controls.
    """
    from tests.test_pmus_spring_l5_session import (Fake, _script_proposals,
                                                   _ticking)
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                       _ledger_with_fill)
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    _script_proposals(monkeypatch, [relation])
    monkeypatch.setattr(s, "held_slugs_now", lambda: ["held"])
    monkeypatch.setattr(s, "combined_budget_tripped", lambda *a, **k: True)
    fake = Fake(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    return s.run_session(fake, clock=_ticking(), sleep=lambda x: None,
                         wall=lambda: 1000.0, slug="held", gate_ok=True,
                         resume=True)


@pytest.mark.parametrize("relation", ["open", "extend"])
def test_the_branch_that_killed_the_campaign_now_runs(monkeypatch, tmp_path,
                                                      relation):
    """THE REGRESSION. Both relations the gate names, driven end to end."""
    rep = _tripped_session(monkeypatch, tmp_path, relation)
    assert rep["status"] != "HALT", rep.get("reason")
    assert "UnboundLocalError" not in str(rep.get("reason") or "")


@pytest.mark.parametrize("relation", ["open", "extend"])
def test_the_refusal_is_recorded_on_a_decision_row(monkeypatch, tmp_path,
                                                   relation):
    """Not crashing is half of it. The refusal has to be auditable, or a
    risk stop that fires leaves no evidence that it fired."""
    rep = _tripped_session(monkeypatch, tmp_path, relation)
    marked = [d for d in rep.get("decisions", [])
              if "combined_loss_stop" in list(d.get("cap_binding", []))]
    assert marked, [d.get("cap_binding") for d in rep.get("decisions", [])]
    assert all(d.get("risk_reduction_only") is True for d in marked)


def test_the_session_reports_the_lane_even_before_any_action(monkeypatch,
                                                             tmp_path):
    """report[risk_reduction_only] is set above the loop, so it is present
    whether or not a proposal ever reached the gate."""
    rep = _tripped_session(monkeypatch, tmp_path, "extend")
    assert rep["risk_reduction_only"] is True
    assert rep["combined_loss_budget_usd"] == str(s.daily_loss_stop())


def test_no_new_exposure_is_opened_while_the_budget_is_tripped(monkeypatch,
                                                               tmp_path):
    """The gate's actual job. Refusing to crash is not the same as refusing
    to trade, and only one of those protects the budget."""
    rep = _tripped_session(monkeypatch, tmp_path, "open")
    for o in rep.get("orders", []):
        assert o["relation"] not in ("open", "extend"), o
