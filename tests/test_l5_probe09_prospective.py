"""The Probe 09 candidate: classify on realized PLUS what is still out.

Offline. These tests pin the rule's behaviour, pin the fact that it is NOT
wired to anything live, and pin the counterfactual result -- including the
part that counts against it, because a candidate whose failure mode is not
in the test suite is a candidate nobody can argue with later.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_prospective_class as PC
from src.l5_live_execution import (FLAT_OPEN, INCREASE_RESIDUAL,
                                   REDUCE_RESIDUAL)

SEQ = "analysis/pmus/evidence/probe08/probe08b_sequence.json"


# ------------------------------------------------------------- the rule

def test_prospective_residual_is_realized_plus_outstanding():
    assert PC.prospective_residual(D("-3.93"), D("4.92")) == D("0.99")


@pytest.mark.parametrize("realized,outstanding,side,expected", [
    ("0", "0", "DOWN", FLAT_OPEN),
    ("0", "-1.68", "DOWN", INCREASE_RESIDUAL),     # the Probe 08 gap
    ("0", "-1.68", "UP", REDUCE_RESIDUAL),
    ("-1.68", "0", "UP", REDUCE_RESIDUAL),
    ("-1.68", "2.67", "UP", INCREASE_RESIDUAL),    # already long prospectively
    ("-3.93", "3.93", "UP", FLAT_OPEN),            # exactly flat when filled
])
def test_the_truth_table(realized, outstanding, side, expected):
    assert PC.classify(D(realized), D(outstanding), side) == expected


def test_the_realized_basis_reproduces_probe_08():
    """Same function, old answer, so the control is exactly the control."""
    assert PC.classify(D(0), D("-1.68"), "DOWN", basis=PC.REALIZED) == FLAT_OPEN


def test_an_unknown_basis_is_refused_not_guessed():
    with pytest.raises(ValueError):
        PC.classify(D(0), D(0), "UP", basis="SOMETHING_ELSE")


def test_only_increase_residual_is_refused():
    for realized, out, side in (("0", "0", "UP"), ("-2", "0", "UP"),
                                ("2", "0", "DOWN")):
        assert PC.decide(D(realized), D(out), side)["permitted"] is True
    d = PC.decide(D("0"), D("-1.68"), "DOWN")
    assert d["permitted"] is False
    assert d["refusal"] == "IMBALANCE_INCREASE_SUPPRESSED"


def test_the_decision_shows_its_working():
    d = PC.decide(D("-3.93"), D("4.92"), "UP")
    assert d["realized_residual"] == "-3.93"
    assert d["outstanding_residual"] == "4.92"
    assert d["prospective_residual"] == "0.99"
    assert d["basis"] == PC.PROSPECTIVE


# ------------------------------------------------- the outstanding book

def test_an_order_reserves_direction_at_full_size():
    b = PC.OutstandingBook()
    b.place("a", "DOWN", D("1.68"))
    assert b.signed() == D("-1.68") and b.count() == 1


def test_a_fill_returns_the_reservation():
    b = PC.OutstandingBook()
    b.place("a", "UP", D("2.25"))
    b.fill("a", D("2.25"))
    assert b.signed() == 0 and b.count() == 0


def test_a_partial_fill_returns_only_what_filled():
    b = PC.OutstandingBook()
    b.place("a", "UP", D("2.25"))
    b.fill("a", D("1.00"))
    assert b.signed() == D("1.25")


def test_a_terminal_order_returns_its_reservation():
    b = PC.OutstandingBook()
    b.place("a", "UP", D("2.25"))
    b.terminate("a")
    assert b.signed() == 0


def test_an_unfilled_order_keeps_reserving():
    """The property that makes the candidate fail on Probe 08's window."""
    b = PC.OutstandingBook()
    b.place("a", "UP", D("0.42"))
    b.place("b", "UP", D("2.25"))
    assert b.signed() == D("2.67")


# --------------------------------------------- the Probe 08 counterfactual

def sequence():
    return json.load(open(SEQ))["orders"]


def test_the_frozen_sequence_is_present_and_complete():
    o = sequence()
    assert len(o) == 8
    assert [x["venue_state"] for x in o].count("ORDER_STATE_FILLED") == 5


def test_the_candidate_suppresses_the_second_down_open():
    """The defect it was designed for, on the real numbers."""
    o = sequence()
    first, second = o[0], o[1]
    assert first["side"] == second["side"] == "DOWN"
    assert second["inventory_class"] == FLAT_OPEN, "how Probe 08 saw it"
    # after the first is placed and has not yet filled
    b = PC.OutstandingBook()
    b.place(first["venue_order_id"], first["side"], D(first["qty"]))
    d = PC.decide(D(0), b.signed(), second["side"])
    assert d["inventory_class"] == INCREASE_RESIDUAL
    assert d["permitted"] is False


def test_the_candidate_then_starves_the_repairs_that_filled():
    """The cost, pinned so it cannot be quietly dropped from a summary."""
    o = {x["seq"]: x for x in sequence()}
    b = PC.OutstandingBook()
    # seq 3 and seq 4 are placed and neither ever fills
    b.place("s3", "UP", D(o[3]["qty"]))
    b.place("s4", "UP", D(o[4]["qty"]))
    assert b.signed() == D("2.67")
    # realized is -1.68 because only the first DOWN open was allowed
    for seq in (5, 6, 7, 8):
        d = PC.decide(D("-1.68"), b.signed(), o[seq]["side"])
        assert d["inventory_class"] == INCREASE_RESIDUAL, seq
        assert d["permitted"] is False, seq
    # and seq 6, 7, 8 are precisely the three that DID fill
    assert all(o[s]["venue_state"] == "ORDER_STATE_FILLED" for s in (6, 7, 8))


def test_under_the_realized_basis_every_probe_08_order_is_permitted():
    o = sequence()
    b = PC.OutstandingBook()
    realized = {"UP": D(0), "DOWN": D(0)}
    for x in o:
        d = PC.decide(realized["UP"] - realized["DOWN"], b.signed(),
                      x["side"], basis=PC.REALIZED)
        assert d["permitted"] is True, x["seq"]
        b.place(x["venue_order_id"], x["side"], D(x["qty"]))


# ----------------------------------------------------- offline, and frozen

def module_ast(path):
    import ast
    return ast.parse(pathlib.Path(path).read_text())


def imported_names(tree):
    import ast
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            out.add(n.module or "")
            out.update(a.name for a in n.names)
    return out


def called_names(tree):
    import ast
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_the_candidate_imports_no_transport_and_no_network():
    """Asserted on the parse tree, so prose about what it does not do
    cannot fail it -- an earlier version of this test scanned the
    docstring and tripped over the sentence promising the opposite."""
    names = imported_names(module_ast("src/l5_prospective_class.py"))
    for forbidden in ("requests", "http", "urllib", "socket",
                      "src.l5_pmus_transport", "src.pmus_auth",
                      "src.pmus_q1_executor", "src.l5_order_body"):
        assert forbidden not in names, forbidden


def test_the_candidate_calls_no_order_verb():
    calls = called_names(module_ast("src/l5_prospective_class.py"))
    for verb in ("submit", "cancel", "post", "request", "getenv"):
        assert verb not in calls, verb


def test_the_candidate_imports_only_the_classifier_it_reuses():
    names = imported_names(module_ast("src/l5_prospective_class.py"))
    assert "src.l5_live_execution" in names
    assert "inventory_class" in names
    assert not any(n.startswith("scripts.") for n in names)


def test_nothing_live_imports_the_candidate():
    """Offline until something authorizes otherwise."""
    for path in ("src/l5_live_execution.py",
                 "scripts/l5_pmus_live_oneshot.py",
                 "src/l5_pmus_live_wiring.py"):
        assert "l5_prospective_class" not in imported_names(module_ast(path)), path


def test_l5_native_is_untouched_by_this_work():
    """The frozen rule still decides side, price, size and timing."""
    names = imported_names(module_ast("src/l5_prospective_class.py"))
    for frozen in ("src.l5_faithful_translation", "src.l5_native_shadow",
                   "l5_native", "pmus_translate"):
        assert frozen not in names, frozen
