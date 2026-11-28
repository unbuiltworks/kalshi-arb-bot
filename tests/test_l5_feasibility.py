"""OWNER 5559167134 #2/#3: frozen-gate feasibility decomposition under the
current safety shell and cap-fallback attribution, read-only, gate and
caps untouched."""
from __future__ import annotations

from src import l5_fidelity_gate as gate


def _dec(sampled, acted, p_ext=0.67, p_comp=0.33, residual=3.0, divergence="none",
         refusal=None, would=None):
    d = {"relation_ranked": [{"relation": "extend", "p": p_ext},
                             {"relation": "complete", "p": p_comp},
                             {"relation": "open", "p": round(1 - p_ext - p_comp, 4)}],
         "shadow": {"stochastic": {"sampled": sampled}}, "acted": acted,
         "divergence": divergence, "refusal": refusal,
         "lookup": {"residual_units": residual}}
    if would:
        d["would_act_without_safety_cap"] = would
    return d


def test_cap_forced_conversions_make_the_acted_threshold_unreachable():
    spec = gate.load_spec()
    # 10 post-fill decisions at the residual cap: L5 says extend .67 /
    # complete .33; the draw follows it (7 extend, 3 complete); every
    # extend at the cap is converted to COMPLETE by the shell
    decs = [_dec("extend", "complete", divergence="policy_flag:cap_fallback_complete",
                 refusal="residual_cap:fallback_complete:p=0.33", would="extend")
            for _ in range(7)]
    decs += [_dec("complete", "complete") for _ in range(3)]
    out = gate.feasibility_decomposition(decs, spec)
    assert out["decisions"] == 10 and out["post_fill_decisions"] == 10
    lay = out["layers"]
    assert lay["sampled"]["relation_freq"] == {"complete": 0.3, "extend": 0.7, "open": 0.0}
    assert lay["sampled"]["l1_vs_l5"] < 0.1
    assert lay["canonical_before_shell"] == lay["sampled"]
    assert lay["acted"]["relation_freq"]["complete"] == 1.0
    # the forced conversions cannot be undone under the current rules
    assert lay["best_achievable_under_current_shell"] == lay["acted"]
    assert out["status"] == "GATE_FEASIBILITY_CONFLICT"
    assert out["binding_rules"] == ["residual_cap"]
    assert out["forced_by_rule"] == {"residual_cap": 7}
    attr = out["cap_fallback_attribution"]
    assert attr == {"fallback_completes": 7, "at_residual_cap": 7,
                    "anomalies_below_cap": 0, "verdict": "ALL_CAP_FORCED"}


def test_a_fallback_below_the_cap_is_an_implementation_defect_candidate():
    spec = gate.load_spec()
    decs = [_dec("extend", "complete", residual=2.0,
                 divergence="policy_flag:cap_fallback_complete",
                 refusal="residual_cap:fallback_complete:p=0.33")]
    out = gate.feasibility_decomposition(decs, spec)
    assert out["cap_fallback_attribution"]["anomalies_below_cap"] == 1
    assert out["cap_fallback_attribution"]["verdict"] == "IMPLEMENTATION_DEFECT_CANDIDATE"
    assert "ANOMALY:residual_cap_fallback_below_cap" in out["forced_by_rule"]
    assert out["binding_rules"] == []


def test_feasible_when_no_rule_binds_and_spend_cap_is_named_when_it_does():
    spec = gate.load_spec()
    decs = [_dec("extend", "extend", residual=1.0) for _ in range(7)]
    decs += [_dec("complete", "complete", residual=1.0) for _ in range(3)]
    out = gate.feasibility_decomposition(decs, spec)
    assert out["status"] == "FEASIBLE_UNDER_CURRENT_SHELL" and out["binding_rules"] == []
    decs.append(_dec("extend", "complete", residual=1.0,
                     divergence="safety_adaptation:spend_cap->complete", would="extend"))
    out2 = gate.feasibility_decomposition(decs, spec)
    assert out2["forced_by_rule"] == {"spend_cap": 1}
    assert out2["binding_rules"] == ["spend_cap"]
    hold = _dec("complete", "hold", residual=0.0, refusal="complete_without_residual",
                divergence="venue_execution_adaptation:complete_below_unit_granularity")
    out3 = gate.feasibility_decomposition(decs + [hold], spec)
    assert out3["forced_by_rule"]["hold:complete_without_residual"] == 1


def test_evaluate_carries_the_feasibility_block():
    from tests.test_l5_evidence_integrity import _report
    out = gate.evaluate([_report("s1", "m1", 6)])
    assert out["feasibility"]["status"] in ("FEASIBLE_UNDER_CURRENT_SHELL",
                                            "GATE_FEASIBILITY_CONFLICT")
    assert out["feasibility"]["decisions"] == 6
    assert out["evidence_progress"]["minimums"]["live_scored_decisions"]["need"] == 200


def test_unclassified_ledger_row_is_classed_from_its_own_divergence_string():
    """LIVE 14:14Z: one clean row carried the superseded writer's label
    implementation_bug_candidate:complete_without_residual and the class
    'unclassified'. The gate names the row and assigns the class from the
    persisted divergence string -- never a guess, never a row rewrite."""
    import copy
    from tests.test_l5_evidence_integrity import _report
    rep = _report("s1", "m1", 3)
    rep["decisions"][1]["divergence"] = "implementation_bug_candidate:complete_without_residual"
    rep["decisions"][1]["divergence_class"] = "implementation bug (unclassified divergence -- investigate)"
    rep["decisions"][1]["refusal"] = "complete_without_residual"
    rep["decisions"][2]["divergence"] = "something:new"
    rep["decisions"][2]["divergence_class"] = "implementation bug (unclassified divergence -- investigate)"
    out = gate.evaluate([copy.deepcopy(rep)])
    cls = out["divergence_classes"]
    assert cls["venue/execution adaptation (shares-scale imbalance below one venue unit)"] == 1
    assert cls["implementation bug (unclassified divergence -- investigate)"] == 1
    assert out["divergence_reclassified_from_ledger"] == 1
    # the synthetic row 0 has no divergence at all (also "unclassified");
    # the reclassified row is no longer listed, the unknown one is
    samples = out["divergence_unclassified_samples"]
    assert [s["divergence"] for s in samples] == [None, "something:new"]


def test_gate_splits_clean_rows_by_safety_shell_version():
    """OWNER 5560559108 #5: evidence is labelled by the safety shell it was
    produced under; pre-label rows fall into the R1 concurrency-1 bucket."""
    import copy
    from tests.test_l5_evidence_integrity import _report
    rep = _report("s1", "m1", 4)
    rep["decisions"][0]["safety_shell_version"] = "R1/cap0.05/residual3/conc1/maker-only/fee-safe0.98/rest30s"
    rep["decisions"][1]["safety_shell_version"] = "R1/cap0.05/residual3/conc1/maker-only/fee-safe0.98/rest30s"
    out = gate.evaluate([copy.deepcopy(rep)])
    assert out["safety_shell_versions"] == {
        "R1/cap0.05/residual3/conc1/maker-only/fee-safe0.98/rest30s": 2,
        "unlabelled(R1 concurrency-1 era)": 2}
