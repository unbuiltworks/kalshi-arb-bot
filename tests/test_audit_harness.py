"""The audit harness is what stands between a plausible number and a wrong one,
so its own guarantees are tested rather than assumed."""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUD = os.path.join(ROOT, "analysis", "gemini", "audit")
sys.path.insert(0, AUD)
sys.path.insert(0, os.path.join(ROOT, "analysis", "gemini"))

import invariants as INV                                       # noqa: E402
import verify as V                                             # noqa: E402
from metric import Metric, ContractViolation                   # noqa: E402


def _mk(**kw):
    base = dict(name="m", numerator=1, denominator=2, unit_numerator="fill",
                unit_denominator="window", source="s", period="p",
                lane="BTC5m", evidence="OBSERVED", decoder="NONE")
    base.update(kw)
    return Metric(**base)


# -- the contract refuses what it must ------------------------------------

def test_unit_vocabulary_is_closed():
    """'request' and 'action' are the words that let a rung-cycle be called an
    order. They are not units."""
    for bad in ("request", "action", "rung", "opportunity"):
        try:
            _mk(unit_numerator=bad)
        except ContractViolation:
            continue
        raise AssertionError("%r was accepted as a unit" % bad)


def test_a_ratio_must_name_its_denominator_unit():
    try:
        _mk(unit_denominator=None)
    except ContractViolation:
        return
    raise AssertionError("a ratio without a denominator unit was accepted")


def test_source_period_and_lane_are_required():
    for f in ("source", "period", "lane"):
        try:
            _mk(**{f: ""})
        except ContractViolation:
            continue
        raise AssertionError("%s was allowed to be empty" % f)


def test_side_and_fill_dependent_are_provisional_by_construction():
    assert _mk(decoder="SIDE_DEPENDENT").provisional
    assert _mk(decoder="FILL_DEPENDENT").provisional
    assert not _mk(decoder="NONE").provisional


def test_unverified_is_not_agreement():
    assert _mk().agreement == "NOT_VERIFIED"
    assert _mk(independent="0.5").agreement == "AGREE"
    assert _mk(independent="0.6").agreement == "DISAGREEMENT"


# -- the verifier is actually independent ---------------------------------

FORBIDDEN_IMPORTS = {"report", "run_report", "metric", "invariants",
                     "p10h_gate", "p10b_decode", "p10e_audit_decode",
                     "p11a_blocked_census", "p10i_subpop"}


def test_verify_imports_nothing_from_the_primary_implementation():
    src = open(os.path.join(AUD, "verify.py")).read()
    tree = ast.parse(src)
    got = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            got |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            got.add(node.module.split(".")[0])
    bad = got & FORBIDDEN_IMPORTS
    assert not bad, "verify.py imports the code it is meant to check: %s" % bad


def test_the_two_bound_algorithms_agree():
    """Binomial-sum bisection against a Beta-quantile continued fraction."""
    import p10h_gate as G
    for k, n in ((0, 150), (1, 236), (2, 313), (3, 134), (5, 400), (0, 10)):
        a, b = G.binom_upper(k, n), V.clopper_pearson_upper(k, n)
        assert abs(a - b) < 1e-9, (k, n, a, b)


def test_closed_form_anchors_both():
    """k=0 has an exact closed form neither implementation uses."""
    import p10h_gate as G
    for n in (10, 149, 1000):
        want = 1 - 0.05 ** (1.0 / n)
        assert abs(G.binom_upper(0, n) - want) < 1e-9
        assert abs(V.clopper_pearson_upper(0, n) - want) < 1e-9


# -- the invariants fail closed on the errors we actually made ------------

def test_fills_per_window_rejects_a_per_cycle_denominator():
    """The exact defect that produced '0.80%'."""
    census = {"cols": {"5M": {"filOPEN": 33, "filEXTEND": 0, "filCOMPLETE": 0}},
              "windows": {"5M": 113}}
    good = _mk(name="fills_per_window[BTC5m]", numerator=33, denominator=113,
               evidence="RECONSTRUCTED", decoder="FILL_DEPENDENT")
    assert INV.fills_per_window_from_actuals(census, [good])[1] == INV.PASS
    bad = _mk(name="fills_per_window[BTC5m]", numerator=33, denominator=4396,
              evidence="RECONSTRUCTED", decoder="FILL_DEPENDENT")
    assert INV.fills_per_window_from_actuals(census, [bad])[1] == INV.FAIL


def test_a_constraint_may_not_be_called_non_binding_while_unreachable():
    """The exact defect behind 'CAP4 never binds'."""
    unreachable = [{"name": "CAP4", "blocked": 0, "reachable": False,
                    "asserts_non_binding": True, "reach_evidence": "x"}]
    assert INV.constraint_had_opportunity(unreachable)[1] == INV.FAIL
    honest = [{"name": "CAP4", "blocked": 0, "reachable": False,
               "asserts_non_binding": False, "reach_evidence": "UNTESTED"}]
    assert INV.constraint_had_opportunity(honest)[1] == INV.PASS


def test_a_comparison_without_an_exposure_note_fails():
    """The exact defect behind the BTC15m 6.2x."""
    assert INV.comparisons_normalize_exposure(
        [{"name": "H1"}])[1] == INV.FAIL
    assert INV.comparisons_normalize_exposure(
        [{"name": "H1", "exposure_note": "not matched: 17-37 vs 67-149"}]
    )[1] == INV.PASS


def test_disagreement_fails_the_report():
    dis = _mk(independent="0.9")
    assert INV.independent_agreement([dis])[1] == INV.FAIL


def test_unverified_also_fails_the_report():
    assert INV.independent_agreement([_mk()])[1] == INV.FAIL


def test_pnl_identity_is_na_not_pass_when_absent():
    """An absent P&L must be recorded N/A, never counted as a passing check."""
    assert INV.pnl_cashflow_identity(None)[1] == INV.NA
    assert INV.matched_residual_reconcile(None)[1] == INV.NA


def test_pnl_identity_catches_a_broken_cashflow():
    ok = {"matched": 1, "residual": -2, "fees": "-0.5", "rebate": "0.25",
          "total": "-1.25"}
    assert INV.pnl_cashflow_identity(ok)[1] == INV.PASS
    broken = dict(ok, total="0")
    assert INV.pnl_cashflow_identity(broken)[1] == INV.FAIL


def test_population_reconciles_catches_a_dropped_class():
    pop = {"H1": 10, "H2": 2, "_ambiguous": 5, "STALE": 3, "PRE_CAPTURE": 1,
           "NO_BOOK": 0, "_observed": 20, "_unique_keys": 21}
    assert INV.decoder_population_reconciles(pop)[1] == INV.PASS
    assert INV.decoder_population_reconciles(
        dict(pop, _observed=19))[1] == INV.FAIL
