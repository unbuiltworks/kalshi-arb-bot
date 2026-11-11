"""Post-imbalance reconstruction: no hindsight, and the arithmetic is right.

Governed by analysis/l5/RESIDUAL_REPAIR_PREREGISTRATION.md (dd2e2637), which
names the settlement outcome, resolution side and realized_usd as forbidden
inputs. The first test here enforces that by reading the module's source: a
promise in a docstring is not a control.

THE ARITHMETIC THAT SURPRISED ME, pinned here because it changed the reading
of the whole study: completing a residual ALWAYS improves the guaranteed
floor. Completing q units at price p costs pq and lifts min(up,dn) by q, so
the floor moves by q(1-p), which is positive for every p below 1.00. So
"does completing improve the floor" is not a discriminator at all -- it is
true 220 times out of 220 -- and the binding constraint is the pair ceiling.
A metric that is always true measures nothing, and I had pre-registered it as
though it would separate cases.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

from src import l5_residual_reconstruction as R


def _inv(up, dn, spent, avg_up=None, avg_dn=None):
    return {"up": up, "dn": dn, "spent": spent,
            "avg_up": avg_up, "avg_dn": avg_dn,
            "residual": str(D(up) - D(dn))}


# ------------------------------------------------------- no hindsight
def test_no_settlement_field_is_read_anywhere():
    """The pre-registration's forbidden list, enforced against the CODE.

    Docstrings and comments are stripped first. A first version matched the
    raw source and failed on the module's own sentence saying these fields
    are not read -- banning the word rather than the use, which is the same
    mistake the completion-ceiling label test made an hour earlier. Prose is
    not code, and a check that cries wolf gets waived through next time."""
    import ast
    import inspect
    banned = {"realized_usd", "resolution_side", "resolution",
              "realized_confirmed", "winning_side", "settled_side"}
    read: set = set()
    for node in ast.walk(ast.parse(inspect.getsource(R))):
        # x.get("field") / x.get("field", default)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "pop")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            read.add(node.args[0].value)
        # x["field"]
        elif (isinstance(node, ast.Subscript)
              and isinstance(node.slice, ast.Constant)
              and isinstance(node.slice.value, str)):
            read.add(node.slice.value)
        # x.field
        elif isinstance(node, ast.Attribute):
            read.add(node.attr)
    leaked = banned & read
    assert not leaked, f"settlement fields read: {sorted(leaked)}"


# ------------------------------------------------------- the floor
def test_completing_always_improves_the_floor():
    """q(1-p) > 0 for every p < 1.00. Pinned because I pre-registered this
    as a discriminator and it cannot be one."""
    inv = _inv("10", "4", "5.00", "0.40", "0.25")
    for px in ("0.01", "0.30", "0.60", "0.99"):
        got = R.complete_at(inv, D(px))
        assert got["improves_floor"] is True, px
        gain = D(got["floor_delta_usd"])
        assert gain == (D("6") * (R.ONE - D(px))).quantize(D("0.0001"))


def test_the_pair_ceiling_is_what_actually_binds():
    """Same position, two prices: both lift the floor, only one is allowed."""
    inv = _inv("10", "4", "5.00", "0.40", "0.25")
    cheap, dear = R.complete_at(inv, D("0.30")), R.complete_at(inv, D("0.95"))
    assert cheap["improves_floor"] and dear["improves_floor"]
    assert cheap["within_pair_ceiling"] is True
    assert dear["within_pair_ceiling"] is False


def test_a_flat_position_has_no_completion_to_offer():
    """None, not a zero-cost opportunity. Reporting one would invent an
    option that did not exist at that state."""
    assert R.complete_at(_inv("5", "5", "4.00", "0.40", "0.40"),
                         D("0.50")) is None


def test_floor_now_is_min_minus_spent():
    assert R.floor_now(_inv("10", "4", "5.00")) == D("-1.00")


# ------------------------------------------- reparative vs damaging extend
def test_an_extend_below_the_surplus_average_is_reparative():
    """The measured nikizq case: surplus NO averaging 0.49, extends near
    0.38 pulled it to 0.4226 and dug the pair out from 1.15 to 1.0732."""
    inv = _inv("3.78", "5", "4.9448", "0.66", "0.49")
    assert R.extend_is_reparative(inv, D("0.38")) is True


def test_an_extend_above_the_surplus_average_is_not():
    """That is the 2026-09-10 loophole, not repair."""
    inv = _inv("3.78", "5", "4.9448", "0.66", "0.49")
    assert R.extend_is_reparative(inv, D("0.60")) is False


def test_a_flat_position_has_no_surplus_side():
    assert R.extend_is_reparative(_inv("5", "5", "4.00", "0.4", "0.4"),
                                  D("0.3")) is None


# --------------------------------------------- decision/order pairing
def test_orders_pair_only_with_decisions_that_acted():
    """A held or refused decision submitted nothing. Zipping decisions to
    orders directly slides every later pairing by one after the first hold,
    and this module's whole job is knowing which price was on screen at
    which state."""
    session = {
        "decisions": [{"acted": "open"}, {"acted": "hold"},
                      {"acted": "extend"}, {"acted": "complete"}],
        "orders": [{"stage": "spring_open_1", "px": "0.40"},
                   {"stage": "spring_extend_2", "px": "0.38"},
                   {"stage": "spring_complete_3", "px": "0.60"}]}
    paired = R.pair_decisions_to_orders(session)
    assert [(d["acted"], (o or {}).get("px")) for d, o in paired] == [
        ("open", "0.40"), ("hold", None), ("extend", "0.38"),
        ("complete", "0.60")]


def test_more_acting_decisions_than_orders_does_not_crash_or_misalign():
    session = {"decisions": [{"acted": "extend"}, {"acted": "extend"}],
               "orders": [{"px": "0.38"}]}
    paired = R.pair_decisions_to_orders(session)
    assert paired[0][1]["px"] == "0.38" and paired[1][1] is None


# ------------------------------------------------- scope starts at imbalance
def test_states_begin_at_the_first_imbalance_not_the_first_decision(tmp_path):
    session = {"slug": "m", "orders": [
                   {"px": "0.40"}, {"px": "0.38"}, {"px": "0.60"}],
               "decisions": [
                   {"acted": "open", "inventory_before": _inv("0", "0", "0")},
                   {"acted": "extend",
                    "inventory_before": _inv("0", "5", "1.90", None, "0.38")},
                   {"acted": "complete",
                    "inventory_before": _inv("0", "9", "3.42", None, "0.38")}]}
    rows = R.states_for_market([("t1", session)])
    assert len(rows) == 2, "the flat opening decision is out of scope"
    assert rows[0]["acted"] == "extend"


def test_a_market_that_never_became_imbalanced_yields_no_states():
    session = {"slug": "m", "orders": [],
               "decisions": [{"acted": "hold",
                              "inventory_before": _inv("5", "5", "4.00")}]}
    assert R.states_for_market([("t1", session)]) == []


# ------------------------------------------------------- classification
def test_an_available_compliant_complete_classifies_as_complete():
    row = {"complete_available": {"within_pair_ceiling": True,
                                  "improves_floor": True,
                                  "complete_px": "0.30"},
           "extend_reparative": None, "submitted_px": "0.30"}
    assert R.classify(row) == "COMPLETE"


def test_a_ceiling_blocked_complete_is_not_a_complete_opportunity():
    row = {"complete_available": {"within_pair_ceiling": False,
                                  "improves_floor": True,
                                  "complete_px": "0.95"},
           "extend_reparative": False, "submitted_px": "0.95"}
    assert R.classify(row) == "WAIT_REQUOTE"


def test_nothing_priced_at_all_is_no_safe_action():
    assert R.classify({"complete_available": None,
                       "extend_reparative": None,
                       "submitted_px": None}) == "NO_SAFE_ACTION"


# ------------------------------------------------------------- end to end
def test_reconstruct_counts_a_taken_opportunity_as_taken(tmp_path):
    """The headline number is MISSED opportunities. A state where the
    complete was available and we completed is not missed -- that
    distinction is the entire result of the study."""
    session = {"kind": "session_end", "slug": "m",
               "inventory": _inv("9", "9", "6.00"),
               "orders": [{"px": "0.30", "outcome": "filled",
                           "filled_qty": "9"}],
               "decisions": [{"acted": "complete",
                              "inventory_before": _inv("0", "9", "3.42",
                                                       None, "0.38"),
                              "counterfactual_l5_price": {
                                  "l5_touch_px": "0.30"}}]}
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"deploy": [
        {"timestamp": "t1",
         "message": "PMUS_L5_CAMPAIGN " + json.dumps(session)}]}))
    res = R.reconstruct([str(p)], ["m"])
    m, = res["markets"]
    assert m["post_imbalance_states"] == 1
    assert m["class_counts"] == {"COMPLETE": 1}
    assert m["missed_complete_states"] == 0, "we completed; nothing missed"
