"""Spec population: only what the report establishes, and nothing to be tidy."""
import io
import json

import pytest

from analysis.l5.window_forensics import spec as SP
from src.l5_surface import params as PR


def _rep(**over):
    """A report that meets every rule, so tests can break one at a time."""
    parents = []
    for i in range(40):
        parents.append({"qty": 100.0 if i < 30 else 25.0,
                        "marks": {"-1": {"readable": True, "present": True,
                                         "ticks_from_touch": 0 if i % 2 else 1}}})
    w = {"parents": {"n": 40},
         "qty": {"classes": {"EXACT_100": 30}, "exact_100_share": 0.75},
         "qty_vs_inventory": {"corr_abs_residual_vs_qty": 0.02},
         "qty_vs_tte": {"by_bucket": {"tte>600": {"n": 10},
                                      "tte300-600": {"n": 10},
                                      "tte120-300": {"n": 10},
                                      "tte<=120": {"n": 10}}},
         "replenishment": {"at_+5s": {"readable": 40, "present": 34}},
         "scored_parents": parents}
    rep = {"integrity": {"sample_complete": True, "eligible_n": 3,
                         "n_required": 3},
           "mechanism": {"verdict": "H3-A PERSISTENT_TWO_SIDED_SURFACE"},
           "pooled": {"both_sides": {"both_outcomes_share": 0.85,
                                     "readable_snapshots": 900},
                      "reversals": {"resolved": 20,
                                    "share_already_present": 0.85}},
           "windows": [w]}
    for k, v in over.items():
        rep[k] = v
    return rep


def test_an_incomplete_sample_populates_nothing():
    r = SP.build({"integrity": {"sample_complete": False, "eligible_n": 1,
                                "n_required": 3}})
    assert r["built"] is False and "incomplete" in r["why"]


def test_a_supported_report_populates_only_supported_fields():
    r = SP.build(_rep())
    sp = r["spec"]
    got = {f: sp.params[f].status for f in PR.FIELDS}
    assert got["base_parent_qty"] == PR.OBSERVED_PROSPECTIVE
    assert sp.get("base_parent_qty") == "100"
    assert got["both_sides_policy"] == PR.OBSERVED_PROSPECTIVE
    assert got["refresh_policy"] == PR.OBSERVED_PROSPECTIVE
    assert sp.params["refresh_policy"].value == {"mode": "STANDING"}
    assert got["replenishment_policy"] == PR.OBSERVED_PROSPECTIVE
    assert got["inventory_size_skew"] == PR.OBSERVED_PROSPECTIVE
    assert got["TTE_policy"] == PR.OBSERVED_PROSPECTIVE
    # and the ones nothing can support stay open
    assert got["withdrawal_policy"] == PR.UNRESOLVED
    assert got["inventory_price_skew"] == PR.UNRESOLVED
    assert got["max_residual"] == PR.UNRESOLVED       # FIDELITY
    assert got["minimum_rest_time"] == PR.UNRESOLVED


def test_every_populated_field_cites_the_statistic_that_established_it():
    sp = SP.build(_rep())["spec"]
    for f in PR.FIELDS:
        p = sp.params[f]
        if p.status == PR.OBSERVED_PROSPECTIVE:
            assert p.evidence and any(ch.isdigit() for ch in p.evidence), f


def test_a_mode_below_the_threshold_does_not_become_a_rule():
    rep = _rep()
    rep["windows"][0]["qty"]["classes"]["EXACT_100"] = 12    # 0.30
    r = SP.build(rep)
    assert r["spec"].params["base_parent_qty"].status == PR.UNRESOLVED
    assert "0.5" in r["unresolved_because"]["base_parent_qty"]


def test_a_thin_sample_is_not_asked_the_question_at_all():
    rep = _rep()
    rep["windows"][0]["parents"]["n"] = 9
    r = SP.build(rep)
    assert r["spec"].params["base_parent_qty"].status == PR.UNRESOLVED
    assert "n >= 30" in r["unresolved_because"]["base_parent_qty"]


def test_an_ambiguous_share_resolves_nothing():
    rep = _rep()
    rep["pooled"]["both_sides"]["both_outcomes_share"] = 0.50
    rep["pooled"]["reversals"]["share_already_present"] = 0.50
    r = SP.build(rep)
    assert r["spec"].params["both_sides_policy"].status == PR.UNRESOLVED
    assert r["spec"].params["refresh_policy"].status == PR.UNRESOLVED


def test_a_low_share_establishes_the_negative_not_nothing():
    """0.10 is as informative as 0.90. The rule says so in both directions."""
    rep = _rep()
    rep["pooled"]["reversals"]["share_already_present"] = 0.10
    rep["windows"][0]["replenishment"]["at_+5s"]["present"] = 2
    r = SP.build(rep)
    assert r["spec"].params["refresh_policy"].value == {
        "mode": "REFRESHED_AFTER_FILL"}
    assert r["spec"].params["replenishment_policy"].value == {
        "mode": "NOT_REPLACED"}


def test_non_contiguous_modal_offsets_are_not_a_ladder():
    rep = _rep()
    for i, p in enumerate(rep["windows"][0]["scored_parents"]):
        p["marks"]["-1"]["ticks_from_touch"] = 0 if i % 2 else 7
    r = SP.build(rep)
    assert r["spec"].params["tick_offsets"].status == PR.UNRESOLVED
    assert "not contiguous" in r["unresolved_because"].get(
        "tick_offsets", r["spec"].params["tick_offsets"].prior_evidence)


def test_contiguous_modal_offsets_set_both_offsets_and_rung_count():
    sp = SP.build(_rep())["spec"]
    assert sp.get("tick_offsets") == [0, 1]
    assert sp.get("rung_count") == 2


def test_withdrawal_policy_is_marked_permanently_unobservable():
    sp = SP.build(_rep())["spec"]
    p = sp.params["withdrawal_policy"]
    assert p.status == PR.UNRESOLVED
    assert "PERMANENTLY UNOBSERVABLE" in p.prior_evidence
    assert "however large" in p.prior_evidence


def test_the_adapted_build_carries_our_risk_fields_as_ours():
    sp = SP.build(_rep(), kind="ADAPTED")["spec"]
    assert sp.params["max_residual"].status == PR.OURS_ADAPTED
    assert sp.params["minimum_rest_time"].status == PR.OURS_ADAPTED
    assert sp.kind == "ADAPTED"


def test_the_rules_are_declared_above_the_code_that_applies_them():
    src = io.open("analysis/l5/window_forensics/spec.py").read()
    assert src.index("RULES: Dict[str, Dict[str, Any]] = {") < src.index(
        "def build(")


def test_a_populated_spec_can_actually_drive_the_engine():
    """The point of populating it. A spec that still blocks the engine has
    established nothing usable, and the test says which fields remain."""
    from src.l5_surface import engine as E
    sp = SP.build(_rep(), kind="ADAPTED")["spec"]
    st = {"cohort": "BTC15m", "market_key": "m", "state_id": "s",
          "t_utc": 1.0, "tte_s": 600.0,
          "outcomes": {"Up": {"bid": 0.50, "ask": 0.51, "tick": 0.01},
                       "Down": {"bid": 0.49, "ask": 0.50, "tick": 0.01}},
          "inventory": {}}
    r = E.desired_surface(st, sp)
    assert r["emitted"] is True, r.get("blocked_by")
    s = r["surface"]
    assert [x.max_acceptable_price for x in s.rungs
            if x.outcome == "Up"] == ["0.51", "0.50"]
    assert [x.desired_qty for x in s.rungs if x.outcome == "Up"] == ["100",
                                                                     "25.0"]


def test_the_fidelity_build_stays_unrunnable_and_says_why():
    """The honest verdict for a reconstruction of L5: two fields no study of
    public data can reach, so FIDELITY cannot be deployed as such."""
    from src.l5_surface import engine as E
    sp = SP.build(_rep(), kind="FIDELITY")["spec"]
    r = E.desired_surface({"cohort": "c", "market_key": "m", "state_id": "s",
                           "t_utc": 1.0, "tte_s": 600.0, "outcomes": {},
                           "inventory": {}}, sp)
    assert r["emitted"] is False
    assert "inventory_price_skew" in r["blocked_by"]
    assert "withdrawal_policy" in r["blocked_by"]
