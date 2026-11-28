"""Two lanes off one observable set, and no lane may launder a transfer.

The point of running PMUS sports and Gemini crypto in parallel is that the
transfer of a Polymarket BTC15m finding to another venue can FAIL. That is
only learnable if a transferred field never becomes indistinguishable from a
field this lane measured itself -- so these tests hold the line at the
boundary rather than at the numbers.
"""
import pytest

from src.l5_surface import lanes as LN, params as PR


def _fidelity():
    return PR.load_frozen()["FIDELITY_OBSERVABLE_V1"]


def _both():
    f = _fidelity()
    return f, (LN.derive(f, LN.PMUS_SPORTS), LN.derive(f, LN.GEMINI_CRYPTO))


def test_deriving_lanes_does_not_touch_the_observable_set():
    before = _fidelity().spec_sha256()
    f, _ = _both()
    assert f.spec_sha256() == before
    assert _fidelity().spec_sha256() == before


def test_every_inherited_field_is_transferred_and_names_its_source():
    f, both = _both()
    src_sha = f.spec_sha256()[:16]
    for sp in both:
        assert sp.transferred(), "%s inherited nothing at all" % sp.label
        for field in sp.transferred():
            p = sp.params[field]
            assert p.transferred_from, "%s/%s is silent about its source" % (
                sp.label, field)
            assert src_sha in p.transferred_from
            assert f.params[field].status == PR.OBSERVED_PROSPECTIVE
            assert p.value == f.params[field].value


def test_a_transferred_field_is_never_evidence_about_this_lane():
    _, both = _both()
    for sp in both:
        assert sp.l5_evidence_fields() == []
        assert LN.report_transfer(sp)["observed_here"] == []
        for field in sp.transferred():
            assert sp.params[field].is_l5_evidence() is False


def test_instrument_specific_fields_are_never_transferred():
    f, both = _both()
    for field in LN.INSTRUMENT_SPECIFIC:
        for sp in both:
            p = sp.params[field]
            assert p.status != PR.TRANSFERRED
            assert p.transferred_from is None
    # and they were genuinely available to transfer -- the test would pass
    # vacuously if the observable set had never resolved them
    assert all(f.params[x].status == PR.OBSERVED_PROSPECTIVE
               for x in LN.INSTRUMENT_SPECIFIC)


def test_a_lane_that_declines_an_instrument_specific_field_stays_unresolved():
    f = _fidelity()
    ours = dict(LN.OURS[LN.GEMINI_CRYPTO])
    del ours["TTE_policy"]
    sp = LN.derive(f, LN.GEMINI_CRYPTO, ours)
    assert "TTE_policy" in sp.unresolved()
    with pytest.raises(PR.Unresolved):
        sp.get("TTE_policy")


def test_our_own_choices_are_ours_adapted_not_observations():
    _, both = _both()
    for sp in both:
        ours = LN.report_transfer(sp)["ours"]
        assert "max_residual" in ours and "withdrawal_policy" in ours
        for field in ours:
            assert sp.params[field].is_l5_evidence() is False


def test_the_two_lanes_are_separate_specs():
    _, (pmus, gem) = _both()
    assert pmus.label != gem.label
    assert pmus.spec_sha256() != gem.spec_sha256()


def test_an_unknown_lane_is_refused():
    with pytest.raises(ValueError):
        LN.derive(_fidelity(), "L5_ADAPTED_SOMEWHERE_ELSE")


def test_the_frozen_lane_artifacts_match_what_derive_produces():
    """The files on disk are not hand-written; drift between code and
    artifact would otherwise be invisible until a shadow read the wrong one."""
    sets = PR.load_frozen()
    _, (pmus, gem) = _both()
    for built in (pmus, gem):
        assert built.label in sets, "%s was never frozen" % built.label
        assert sets[built.label].spec_sha256() == built.spec_sha256()


def _state(tte):
    return {"cohort": "t", "market_key": "m", "state_id": "s", "t_utc": 0.0,
            "tte_s": tte, "inventory": {},
            "outcomes": {"Up": {"bid": 0.55, "ask": 0.56, "tick": 0.01},
                         "Down": {"bid": 0.44, "ask": 0.45, "tick": 0.01}}}


def test_each_lane_quotes_only_inside_its_own_tte_band():
    """A lane withholding its surface is the TTE band doing its job, not a
    defect -- and a 600s state is outside a 300s BTC5m window by construction."""
    from src.l5_surface import engine as EN
    sets = PR.load_frozen()

    far = EN.desired_surface(_state(600.0), sets[LN.GEMINI_CRYPTO])
    assert far["emitted"] is False
    assert far["blocked_by"] == [], "withholding is not an unresolved field"
    assert "TTE_policy" in far["reason"]

    near = EN.desired_surface(_state(240.0), sets[LN.GEMINI_CRYPTO])
    assert near["emitted"] is True and len(near["surface"].rungs) == 2

    # the same 600s state is well inside a sports market's band
    sport = EN.desired_surface(_state(600.0), sets[LN.PMUS_SPORTS])
    assert sport["emitted"] is True and len(sport["surface"].rungs) == 2


def test_no_lane_rung_crosses_the_spread():
    """tick_offsets are measured from the BID; a lane that read them as
    offsets from the ask would place rung 0 on the ask as a taker."""
    from src.l5_surface import engine as EN
    sets = PR.load_frozen()
    for lane, tte in ((LN.PMUS_SPORTS, 600.0), (LN.GEMINI_CRYPTO, 240.0)):
        r = EN.desired_surface(_state(tte), sets[lane])
        for g in r["surface"].rungs:
            bid = 0.55 if g.outcome == "Up" else 0.44
            assert float(g.max_acceptable_price) <= bid, (
                "%s %s rung would cross" % (lane, g.outcome))


# --------------------------------------------- the hashes, pinned in source

#: The identities that downstream evidence is labelled with. Pinned here so a
#: schema change cannot quietly re-hash a spec that was already frozen and
#: reviewed -- which is exactly what adding `transferred_from` nearly did.
FROZEN_SHA = {
    "FIDELITY_OBSERVABLE_V1":
        "d583fa4005d1b815223f40235d368a347208b942633603aa31d90f6dde58cb95",
    "L5_ADAPTED_V1":
        "fe9d6557af80df02f40ac3a691650aa77da65f3b6505d818803ab54b8764d777",
    "L5_ADAPTED_PMUS_SPORTS":
        "2237304d81ac63f51ea2adaf43849f6220fca7578c4b4a18ba68dc112877915c",
    "L5_ADAPTED_GEMINI_CRYPTO":
        "62d004d80b4af8b97823e239d8919ee3abff5420c0318d18623c2810bdb3c2a6",
    "L5_ADAPTED_GEMINI_BTC15M_V2":
        "cd8ad6a914dc9fc0a27a82de3159c990a2d3a380b6186cb95e5f87aa36defe7a",
}


def test_the_frozen_identities_are_stable():
    sets = PR.load_frozen()
    for label, sha in FROZEN_SHA.items():
        assert sets[label].spec_sha256() == sha, (
            "%s re-hashed; downstream evidence cites the old value" % label)


# ----------------------------------------- the BTC15m timing correction

def test_THE_CORRECTION_CHANGES_EXACTLY_ONE_FIELD():
    """"Only timing changes" has to be a property of the artifact.

    Side selection, the price surface, the inventory mechanics and every
    bound are the control lane's. If a second field ever moves, the
    prospective comparison stops being about timing and nothing it produces
    answers the question it was built for.
    """
    sets = PR.load_frozen()
    base = sets[LN.GEMINI_CRYPTO]
    corr = LN.derive_correction(sets["FIDELITY_OBSERVABLE_V1"],
                                LN.GEMINI_BTC15M_V2)
    moved = [f for f in PR.FIELDS
             if (base.params[f].status, base.params[f].value)
             != (corr.params[f].status, corr.params[f].value)]
    assert moved == ["TTE_policy"], moved
    assert corr.get("TTE_policy") == {"quote_between_tte_s": [0, 900]}
    assert base.get("TTE_policy") == {"quote_between_tte_s": [0, 300]}


def test_THE_CONTROL_IS_PRESERVED_NOT_MUTATED():
    """Deriving the correction must leave the lane it corrects alone."""
    sets = PR.load_frozen()
    before = sets[LN.GEMINI_CRYPTO].spec_sha256()
    LN.derive_correction(sets["FIDELITY_OBSERVABLE_V1"], LN.GEMINI_BTC15M_V2)
    after = PR.load_frozen()[LN.GEMINI_CRYPTO].spec_sha256()
    assert before == after == FROZEN_SHA["L5_ADAPTED_GEMINI_CRYPTO"]


def test_A_CORRECTION_IS_NOT_A_LANE_AND_GETS_NO_LIVE_GATE():
    """lane_gates.SOURCES is keyed by LANES.

    A correction that joined LANES would acquire a live-progression gate by
    existing rather than by earning one, which is the exact substitution this
    codebase refuses everywhere else.
    """
    from src.l5_surface import lane_gates as G
    assert LN.GEMINI_BTC15M_V2 not in LN.LANES
    assert LN.GEMINI_BTC15M_V2 not in G.SOURCES
    assert set(G.SOURCES) >= set(LN.LANES)


def test_THE_CORRECTED_FIELD_STATES_ITS_PROVENANCE_AND_IS_NOT_CALLED_OBSERVED():
    sets = PR.load_frozen()
    corr = LN.derive_correction(sets["FIDELITY_OBSERVABLE_V1"],
                                LN.GEMINI_BTC15M_V2)
    p = corr.params["TTE_policy"]
    assert p.status == PR.OURS_ADAPTED
    assert not p.is_l5_evidence()
    assert corr.l5_evidence_fields() == []
    assert p.evidence is None
    why = p.prior_evidence
    for token in ("CORRECTION", "OURS_ADAPTED, never measured from L5",
                  "TTE_PROVENANCE_1.md", "NOT CHANGED", "SHADOW ONLY"):
        assert token in why, token
    # the numbers the correction rests on are IN the artifact, not only in a
    # commit message that nothing downstream reads
    for token in ("63,569", "89.5%", "p=0.0037", "38 of the 96"):
        assert token in why, token


def test_FREEZING_WRITES_THE_CORRECTION_AND_STILL_NEVER_TOUCHES_FIDELITY():
    import tempfile
    import shutil
    import os
    with tempfile.TemporaryDirectory() as d:
        for f in os.listdir(PR.FROZEN_DIR):
            shutil.copy(os.path.join(PR.FROZEN_DIR, f), d)
        out = LN.freeze(d)
        assert out[LN.GEMINI_BTC15M_V2] == \
            FROZEN_SHA["L5_ADAPTED_GEMINI_BTC15M_V2"]
        assert out["FIDELITY_OBSERVABLE_V1"] == \
            FROZEN_SHA["FIDELITY_OBSERVABLE_V1"]
        assert PR.load_frozen(d)[LN.GEMINI_BTC15M_V2].get("TTE_policy") == \
            {"quote_between_tte_s": [0, 900]}
