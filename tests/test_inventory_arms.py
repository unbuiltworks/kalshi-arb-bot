"""PROSPECTIVE arms: identical inputs, one degree of freedom, and no fills.

Three things would silently invalidate the prospective comparison, so each is
asserted directly rather than inferred from a summary:

  * arms receiving different inputs. Then the comparison measures the input,
    not the policy -- and with a stochastic policy the most likely difference
    is the seed.
  * the two axes getting entangled. If placement moves with inventory policy,
    a result cannot be attributed to either.
  * a classification becoming a fill. CROSS/RESTABLE/EXECUTABLE/WAIT exist
    precisely so that no code path turns a quote into a position.
"""
from __future__ import annotations

import inspect
from decimal import Decimal as D

import pytest

from analysis.l5.adapted import prospective as PR
from src.l5_shadow import inventory_arms as A


def _states(arms=None):
    return {a: A.new_state(a) for a in A.arms(arms)}


def _obs(**kw):
    d = {"slug": "btc-updown-15m-1", "seq": 1, "t": 100, "p_up": 0.5,
         "abs_dp_60s": 0.01, "tte_s": 400}
    d.update(kw)
    return d


def _prop(side="Up", qty="5"):
    return {"side": side, "px": "0.50", "wire_px": "0.50", "qty": qty}


# --------------------------------------------------------------------------
# identical inputs

def test_every_arm_receives_the_same_draw_and_the_same_proposal():
    rows = A.decide(_obs(), _states(), sampled_relation="extend",
                    proposal=_prop(), classification="RESTABLE")
    assert len({r["sampled_relation"] for r in rows}) == 1
    assert len({tuple(sorted(r["proposal"].items())) for r in rows}) == 1
    assert len({r["classification"] for r in rows}) == 1
    assert {r["inventory_policy"] for r in rows} == set(A.DEFAULT_ARMS)


def test_an_arm_cannot_change_what_is_proposed_only_whether_to_allow_it():
    """`decide` takes the proposal as an argument and returns it unchanged;
    there is no hook by which an arm could rewrite side, price or size."""
    src = inspect.getsource(A.decide)
    assert '"proposal": dict(proposal)' in src
    sig = inspect.signature(A.decide)
    assert {"sampled_relation", "proposal", "classification"} <= set(sig.parameters)


def test_the_control_arm_allows_everything():
    st = _states()
    for side, qty in (("Up", "500"), ("Down", "900"), ("Up", "1")):
        rows = A.decide(_obs(), st, sampled_relation="extend",
                        proposal=_prop(side, qty), classification="WAIT")
        ctrl = [r for r in rows if r["inventory_policy"] == "FIDELITY"][0]
        assert ctrl["allowed"] is True


# --------------------------------------------------------------------------
# one degree of freedom, and it bites

def test_the_adapted_arms_actually_refuse_where_fidelity_does_not():
    st = _states()
    A.apply_evidenced_fill(st["V2A_cap100"], side="Up", qty=D("99"),
                           evidence=PR.STRICT)
    A.apply_evidenced_fill(st["V2E_near10_sub50"], side="Up", qty=D("2"),
                           evidence=PR.STRICT)
    rows = A.decide(_obs(), st, sampled_relation="extend",
                    proposal=_prop("Up", "50"), classification="RESTABLE")
    got = {r["inventory_policy"]: r["allowed"] for r in rows}
    assert got["FIDELITY"] is True
    assert got["V2A_cap100"] is False        # proposed 149 > cap 100
    assert got["V2E_near10_sub50"] is False  # near flat, would leave 52 > 50
    d = A.divergence(rows)
    assert d["any_disagreement"] is True
    assert d["diverged_from_fidelity"] == ["V2A_cap100", "V2E_near10_sub50"]


def test_a_refusal_records_why_rather_than_just_that():
    st = _states()
    A.apply_evidenced_fill(st["V2A_cap100"], side="Up", qty=D("99"),
                           evidence=PR.STRICT)
    rows = A.decide(_obs(), st, sampled_relation="extend",
                    proposal=_prop("Up", "50"), classification="RESTABLE")
    r = [x for x in rows if x["inventory_policy"] == "V2A_cap100"][0]
    assert "V2A_cap100 suppressed EXTEND" in r["gate_reason"]
    assert r["shadow_residual_before"] == "99"


def test_the_gates_are_the_frozen_ones_not_reimplemented_here():
    from analysis.l5.exploratory_v2 import interventions as IV
    src = inspect.getsource(A)
    assert "IV.v2e_matched_preservation(10.0, 50.0)" in src
    assert "IV.v2a_residual_cap(100.0)" in src
    assert IV.__name__.endswith("exploratory_v2.interventions")


# --------------------------------------------------------------------------
# the two axes stay separable

def test_placement_is_held_constant_across_arms():
    rows = A.decide(_obs(), _states(), sampled_relation="complete",
                    proposal=_prop(), classification="EXECUTABLE")
    assert A.divergence(rows)["placement_held_constant"] is True
    assert {r["placement_policy"] for r in rows} == {A.PLACEMENT_PINNED}


def test_every_row_names_both_axes_so_a_result_is_attributable():
    rows = A.decide(_obs(), _states(), sampled_relation="extend",
                    proposal=_prop(), classification="CROSS")
    for r in rows:
        assert r["inventory_policy"] and r["placement_policy"]


def test_placement_cannot_be_combined_before_the_pmus_tape_closes():
    with pytest.raises(RuntimeError) as e:
        A.combine_with_placement("nearest_maker", pmus_tape_closed=False)
    assert "pinned" in str(e.value)
    assert A.combine_with_placement("nearest_maker",
                                    pmus_tape_closed=True) == "nearest_maker"


# --------------------------------------------------------------------------
# nothing here is a fill

def test_no_classification_ever_moves_shadow_inventory():
    st = _states()
    for c in A.CLASSIFICATIONS:
        A.decide(_obs(), st, sampled_relation="extend",
                 proposal=_prop("Up", "50"), classification=c)
    for arm, s in st.items():
        assert s["up"] == D("0") and s["dn"] == D("0"), arm
        assert s["evidenced_fills"] == 0


def test_the_vocabulary_contains_no_filled():
    assert "FILLED" not in A.CLASSIFICATIONS
    with pytest.raises(ValueError):
        A.decide(_obs(), _states(), sampled_relation="extend",
                 proposal=_prop(), classification="FILLED")


def test_a_non_strict_fill_is_refused_rather_than_downgraded():
    st = A.new_state("FIDELITY")
    for weak in ("TOUCHED", "BOOK_REACHED", "TRADE_FLOW", "", None):
        with pytest.raises(ValueError):
            A.apply_evidenced_fill(st, side="Up", qty=D("5"), evidence=weak)
    assert st["up"] == D("0")
    A.apply_evidenced_fill(st, side="Up", qty=D("5"), evidence=PR.STRICT)
    assert st["up"] == D("5")


def test_the_module_has_no_money_moving_capability():
    """Checked on the AST, not on the text: the docstring says the words
    'no submit path', and a substring scan would have matched its own prose
    instead of the code."""
    import ast
    tree = ast.parse(inspect.getsource(A))
    imported, called = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
        elif isinstance(n, ast.Attribute):
            called.add(n.attr)
        elif isinstance(n, ast.Name):
            called.add(n.id)
    assert not (imported & {"requests", "httpx", "urllib", "aiohttp", "socket",
                            "http"}), imported
    assert not (called & {"submit", "submit_order", "place_order", "post",
                          "cancel_order", "create_order"}), called


def test_shadow_state_says_it_is_shadow_wherever_it_is_read():
    st = A.new_state("FIDELITY")
    assert "SHADOW_INVENTORY" in st["basis"]
    rows = A.decide(_obs(), _states(), sampled_relation="wait",
                    proposal=_prop(), classification="WAIT")
    assert all("NEVER a fill" in r["labels"] for r in rows)


# --------------------------------------------------------------------------
# the rejected family is preserved, not silently deleted and not run

def test_the_run_cutoff_family_is_preserved_but_not_in_the_default_comparison():
    assert set(A.SUPERSEDED_AS_CONTROL) == {"cap5", "cap8", "cap13"}
    assert not set(A.SUPERSEDED_AS_CONTROL) & set(A.DEFAULT_ARMS)
    from src.l5_shadow import adapted_policy as AP
    assert AP.CAPS == (5, 8, 13)        # the frozen module is untouched


def test_an_unknown_arm_is_refused_rather_than_silently_dropped():
    with pytest.raises(KeyError):
        A.arms(["V2E_near10_sub50", "not_an_arm"])
