"""OWNER 2026-09-17 -- the two-token divergence-classification correction.

THE DEFECT, observed live. `mark_noop(dec, reason, divergence)` stores what it
is given in `dec["divergence"]` and then classifies THAT itself:

    dv = divergence or f"safety_adaptation:{reason}"
    if str(dec.get("divergence") or "none").startswith("none"):
        dec["divergence"] = dv
        dec["divergence_class"] = divergence_class(dv)

Two of the eleven call sites handed it `divergence_class(key)` -- an already
classified, human-readable string -- so the classifier ran on its own output.
The result is not a key, contains no "->", and does not start with
"safety_adaptation:", so it fell through to the catch-all. On
aec-itfme-artkuk-egople-2026-09-17 at 11:59Z, both refused decisions recorded:

    divergence      : 'safety-cap override (average matched-pair cost ceiling)'
    divergence_class: 'implementation bug (unclassified divergence -- investigate)'

TWO CONSEQUENCES, and the second is the worse one:
  1. a correct, named safety override reads as "implementation bug" in the
     field a reviewer scans first -- the exact opposite of the truth;
  2. `divergence` itself is corrupted from a KEY into prose, so anything that
     buckets on it (the fidelity evaluator among them) sees a value outside the
     enumeration. That half is silent.

`combined_loss_stop` carried the identical defect and had simply never fired.

THIS IS CLASSIFICATION ONLY. The matched-pair ceiling gate is CORRECT and
intentionally covers EXTEND -- an EXTEND raises the average matched-pair cost
under inventory that is already matched, which is precisely why the rule is an
average rather than a marginal one. Both live refusals at 1.022 > 1.00 were
right. No gate, threshold, price, size, relation or mandate semantic changes.
"""
from __future__ import annotations

import ast
import inspect
import io

import pytest

from src import pmus_spring_l5_session as S

#: the two sites that were wrong, with the class each must now reach
AFFECTED = {
    "safety_adaptation:matched_pair_ceiling":
        "safety-cap override (average matched-pair cost ceiling)",
    "safety_adaptation:combined_loss_stop":
        "safety-cap override (combined settled + unsettled loss budget)",
}

#: verbatim from the live rows, and the thing that must never reappear
LIVE_DEFECT_CLASS = "implementation bug (unclassified divergence -- investigate)"


def _fresh_decision() -> dict:
    """A decision row as the record builder leaves it before any gate runs:
    acted set, no refusal, divergence still 'none'."""
    return {"acted": "extend", "refusal": None, "divergence": "none",
            "divergence_class": "no disagreement"}


# --------------------------------------------------------------------------
# the regressions the owner asked for, by name

@pytest.mark.parametrize("key,want_class", sorted(AFFECTED.items()))
def test_the_raw_key_is_retained_and_resolves_to_its_intended_class(
        key, want_class):
    """Through mark_noop, exactly as the live path calls it."""
    dec = _fresh_decision()
    S.mark_noop(dec, "matched_pair_ceiling:1.022>1.00", key)
    assert dec["divergence"] == key, "the KEY must survive, not a class string"
    assert dec["divergence_class"] == want_class
    assert dec["divergence_class"] != LIVE_DEFECT_CLASS


@pytest.mark.parametrize("key,want_class", sorted(AFFECTED.items()))
def test_the_class_map_already_contained_the_intended_class(key, want_class):
    """The class was never missing -- the call site just never reached it."""
    assert S.DIVERGENCE_CLASSES[key] == want_class
    assert S.divergence_class(key) == want_class


def test_the_matched_pair_ceiling_row_is_a_safety_override_not_a_bug(self=None):
    """The live row, rebuilt: refused EXTEND at 1.022 > 1.00."""
    dec = _fresh_decision()
    S.mark_noop(dec, "matched_pair_ceiling:1.022>1.00",
                "safety_adaptation:matched_pair_ceiling")
    assert dec["acted"] == "hold"
    assert dec["attempted"] == "extend"      # the drawn relation is preserved
    assert dec["refusal"] == "matched_pair_ceiling:1.022>1.00"
    assert dec["divergence_class"].startswith("safety-cap override")


def test_the_combined_loss_stop_row_classifies_too(self=None):
    dec = _fresh_decision()
    S.mark_noop(dec, "combined_loss_stop:-6.01<=-6.00",
                "safety_adaptation:combined_loss_stop")
    assert dec["divergence"] == "safety_adaptation:combined_loss_stop"
    assert dec["divergence_class"] == \
        "safety-cap override (combined settled + unsettled loss budget)"


# --------------------------------------------------------------------------
# the mutation: the OLD defect, reproduced, so the test cannot pass vacuously

@pytest.mark.parametrize("key", sorted(AFFECTED))
def test_the_old_double_classification_reproduces_the_live_defect(key):
    """Passing divergence_class(key) -- what the two sites used to do -- still
    produces exactly the wrong pair of values seen live. This is the mutation
    guard: if someone reintroduces the wrapper, the tests above go red and this
    one documents what they will see."""
    dec = _fresh_decision()
    S.mark_noop(dec, "irrelevant", S.divergence_class(key))
    assert dec["divergence"] == AFFECTED[key]          # prose, not a key
    assert dec["divergence"] not in S.DIVERGENCE_CLASSES
    assert dec["divergence_class"] == LIVE_DEFECT_CLASS


def test_classifying_a_class_is_what_falls_through(self=None):
    """The mechanism in one line, independent of mark_noop."""
    for key, cls in AFFECTED.items():
        assert S.divergence_class(key) == cls
        assert S.divergence_class(cls) == LIVE_DEFECT_CLASS


# --------------------------------------------------------------------------
# the structural guard -- the test that would have caught this in the first
# place, and the reason it is written over CALL SITES rather than over values

def _mark_noop_divergence_args():
    """Every mark_noop(...) call in the live session module, with its third
    argument's AST node. Parsed, not grepped: an argument spanning three lines
    is exactly how the matched_pair_ceiling one hid."""
    tree = ast.parse(io.open(inspect.getsourcefile(S), encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name != "mark_noop":
            continue
        arg = node.args[2] if len(node.args) > 2 else None
        for kw in node.keywords:
            if kw.arg == "divergence":
                arg = kw.value
        out.append((node.lineno, arg))
    return out


def test_no_mark_noop_call_site_is_double_classified(self=None):
    """THE FIX, asserted over every call site with no exceptions. Wrapping the
    argument in divergence_class() is the defect this change removes, and it
    must never come back anywhere."""
    sites = _mark_noop_divergence_args()
    assert len(sites) >= 9, f"expected the live call sites, found {len(sites)}"
    bad = []
    for lineno, arg in sites:
        if not isinstance(arg, ast.Call):
            continue
        fn = arg.func
        nm = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if nm == "divergence_class":
            bad.append(lineno)
    assert bad == [], f"mark_noop argument wrapped in divergence_class(): {bad}"


#: FOUND BY THE TEST BELOW, 2026-09-17, and deliberately NOT repaired here.
#: `venue_execution_adaptation:no_priceable_side` reaches the SAME wrong label
#: as the bug this file fixes -- "implementation bug" for what is plainly a
#: venue/execution adaptation -- but by a DIFFERENT mechanism: the key is
#: simply absent from DIVERGENCE_CLASSES, and only keys starting
#: "safety_adaptation:" have a generic fallback. It is not one of the two sites
#: the owner scoped, and it is not a double classification, so it is reported
#: rather than quietly swept in. Naming it here means a NEW offender still
#: fails, and repairing this one also fails until the list is updated.
KNOWN_UNMAPPED = {"venue_execution_adaptation:no_priceable_side"}


def test_every_mark_noop_key_resolves_to_a_real_class(self=None):
    """A key that does not resolve is mislabelled in the field a reviewer
    reads first. `safety_adaptation:entry_band_left` is fine unmapped -- it
    takes the generic "safety-cap override" fallback, which is true of it."""
    unresolved = set()
    for lineno, arg in _mark_noop_divergence_args():
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            continue
        if S.divergence_class(arg.value) == LIVE_DEFECT_CLASS:
            unresolved.add(arg.value)
    assert unresolved == KNOWN_UNMAPPED, (
        "call-site keys that classify as 'implementation bug' changed; "
        f"got {sorted(unresolved)}, expected {sorted(KNOWN_UNMAPPED)}")


def test_the_two_repaired_keys_are_not_in_the_known_unmapped_set(self=None):
    """The point of the repair: neither of the owner's two sites is on the
    exception list. If one reappears there, the fix regressed."""
    assert AFFECTED.keys() & KNOWN_UNMAPPED == set()


def test_no_call_site_passes_a_value_that_is_itself_a_class_string(self=None):
    """Belt and braces over the same call sites: a literal that happens to
    equal one of the human-readable classes is the same bug wearing a
    different hat."""
    classes = set(S.DIVERGENCE_CLASSES.values())
    for lineno, arg in _mark_noop_divergence_args():
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            assert arg.value not in classes, \
                f"line {lineno} passes a CLASS string, not a key"


# --------------------------------------------------------------------------
# what this change is NOT

def test_the_pair_ceiling_gate_itself_is_untouched(self=None):
    """The gate is correct and intentionally covers EXTEND. The refusal
    predicate, its threshold source and its stop_reason format are all
    unchanged by a labelling fix -- if any of these move, this is no longer
    classification-only."""
    src = io.open(inspect.getsourcefile(S), encoding="utf-8").read()
    assert "if not pair_invariant_ok(inv, side_up, px, qty):" in src
    assert 'f"matched_pair_ceiling:{_after}>"' in src
    assert "matched_pair_cost_after(inv, side_up, px, qty)" in src


def test_mark_noop_still_records_a_refusal_as_hold_with_its_reason(self=None):
    """The behaviour every safety refusal depends on, unchanged: a decision
    that submits nothing is HOLD with a named reason, and the relation it
    attempted is preserved rather than rewritten."""
    dec = _fresh_decision()
    dec["acted"] = "complete"
    S.mark_noop(dec, "matched_pair_ceiling:1.022>1.00",
                "safety_adaptation:matched_pair_ceiling")
    assert dec["acted"] == "hold"
    assert dec["attempted"] == "complete"


def test_an_already_diverged_row_is_not_overwritten(self=None):
    """mark_noop only fills in a divergence that is still 'none'. A row that
    already carries a real divergence keeps it -- the fix must not change that
    precedence."""
    dec = _fresh_decision()
    dec["divergence"] = "behavioral:modal_argmax_vs_stochastic_draw"
    dec["divergence_class"] = S.divergence_class(dec["divergence"])
    S.mark_noop(dec, "matched_pair_ceiling:1.022>1.00",
                "safety_adaptation:matched_pair_ceiling")
    assert dec["divergence"] == "behavioral:modal_argmax_vs_stochastic_draw"
