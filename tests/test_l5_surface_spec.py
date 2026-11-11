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
            if x.outcome == "Up"] == ["0.50", "0.49"]
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


def test_the_two_frozen_sets_carry_the_owner_s_names():
    assert SP.FIDELITY_LABEL == "FIDELITY_OBSERVABLE_V1"
    assert SP.ADAPTED_LABEL == "L5_ADAPTED_V1"
    assert SP.build(_rep(), kind="FIDELITY")["spec"].label == SP.FIDELITY_LABEL
    assert SP.build(_rep(), kind="ADAPTED")["spec"].label == SP.ADAPTED_LABEL


def test_adapted_is_the_observable_set_plus_our_policies_and_nothing_else():
    """L5_ADAPTED_V1 must differ from FIDELITY_OBSERVABLE_V1 ONLY in the
    fields we supply. Any other difference would mean the adapted set quietly
    re-read the evidence."""
    f = SP.build(_rep(), kind="FIDELITY")["spec"]
    a = SP.build(_rep(), kind="ADAPTED")["spec"]
    differ = [k for k in PR.FIELDS if f.params[k] != a.params[k]]
    assert set(differ) == {"inventory_price_skew", "withdrawal_policy",
                           "minimum_rest_time", "max_residual"}
    for k in differ:
        assert a.params[k].status == PR.OURS_ADAPTED
    # every field the evidence DID establish is carried across unchanged
    for k in PR.FIELDS:
        if f.params[k].status == PR.OBSERVED_PROSPECTIVE:
            assert a.params[k] == f.params[k], k
    assert a.l5_evidence_fields() == f.l5_evidence_fields()


def test_the_first_adapted_shadow_uses_the_declared_conservative_policies():
    a = SP.build(_rep(), kind="ADAPTED")["spec"]
    assert a.get("inventory_price_skew") == {"widening_ticks": 0,
                                             "reducing_ticks": 0}
    assert a.get("withdrawal_policy")["mode"] == "WITHDRAW_ON_TTE_FLOOR"
    # and they are NOT derived from the report -- the same policies come back
    # from a report with completely different numbers
    other = _rep()
    other["pooled"]["reversals"]["share_already_present"] = 0.05
    other["windows"][0]["qty"]["classes"]["EXACT_100"] = 1
    b = SP.build(other, kind="ADAPTED")["spec"]
    assert b.params["inventory_price_skew"] == a.params["inventory_price_skew"]
    assert b.params["withdrawal_policy"] == a.params["withdrawal_policy"]


# ------------------------------------------------------- the frozen artifacts

def test_the_frozen_specs_load_and_still_hash_to_what_was_frozen():
    sets = PR.load_frozen()
    assert set(sets) >= {"FIDELITY_OBSERVABLE_V1", "L5_ADAPTED_V1"}
    for label, sp in sets.items():
        blob = json.load(io.open("artifacts/l5_surface/%s.json" % label))
        assert sp.spec_sha256() == blob["spec_sha256"], label


def test_an_edited_frozen_spec_is_refused_rather_than_loaded():
    """A spec whose stored hash no longer matches has been edited since
    review, and loading it would silently run something else."""
    blob = json.load(io.open("artifacts/l5_surface/L5_ADAPTED_V1.json"))
    blob["params"]["max_residual"]["value"] = "999"
    with pytest.raises(ValueError) as e:
        PR.from_json(blob)
    assert "edited since it was frozen" in str(e.value)


def test_the_frozen_adapted_spec_is_executable_and_passive():
    from src.l5_surface import engine as E
    from src.l5_surface import shadow as S
    a = PR.load_frozen()["L5_ADAPTED_V1"]
    assert a.unresolved() == []
    st = {"cohort": "BTC15m", "market_key": "m", "state_id": "s",
          "t_utc": 1.0, "tte_s": 600.0,
          "outcomes": {"Up": {"bid": 0.50, "ask": 0.51, "tick": 0.01},
                       "Down": {"bid": 0.49, "ask": 0.50, "tick": 0.01}},
          "inventory": {}}
    r = E.desired_surface(st, a)
    assert r["emitted"] is True
    terms = {"tick": "0.01", "order_min_size": 5}
    books = {"Up": {"bids": [{"price": "0.50", "size": "9"}],
                    "asks": [{"price": "0.51", "size": "9"}]},
             "Down": {"bids": [{"price": "0.49", "size": "9"}],
                      "asks": [{"price": "0.50", "size": "9"}]}}
    for rung in r["surface"].rungs:
        chk = S.restable(float(rung.max_acceptable_price),
                         float(rung.desired_qty), terms,
                         books[rung.outcome])
        assert chk["restable"] is True, (rung.outcome, chk)


def test_the_frozen_fidelity_spec_records_the_boundary_not_a_failure():
    f = PR.load_frozen()["FIDELITY_OBSERVABLE_V1"]
    assert f.l5_evidence_fields() == [
        "rung_count", "tick_offsets", "secondary_rung_qty",
        "both_sides_policy", "refresh_policy", "TTE_policy"]
    assert f.kind == "FIDELITY"
    for k in PR.FIELDS:
        assert f.params[k].status != PR.OURS_ADAPTED


def test_the_freeze_records_that_the_mechanism_was_unresolved():
    """A global UNRESOLVED must not be quietly dropped from the provenance of
    fields frozen underneath it."""
    for label in ("FIDELITY_OBSERVABLE_V1", "L5_ADAPTED_V1"):
        blob = json.load(io.open("artifacts/l5_surface/%s.json" % label))
        fr = blob["frozen_from"]
        assert fr["mechanism_verdict"] == "UNRESOLVED"
        assert fr["study"] == "L5_PASSIVE_SURFACE_PROSPECTIVE_V1"
        assert len(fr["eligible_windows"]) == 3
        assert fr["chain_proven_parents"] == 208
        assert "does not erase them" in fr["labels"]
