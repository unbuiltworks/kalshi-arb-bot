"""Gate attribution, ablations, and forward labels on blocked candidates."""
import ast

import pytest

from src.gemini_execution.econ import attribution as AT
from src.gemini_execution.econ import blocked_labeler as BL


def G(**kw):
    return [{"gate": k, "pass": v} for k, v in kw.items()]


# -------------------------------------------------------------- attribution

def test_a_gate_that_blocks_everything_can_still_be_a_passenger():
    """THE DISTINCTION THE RAW REFUSAL COUNTS CANNOT MAKE. A gate failing on
    100% of cycles changes nothing if another gate failed on all of them too."""
    a = AT.GateAttribution()
    for i in range(20):
        a.record(cohort="BTC5m", symbol="S", t=i, tte_s=200, leg="YES",
                 price="0.6", spread="0.25",
                 gates=G(SPREAD=False, FLOW_REVERSIBLE=False))
    r = a.report()
    assert r["per_gate"]["SPREAD"]["blocked_pct"] == 100.0
    assert r["per_gate"]["SPREAD"]["marginal_pct"] == 0.0
    assert r["per_gate"]["SPREAD"]["verdict"] == "PASSENGER"
    # removing it alone admits nothing
    assert r["ablations"]["no_spread_gate"]["delta_vs_current"] == 0


def test_a_sole_blocker_is_marginal_and_binding():
    a = AT.GateAttribution()
    for i in range(20):
        a.record(cohort="BTC5m", symbol="S", t=i, tte_s=200, leg="YES",
                 price="0.6", spread="0.25",
                 gates=G(SPREAD=False, FLOW_REVERSIBLE=True))
    r = a.report()
    assert r["per_gate"]["SPREAD"]["verdict"] == "BINDING"
    assert r["per_gate"]["SPREAD"]["marginal_pct"] == 100.0
    assert r["ablations"]["no_spread_gate"]["admitted_n"] == 20
    assert r["ablations"]["no_spread_gate"]["delta_vs_current"] == 20


def test_an_unknown_gate_blocks_unless_it_was_waived():
    a = AT.GateAttribution()
    a.record(cohort="BTC5m", symbol="S", t=1, tte_s=200, leg="YES",
             price="0.6", spread="0.05",
             gates=G(HAZARD=None, SPREAD=True))
    a.record(cohort="BTC5m", symbol="S", t=2, tte_s=200, leg="YES",
             price="0.6", spread="0.05",
             gates=G(HAZARD=None, SPREAD=True), waived=["HAZARD"])
    r = a.report()
    assert r["admitted_n"] == 1              # only the waived one


def test_blocker_combinations_are_counted():
    a = AT.GateAttribution()
    for _ in range(3):
        a.record(cohort="BTC5m", symbol="S", t=1, tte_s=200, leg="YES",
                 price="0.6", spread="0.2",
                 gates=G(SPREAD=False, FLOW_REVERSIBLE=False))
    for _ in range(2):
        a.record(cohort="BTC5m", symbol="S", t=1, tte_s=200, leg="YES",
                 price="0.6", spread="0.2", gates=G(SPREAD=False))
    combos = {tuple(c["gates"]): c["n"] for c in a.report()["blocker_combinations"]}
    assert combos[("FLOW_REVERSIBLE", "SPREAD")] == 3
    assert combos[("SPREAD",)] == 2


def test_segments_never_pool_yes_and_no():
    """Their toxicity is not assumed symmetric."""
    a = AT.GateAttribution()
    a.record(cohort="BTC5m", symbol="S", t=1, tte_s=90, leg="YES",
             price="0.6", spread="0.2", gates=G(SPREAD=False))
    a.record(cohort="BTC5m", symbol="S", t=1, tte_s=90, leg="NO",
             price="0.4", spread="0.2", gates=G(SPREAD=False))
    seg = a.report()["by_tte_and_leg"]
    assert "BTC5m|61-120|YES" in seg and "BTC5m|61-120|NO" in seg


def test_the_ablation_set_covers_the_named_experiments():
    for name in ("current_gates", "no_spread_gate", "flow_only",
                 "quote_quality_only", "flow_plus_economics"):
        assert name in AT.ABLATIONS


def test_ablations_are_arithmetic_over_recorded_vectors_not_a_rerun():
    """No policy is invoked and nothing is fitted -- the module must not reach
    entry.py at all."""
    tree = ast.parse(open(AT.__file__).read())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module)
    assert not any("econ.entry" in m for m in imported)


# ------------------------------------------------------------ forward labels

def _cand(L, **over):
    kw = dict(cohort="BTC5m", symbol="S", t=0.0, tte_s=200, leg="YES",
              price="0.60", spread="0.25", admitted=False,
              blocking=["SPREAD"], marginal_blocker="SPREAD")
    kw.update(over)
    return L.open_candidate(**kw)


def test_a_blocked_candidate_that_would_have_been_good_is_labelled_good():
    L = BL.BlockedLabeler()
    _cand(L)
    for t, yb, nb in ((10, "0.62", "0.36"), (40, "0.65", "0.33"),
                      (70, "0.66", "0.32"), (130, "0.64", "0.30")):
        L.advance(book={"yes": {"bid": yb}, "no": {"bid": nb}}, t=float(t),
                  symbol="S")
    L.settle_all(leg="YES", symbol="S")
    d = L.report(min_cell=1)["blocked"]
    assert d["markout_p50_8s"] > 0 and d["markout_p50_60s"] > 0
    assert d["opposite_available_share"] == 1.0
    assert d["best_pair_below_one_share"] == 1.0
    assert d["settlement_value_p50"] > 0


def test_a_blocked_candidate_that_would_have_been_toxic_is_labelled_toxic():
    L = BL.BlockedLabeler()
    _cand(L)
    for t, yb, nb in ((10, "0.55", "0.43"), (40, "0.48", "0.50"),
                      (70, "0.40", "0.58"), (130, "0.30", "0.68")):
        L.advance(book={"yes": {"bid": yb}, "no": {"bid": nb}}, t=float(t),
                  symbol="S")
    L.settle_all(leg="NO", symbol="S")
    d = L.report(min_cell=1)["blocked"]
    assert d["markout_p50_60s"] < 0
    assert d["adverse_share_60s"] == 1.0
    assert d["max_adverse_markout_p50_ticks"] < 0
    assert float(d["settlement_value_p50"]) < 0


def test_markout_is_from_the_perspective_of_the_leg_we_would_have_bought():
    """A NO candidate marks against the NO bid, never the YES bid."""
    L = BL.BlockedLabeler()
    _cand(L, leg="NO", price="0.40")
    # YES collapses, so NO rises: favourable to a NO buyer
    L.advance(book={"yes": {"bid": "0.20"}, "no": {"bid": "0.78"}}, t=40.0,
              symbol="S")
    L.settle_all(symbol="S")
    d = L.report(min_cell=1)["blocked"]
    assert d["markout_p50_30s"] > 0


def test_a_candidate_is_never_marked_against_another_contract():
    """Different instruments, different strikes: the arithmetic would look
    plausible and be meaningless."""
    L = BL.BlockedLabeler()
    _cand(L, symbol="A")
    assert L.advance(book={"yes": {"bid": "0.99"}}, t=40.0, symbol="B") == 0
    assert L.advance(book={"yes": {"bid": "0.62"}}, t=40.0, symbol="A") == 1


def test_open_candidates_are_closed_at_rollover():
    L = BL.BlockedLabeler()
    _cand(L, symbol="A")
    assert L.settle_all(symbol="A") == 1
    assert L.open_ == []
    assert L.report(min_cell=1)["labelled"] == 1


def test_labels_are_segmented_by_cohort_tte_and_leg():
    L = BL.BlockedLabeler()
    _cand(L, leg="YES", tte_s=200)
    _cand(L, leg="NO", price="0.40", tte_s=90)
    L.advance(book={"yes": {"bid": "0.61"}, "no": {"bid": "0.38"}}, t=40.0,
              symbol="S")
    L.settle_all(symbol="S")
    seg = L.report(min_cell=1)["by_cohort_tte_leg"]
    assert "BTC5m|121-300|YES" in seg and "BTC5m|61-120|NO" in seg


def test_labels_are_grouped_by_the_gate_that_marginally_blocked_them():
    """So 'what did SPREAD cost us' is answerable directly."""
    L = BL.BlockedLabeler()
    _cand(L, marginal_blocker="SPREAD")
    _cand(L, marginal_blocker=None, admitted=True, blocking=[])
    L.advance(book={"yes": {"bid": "0.62"}, "no": {"bid": "0.36"}}, t=40.0,
              symbol="S")
    L.settle_all(symbol="S")
    by = L.report(min_cell=1)["by_marginal_blocker"]
    assert "SPREAD" in by and "ADMITTED" in by


def test_an_empty_segment_is_not_a_segment_of_zeros():
    L = BL.BlockedLabeler()
    _cand(L)
    L.settle_all(symbol="S")
    r = L.report(min_cell=1)
    assert r["admitted"]["empty"] is True
    assert "opposite_available_share" not in r["admitted"]


def test_a_horizon_is_scored_once_and_never_revised():
    L = BL.BlockedLabeler()
    c = _cand(L)
    L.advance(book={"yes": {"bid": "0.65"}, "no": {"bid": "0.33"}}, t=10.0,
              symbol="S")
    first = dict(c.markouts)
    L.advance(book={"yes": {"bid": "0.20"}, "no": {"bid": "0.78"}}, t=11.0,
              symbol="S")
    assert c.markouts["8"] == first["8"]


# --------------------------------------------------- no look-ahead, pinned

def test_no_gate_ever_reads_a_research_label():
    """A label is computed from the tape AFTER the candidate moment. Feeding it
    back into a gate would be look-ahead of the worst kind."""
    from src.gemini_execution.econ import entry as EN
    from src.gemini_execution.econ import veto as VT
    for mod in (EN, VT):
        tree = ast.parse(open(mod.__file__).read())
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                imported.add(n.module)
            elif isinstance(n, ast.Import):
                for al in n.names:
                    imported.add(al.name)
        assert not any("blocked_labeler" in m or "attribution" in m
                       for m in imported), mod.__name__


def test_the_arm_writes_research_ledgers_but_never_reads_them():
    from src.gemini_execution.econ import arm as ARM
    src = open(ARM.__file__).read()
    # written to
    assert "blocked.open_candidate" in src
    assert "attribution.record" in src
    # never consulted for a decision
    for forbidden in ("blocked.report()[", "attribution.report()[",
                      "if self.state.blocked", "if self.state.attribution"):
        assert forbidden not in src
