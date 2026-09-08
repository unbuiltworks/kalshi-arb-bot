"""The trace explains a fidelity failure. It must not become an argument.

The counterfactual mix in this module says what the acted distribution would
be if no cap had converted a draw -- and "restore those rows" means "do not
honour a cap". That is one short step from proposing to weaken a safety
control, which is forbidden. These tests pin that the module measures and
never recommends, and that its counterfactual is the SHELL's own recorded
field rather than anything this code inferred.
"""
from __future__ import annotations

import os

import pytest

from src import l5_relation_distortion_trace as tr

_SRC = open(tr.__file__).read()
_LOGS = "../r5f_logs"
_have = os.path.isdir(_LOGS)
_paths = ([os.path.join(_LOGS, f) for f in sorted(os.listdir(_LOGS))]
          if _have else [])


def _d(sampled, acted, **kw):
    d = {"acted": acted, "shadow": {"stochastic": {"sampled": sampled}}}
    d.update(kw)
    return d


# ------------------------------------------------------------- the matrix
def test_the_matrix_counts_sampled_rows_against_acted_rows():
    decs = [_d("extend", "complete"), _d("extend", "complete"),
            _d("extend", "extend"), _d("open", "open")]
    m = tr.matrix(decs)
    assert m["extend"]["sampled_n"] == 3
    assert m["extend"]["acted"] == {"extend": 1, "complete": 2}
    assert m["extend"]["fidelity"] == round(1 / 3, 4)
    assert m["open"]["fidelity"] == 1.0


def test_a_row_without_a_sampled_relation_is_excluded():
    decs = [_d(None, "complete"), _d("open", "open")]
    m = tr.matrix(decs)
    assert set(m) == {"open"}
    assert m["open"]["sampled_n"] == 1


# --------------------------------------------------------- the attribution
def test_attribution_uses_the_shells_own_counterfactual_field():
    decs = [_d("extend", "complete", cap_binding=["spend_cap"],
               would_act_without_safety_cap="extend"),
            _d("extend", "complete", cap_binding=["spend_cap"],
               would_act_without_safety_cap=None)]
    a = tr.attribute(decs, "extend", "complete")
    assert a["n"] == 2
    assert a["shell_attests_it_would_have_acted_as_sampled"] == 1
    assert a["unattributed"] == 1
    assert "null are counted UNATTRIBUTED rather than assumed" in a[
        "attestation_note"]


def test_a_null_cap_binding_is_not_read_as_no_cap():
    """Absent instrumentation and an absent cap are different facts."""
    decs = [_d("extend", "complete", cap_binding=None),
            _d("extend", "complete", cap_binding=[])]
    a = tr.attribute(decs, "extend", "complete")
    assert a["cap_binding"]["<not recorded>"] == 1
    assert a["cap_binding"]["<none bound>"] == 1


# ------------------------------------------------------- the counterfactual
def test_the_counterfactual_never_invents_a_restoration():
    """A row with no recorded counterfactual keeps its actual relation."""
    decs = [_d("extend", "complete", would_act_without_safety_cap="extend"),
            _d("extend", "complete", would_act_without_safety_cap=None)]
    cf = tr.counterfactual_mix(decs)
    assert cf["rows_restored"] == 1
    assert cf["counterfactual_incl_hold"] == {"extend": 0.5, "complete": 0.5}


def test_the_counterfactual_is_labelled_a_measurement_not_a_proposal():
    cf = tr.counterfactual_mix([_d("extend", "complete")])
    t = cf["this_is_a_measurement_not_a_proposal"]
    assert "Nothing here proposes that" in t
    assert "the caps may be worth it" in t.replace("\n", " ") or \
        "may be worth" in t


def test_l1_is_symmetric_and_zero_on_a_match():
    assert tr._l1({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}) == 0.0
    assert tr._l1({"a": 1.0}, {"b": 1.0}) == 2.0


# ------------------------------------------------- it must not recommend
def test_it_proposes_no_change_to_any_safety_control():
    assert "no_change_proposed" in _SRC
    blob = _SRC.lower()
    for banned in ("raise the cap", "loosen the cap", "widen the cap",
                   "remove the cap", "relax the cap", "increase the cap",
                   "should be raised", "recommend raising", "disable the"):
        assert banned not in blob, banned


def test_it_never_writes_to_the_live_path():
    assert "pmus_spring_l5_session" not in _SRC
    for forbidden in ("requests", "httpx", "place_order", "subprocess",
                      "os.system", "set-variables", "setenv"):
        assert forbidden not in _SRC, forbidden
    assert _SRC.count('"w") as f') == 2


# ------------------------------------------------------ against the tape
@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_extend_is_the_leaking_cell_on_the_real_tape():
    m = tr.report(_paths)["transition_matrix"]
    assert m["extend"]["fidelity"] < 0.60
    assert m["open"]["fidelity"] > 0.85
    assert m["complete"]["fidelity"] > 0.85
    # the leak goes to COMPLETE and HOLD, not to OPEN
    assert m["extend"]["acted"].get("open", 0) == 0


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_the_spend_cap_is_the_dominant_converter():
    a = tr.report(_paths)["attribution"]["extend->complete"]
    caps = a["cap_binding"]
    assert caps.get("spend_cap", 0) > caps.get("residual_cap", 0)
    assert a["shell_attests_it_would_have_acted_as_sampled"] > 0.8 * a["n"]


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_restoring_the_conversions_moves_l1_toward_l5_not_away():
    cf = tr.report(_paths)["counterfactual_mix"]
    assert cf["l1_counterfactual_vs_l5"] < cf["l1_actual_vs_l5"]
    assert cf["complete_share_counterfactual"] < cf["complete_share_actual"]


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_every_conversion_cell_is_attributed_or_declared_unattributed():
    for k, v in tr.report(_paths)["attribution"].items():
        assert v["shell_attests_it_would_have_acted_as_sampled"] + \
            v["unattributed"] == v["n"], k
